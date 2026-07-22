"""Commands for bootstrapping and managing local users."""

import asyncio
import re
from typing import Annotated

import typer
from sqlalchemy import select

from oncall.audit.service import append_audit_event
from oncall.auth.passwords import hash_password
from oncall.database import async_session
from oncall.models import User


app = typer.Typer(help="管理本地 OnCall 用户。")
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


async def _create_admin(username: str, password: str) -> None:
    async with async_session() as session:
        existing = await session.execute(select(User).where(User.username == username))
        if existing.scalar_one_or_none() is not None:
            raise typer.BadParameter("用户名已存在", param_hint="--username")
        user = User(username=username, password_hash=hash_password(password), role="admin", is_active=True)
        session.add(user)
        await session.flush()
        await append_audit_event(
            session,
            None,
            "admin.user_created",
            {"user_id": str(user.id), "username": user.username, "role": "admin", "source": "cli"},
            actor=f"cli:{username}",
        )
        await session.commit()


@app.command("create-admin")
def create_admin(
    username: Annotated[
        str,
        typer.Option("--username", help="管理员用户名。"),
    ],
) -> None:
    """Create the first local administrator; password input is never a CLI argument."""

    if not 1 <= len(username) <= 128 or _USERNAME_PATTERN.fullmatch(username) is None:
        raise typer.BadParameter(
            "用户名须为1至128位，仅允许字母、数字、点、下划线和连字符，且首位为字母或数字",
            param_hint="--username",
        )
    password = typer.prompt("密码", hide_input=True, confirmation_prompt=True)
    if len(password) < 12:
        raise typer.BadParameter("密码至少需要 12 个字符")
    asyncio.run(_create_admin(username, password))
    typer.echo(f"已创建管理员 {username}")
