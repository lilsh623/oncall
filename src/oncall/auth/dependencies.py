"""FastAPI dependencies that enforce signed identity and RBAC."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.auth.schemas import UserRole
from oncall.auth.tokens import InvalidAccessToken, decode_access_token
from oncall.database import async_session
from oncall.models import User


_bearer_scheme = HTTPBearer(auto_error=False)
def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="认证凭据无效或已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield the request-scoped database session used by auth routes."""

    async with async_session() as session:
        yield session


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """Load an active user after verifying their bearer JWT."""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = UUID(str(claims["sub"]))
    except (InvalidAccessToken, KeyError, ValueError):
        raise _unauthorized()

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    if user.role not in {"viewer", "operator", "approver", "admin"}:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[..., Awaitable[User]]:
    """Require one of the supplied roles after checking database user status."""

    allowed_roles = set(roles)

    async def role_dependency(current_user: CurrentUser) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前用户没有执行此操作的权限",
            )
        return current_user

    return role_dependency
