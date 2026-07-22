"""Public local authentication endpoints."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.audit.service import append_audit_event
from oncall.auth.dependencies import CurrentUser, get_db_session
from oncall.auth.passwords import verify_password
from oncall.auth.schemas import LoginRequest, LoginResponse, UserSummary
from oncall.auth.tokens import (
    ACCESS_TOKEN_LIFETIME,
    REFRESH_COOKIE_NAME,
    REFRESH_TOKEN_LIFETIME,
    InvalidRefreshToken,
    create_access_token,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)
from oncall.config import get_settings
from oncall.models import User


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=int(REFRESH_TOKEN_LIFETIME.total_seconds()),
        httponly=True,
        secure=get_settings().app_env == "production",
        samesite="lax",
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/api/v1/auth")


def _login_response(user: User) -> LoginResponse:
    return LoginResponse(
        access_token=create_access_token(user.id, user.role),
        expires_in=int(ACCESS_TOKEN_LIFETIME.total_seconds()),
        user=UserSummary.model_validate(user),
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LoginResponse:
    """Authenticate a local user and set a rotating refresh-token cookie."""

    result = await session.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    refresh_token = await issue_refresh_token(session, user)
    await append_audit_event(
        session, None, "auth.login", {"user_id": str(user.id)}, actor=user.username
    )
    await session.commit()
    _set_refresh_cookie(response, refresh_token)
    return _login_response(user)


@router.post("/refresh", response_model=LoginResponse)
async def refresh(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
) -> LoginResponse | JSONResponse:
    """Rotate a valid refresh token and return a newly signed access JWT."""

    if not refresh_token:
        expired_response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "刷新令牌无效或已过期"},
        )
        _clear_refresh_cookie(expired_response)
        return expired_response
    try:
        user, replacement = await rotate_refresh_token(session, refresh_token)
    except InvalidRefreshToken:
        await session.rollback()
        expired_response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "刷新令牌无效或已过期"},
        )
        _clear_refresh_cookie(expired_response)
        return expired_response

    await append_audit_event(
        session, None, "auth.refresh", {"user_id": str(user.id)}, actor=user.username
    )
    await session.commit()
    _set_refresh_cookie(response, replacement)
    return _login_response(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)] = None,
) -> Response:
    """Revoke the current refresh token when present and always clear its cookie."""

    if refresh_token:
        revoked = await revoke_refresh_token(session, refresh_token)
        if revoked:
            await append_audit_event(session, None, "auth.logout", {}, actor="authenticated-user")
        await session.commit()
    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserSummary)
async def me(current_user: CurrentUser) -> User:
    """Return the active identity attached to the bearer token."""

    return current_user
