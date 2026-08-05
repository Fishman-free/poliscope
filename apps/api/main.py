"""ASGI application for the Poliscope API.

Run it with::

    uvicorn apps.api.main:app --reload

The database engine is created on startup and disposed on shutdown, so a reload
does not leak connection pools.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from apps.api.dependencies import AppState
from apps.api.routers import (
    knowledge_bases,
    papers,
    reports,
    stream,
    tasks,
    workspace,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = AppState.from_env()
    app.state.poliscope = state
    try:
        yield
    finally:
        await state.dispose()


app = FastAPI(title="Poliscope API", lifespan=lifespan)

app.include_router(
    knowledge_bases.router,
    prefix="/api/knowledge-bases",
    tags=["knowledge-bases"],
)
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(papers.router, prefix="/api/tasks", tags=["papers"])
app.include_router(workspace.router, prefix="/api/workspace", tags=["workspace"])
app.include_router(stream.router, prefix="/api/stream", tags=["stream"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Report whether the API can actually reach its database.

    A check that only proves the process is running would report healthy while
    every request fails, so this issues a real query.
    """
    state: AppState = app.state.poliscope
    async with state.session_factory() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
