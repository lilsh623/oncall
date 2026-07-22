"""Admin-only local user management API."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from oncall.audit.service import append_audit_event
from oncall.auth.dependencies import get_db_session, require_roles
from oncall.auth.passwords import hash_password
from oncall.auth.schemas import UserCreateRequest, UserResponse, UserUpdateRequest
from oncall.models import RefreshToken, User


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
AdminUser = Annotated[User, Depends(require_roles("admin"))]
DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
_ADMIN_INVARIANT_LOCK_ID = 0x4F4E43414C4C


@router.get("/users", response_model=list[UserResponse])
async def list_users(_: AdminUser, session: DatabaseSession) -> list[User]:
    """List local users without exposing password hashes."""

    result = await session.execute(select(User).order_by(User.username))
    return list(result.scalars())


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreateRequest, actor: AdminUser, session: DatabaseSession) -> User:
    """Create a local user using an Argon2id password hash."""

    user = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    await append_audit_event(
        session,
        None,
        "admin.user_created",
        {"user_id": str(user.id), "username": user.username, "role": user.role},
        actor=actor.username,
    )
    await session.commit()
    await session.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserUpdateRequest,
    actor: AdminUser,
    session: DatabaseSession,
) -> User:
    """Change a user's role or active state while preserving an active administrator."""

    await session.execute(select(func.pg_advisory_xact_lock(_ADMIN_INVARIANT_LOCK_ID)))
    user = await session.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    removes_active_admin = (
        user.role == "admin"
        and user.is_active
        and ((payload.role is not None and payload.role != "admin") or payload.is_active is False)
    )
    if removes_active_admin:
        active_admins = await session.execute(
            select(User.id)
            .where(User.role == "admin", User.is_active.is_(True))
            .with_for_update()
        )
        if len(active_admins.scalars().all()) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="不能禁用或降级当前唯一的启用状态 admin",
            )

    before = {"role": user.role, "is_active": user.is_active}
    if payload.is_active is False:
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    await session.flush()
    await append_audit_event(
        session,
        None,
        "admin.user_updated",
        {"user_id": str(user.id), "before": before, "after": {"role": user.role, "is_active": user.is_active}},
        actor=actor.username,
    )
    await session.commit()
    await session.refresh(user)
    return user
