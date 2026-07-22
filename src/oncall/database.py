"""SQLAlchemy async engine and session lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from oncall.config import get_settings


class Base(DeclarativeBase):
    """Base class shared by all persisted runtime entities."""


@lru_cache
def get_engine() -> AsyncEngine:
    """Create one async engine per application process."""

    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory."""

    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def async_session() -> AsyncIterator[AsyncSession]:
    """Yield a transaction-ready session and always release its connection."""

    async with get_session_factory()() as session:
        yield session
