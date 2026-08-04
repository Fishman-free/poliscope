"""Application wiring for the API process.

Engines are created once per process and shared, because creating one per
request would open a new connection pool on every call. Sessions are created per
request and always closed, so a handler cannot leak a transaction into the next
one.

The API only ever holds the application identity. Reaching the evidence graph
with write intent requires the projector identity, which lives in the worker
process; that separation is what makes the privilege model in revision 0003
observable rather than notional.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from packages.kernel.config import DatabaseConfig
from packages.kernel.database import create_database_engine, create_session_factory
from packages.papers.object_store import PrivateObjectStore


class AppState:
    """Process wide resources created at startup and disposed at shutdown."""

    def __init__(
        self, app_url: str, object_store: PrivateObjectStore | None = None
    ) -> None:
        self.engine: AsyncEngine = create_database_engine(app_url)
        self.session_factory: async_sessionmaker[AsyncSession] = (
            create_session_factory(self.engine)
        )
        self.object_store: PrivateObjectStore = (
            object_store or PrivateObjectStore.from_env()
        )

    @classmethod
    def from_env(cls) -> AppState:
        return cls(DatabaseConfig.app_url_from_env())

    async def dispose(self) -> None:
        await self.engine.dispose()


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.poliscope
    return state


async def get_session(
    state: Annotated[AppState, Depends(get_state)],
) -> AsyncIterator[AsyncSession]:
    """Yield a session that commits on success and rolls back on failure.

    Committing here rather than inside each handler keeps a partially applied
    request from being persisted when a later validation fails.
    """
    async with state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


def get_object_store(
    state: Annotated[AppState, Depends(get_state)],
) -> PrivateObjectStore:
    return state.object_store


SessionDep = Annotated[AsyncSession, Depends(get_session)]
StateDep = Annotated[AppState, Depends(get_state)]
ObjectStoreDep = Annotated[PrivateObjectStore, Depends(get_object_store)]
