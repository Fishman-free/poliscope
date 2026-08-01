from __future__ import annotations

from fastapi import FastAPI

from apps.api.routers import stream, tasks, workspace

app = FastAPI(title="Poliscope API")

app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(workspace.router, prefix="/api/workspace", tags=["workspace"])
app.include_router(stream.router, prefix="/api/stream", tags=["stream"])
