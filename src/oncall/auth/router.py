"""Public local authentication endpoints."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.audit.service import append_audit_event
from oncall.auth.dependencies import CurrentUser, get_db_session
from oncall.auth.passwords import hash_password, verify_password
from oncall.auth.schemas import LoginRequest, LoginResponse, UserSummary
from oncall.auth.tokens import (
    ACCESS_TOKEN_LIFETIME,
    CSRF_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    REFRESH_TOKEN_LIFETIME,
    InvalidRefreshToken,
    RefreshTokenReuseDetected,
    create_access_token,
    create_csrf_token_value,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)
from oncall.config import get_settings
from oncall.models import User


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-oncall-user-password")


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    secure = get_settings().app_env == "production"
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=int(REFRESH_TOKEN_LIFETIME.total_seconds()),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api/v1/auth",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=create_csrf_token_value(),
        max_age=int(REFRESH_TOKEN_LIFETIME.total_seconds()),
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/api/v1/auth")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/")


def _require_csrf(csrf_cookie: str | None, csrf_header: str | None) -> None:
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")


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

    result = await session.execute(
        select(User).where(User.username == payload.username).with_for_update()
    )
    user = result.scalar_one_or_none()
    password_hash = user.password_hash if user is not None and user.is_active else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(payload.password, password_hash)
    if user is None or not user.is_active or not password_valid:
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
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE_NAME)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> LoginResponse | JSONResponse:
    """Rotate a valid refresh token and return a newly signed access JWT."""

    if not refresh_token:
        expired_response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "刷新令牌无效或已过期"},
        )
        _clear_refresh_cookie(expired_response)
        return expired_response
    _require_csrf(csrf_cookie, csrf_header)
    try:
        user, replacement = await rotate_refresh_token(session, refresh_token)
    except RefreshTokenReuseDetected:
        await session.commit()
        expired_response = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "检测到刷新令牌重放，会话已撤销"},
        )
        _clear_refresh_cookie(expired_response)
        return expired_response
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
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE_NAME)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    """Revoke the current refresh token when present and always clear its cookie."""

    if refresh_token:
        _require_csrf(csrf_cookie, csrf_header)
        user = await revoke_refresh_token(session, refresh_token)
        if user is not None:
            await append_audit_event(session, None, "auth.logout", {}, actor=user.username)
        await session.commit()
    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserSummary)
async def me(current_user: CurrentUser) -> User:
    """Return the active identity attached to the bearer token."""

    return current_user
