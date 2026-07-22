"""JWT access tokens and database-backed rotating refresh tokens."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError as PyJWTInvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.config import get_settings
from oncall.models import RefreshToken, User


JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_LIFETIME = timedelta(minutes=15)
REFRESH_TOKEN_LIFETIME = timedelta(days=7)
REFRESH_COOKIE_NAME = "refresh_token"
CSRF_COOKIE_NAME = "csrf_token"


class InvalidAccessToken(Exception):
    """Raised when a bearer token cannot be trusted."""


class InvalidRefreshToken(Exception):
    """Raised when a refresh token is expired, revoked, or unknown."""


class RefreshTokenReuseDetected(InvalidRefreshToken):
    """Raised after revoking a family whose rotated token was replayed."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_access_token(subject: UUID | str, role: str) -> str:
    """Create a 15-minute signed access JWT for an active user."""

    issued_at = utc_now()
    payload = {
        "sub": str(subject),
        "role": role,
        "iat": issued_at,
        "exp": issued_at + ACCESS_TOKEN_LIFETIME,
        "jti": str(uuid4()),
    }
    return jwt.encode(
        payload,
        get_settings().jwt_secret.get_secret_value(),
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict[str, object]:
    """Decode a JWT and require the claims used by authorization."""

    try:
        return jwt.decode(
            token,
            get_settings().jwt_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "role", "iat", "exp", "jti"]},
        )
    except PyJWTInvalidTokenError as exc:
        raise InvalidAccessToken from exc


def create_refresh_token_value() -> str:
    """Create an opaque token sourced from exactly 32 random bytes."""

    return secrets.token_urlsafe(32)


def create_csrf_token_value() -> str:
    """Create the public half of the double-submit CSRF protection."""

    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """Return the only refresh-token representation stored in PostgreSQL."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_refresh_token(
    session: AsyncSession, user: User, *, family_id: UUID | None = None
) -> str:
    """Stage a seven-day opaque refresh token for a user in the current transaction."""

    locked_user = await session.execute(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current_user = locked_user.scalar_one_or_none()
    if current_user is None or not current_user.is_active:
        raise InvalidRefreshToken

    raw_token = create_refresh_token_value()
    session.add(
        RefreshToken(
            user_id=user.id,
            family_id=family_id or uuid4(),
            token_hash=hash_refresh_token(raw_token),
            expires_at=utc_now() + REFRESH_TOKEN_LIFETIME,
        )
    )
    await session.flush()
    return raw_token


async def _lock_token_and_user(
    session: AsyncSession, raw_token: str
) -> tuple[RefreshToken, User] | None:
    """Lock the stable User mutex before locking any generation of its token."""

    token_hash = hash_refresh_token(raw_token)
    user_id = await session.scalar(
        select(RefreshToken.user_id).where(RefreshToken.token_hash == token_hash)
    )
    if user_id is None:
        return None

    user = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None:
        return None
    stored_token = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .with_for_update()
    )
    if stored_token is None:
        return None
    return stored_token, user


async def rotate_refresh_token(session: AsyncSession, raw_token: str) -> tuple[User, str]:
    """Revoke a valid refresh token and issue its one-time replacement atomically."""

    record = await _lock_token_and_user(session, raw_token)
    if record is None:
        raise InvalidRefreshToken

    stored_token, user = record
    if stored_token.revoked_at is not None:
        await session.execute(
            RefreshToken.__table__.update()
            .where(
                RefreshToken.family_id == stored_token.family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now())
        )
        await session.flush()
        raise RefreshTokenReuseDetected
    if stored_token.expires_at <= utc_now() or not user.is_active:
        raise InvalidRefreshToken

    stored_token.revoked_at = utc_now()
    replacement = await issue_refresh_token(
        session, user, family_id=stored_token.family_id
    )
    return user, replacement


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> User | None:
    """Revoke a refresh token if it is still active; logout remains idempotent."""

    record = await _lock_token_and_user(session, raw_token)
    if record is None:
        return None
    stored_token, user = record
    await session.execute(
        RefreshToken.__table__.update()
        .where(
            RefreshToken.family_id == stored_token.family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=utc_now())
    )
    await session.flush()
    return user
