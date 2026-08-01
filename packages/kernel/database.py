from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Single declarative base for all Poliscope ORM models."""


def canonical_uuid(value: UUID) -> UUID:
    """Return a plain :class:`uuid.UUID` for an identifier read from the database.

    asyncpg returns ``asyncpg.pgproto.pgproto.UUID``, a subclass. It compares and
    hashes identically, so it is invisible almost everywhere -- but
    :class:`packages.kernel.contracts.ContractModel` admits a leaf only when its
    type matches exactly, on purpose, because a subclass of a scalar can carry
    mutable state. Normalising here keeps that guarantee intact rather than
    weakening it for every type just to accommodate one driver.
    """
    return value if type(value) is UUID else UUID(str(value))


def create_database_engine(url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine without performing schema changes."""
    return create_async_engine(url, pool_pre_ping=True)


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create the shared async session factory."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Commit a successful unit of work and roll back failures."""
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
