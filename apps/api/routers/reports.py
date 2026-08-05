"""Serves the Research Brief in the two formats the product needs.

Markdown is what a researcher reads and exports; JSON is what the interface
renders. Both come from one :class:`ReportService` build, so the two can never
disagree about what the council concluded.

Both go through ``sanitize_export`` inside their renderers. CLAUDE.md 16 requires
uploaded material not to leak through exports, and a signed URL or a local path
in a brief is exactly that leak.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUserDep, SessionDep
from packages.accounts.repository import StoredUser
from packages.reports.json_export import to_dict
from packages.reports.markdown import render_markdown
from packages.reports.service import ReportService, ResearchBrief
from packages.research.repository import ResearchRepository, TaskNotFound

router = APIRouter()


async def build_brief(
    task_id: UUID, session: AsyncSession, user: StoredUser
) -> ResearchBrief:
    """Build the brief, answering 404 rather than 500 for an unknown task."""
    try:
        await ResearchRepository(session).get_task(task_id, user.id)
        return await ReportService(session).build(task_id)
    except TaskNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown task {task_id}",
        ) from error


# response_model=None because the handler returns either a JSON body or a
# PlainTextResponse, and FastAPI cannot build one schema for both.
@router.get("/{task_id}", response_model=None)
async def get_report(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    format: str = Query("json", pattern="^(json|markdown)$"),
) -> Response | dict[str, object]:
    """Return the brief. ``format=markdown`` returns text, otherwise JSON."""
    brief = await build_brief(task_id, session, current_user)
    if format == "markdown":
        return PlainTextResponse(
            render_markdown(brief),
            media_type="text/markdown; charset=utf-8",
        )
    return to_dict(brief)
