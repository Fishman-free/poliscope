from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from uuid import UUID

from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    OperationalError,
    PendingRollbackError,
)
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
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


_SESSION_IO_LOCK_KEY = "poliscope.session_io_lock"


class DatabaseSessionLost(Exception):
    """A shared session's connection is gone; only a fresh session continues.

    Raised by writers that run deep inside a council round (the audit log) so
    the round stops at once instead of every remaining seat paying for a
    model call whose result can no longer be recorded. The worker resumes the
    run from its last committed checkpoint in a new session.
    """


def is_connection_failure(error: BaseException) -> bool:
    """Whether ``error``, or what caused it, is a broken database connection.

    Only connection-level failures qualify: an invalidated connection, the
    ``PendingRollbackError`` every later statement raises after one, a pool
    checkout timeout. A constraint violation or a bad statement would fail
    the same way on a fresh connection, so it does not -- including the
    ``PendingRollbackError`` that merely echoes such a flush failure, told
    apart by the original error SQLAlchemy quotes in its message.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (DatabaseSessionLost, PoolTimeoutError)):
            return True
        if isinstance(current, PendingRollbackError):
            text = str(current).lower()
            if "invalid transaction" in text or "connection" in text:
                return True
        if isinstance(current, DBAPIError) and (
            current.connection_invalidated
            or isinstance(current, (OperationalError, InterfaceError))
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def session_io_lock(session: AsyncSession) -> asyncio.Lock:
    """The one lock serialising guarded writes on a shared session.

    An ``AsyncSession`` is not safe for concurrent use: two coroutines
    flushing or committing it at once leave its connection mid-statement.
    The lock lives in ``session.info`` so every writer holding the same
    session finds the same lock without threading it through call sites.
    """
    lock = session.info.get(_SESSION_IO_LOCK_KEY)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        session.info[_SESSION_IO_LOCK_KEY] = lock
    return lock


async def run_to_completion[T](operation: Awaitable[T]) -> T:
    """Finish a database operation even when the caller is cancelled meanwhile.

    A cancellation that lands while asyncpg is mid-statement makes
    SQLAlchemy treat the connection as dead and invalidate it; from then on
    every statement on that session raises ``PendingRollbackError`` ("Can't
    reconnect until invalid transaction is rolled back"). That is how one
    seat's per-attempt timeout used to take the whole council down. The
    operation is shielded, and a cancellation waits for it to finish before
    propagating, so the session is never left half way through a statement.
    """
    task = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            # A second cancellation (an outer timeout firing too) must not
            # abandon the write either; the first one is re-raised below.
            with suppress(asyncio.CancelledError):
                await asyncio.wait((task,))
        if not task.cancelled():
            # Retrieve the outcome so a failed write is not reported as
            # "exception never retrieved"; the cancellation still wins.
            task.exception()
        raise


async def guarded_write[T](
    session: AsyncSession,
    write: Callable[[], Awaitable[T]],
) -> T:
    """Run ``write`` under the session's lock, immune to cancellation."""

    async def locked() -> T:
        async with session_io_lock(session):
            return await write()

    return await run_to_completion(locked())


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
