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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import CurrentUserDep, SessionDep
from packages.accounts.repository import StoredUser
from packages.evidence.models import ScientificEventModel
from packages.reports.json_export import to_dict
from packages.reports.markdown import render_markdown
from packages.reports.paper_markdown import render_paper_markdown
from packages.reports.service import ReportService, ResearchBrief
from packages.reports.synthesis import (
    FINAL_PAPER_DRAFTED,
    FINAL_PAPER_FAILED,
    paper_payload_to_dataclass,
)
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


async def _paper_state(
    session: AsyncSession, task_id: UUID
) -> tuple[dict[str, object] | None, str | None]:
    """The latest paper event's payload and the failure reason, if any.

    Returns ``(paper, reason)``: ``paper`` is the FINAL_PAPER_DRAFTED payload
    (or None), ``reason`` the FINAL_PAPER_FAILED reason (or None). The last
    event wins, so a task that failed synthesis and later succeeded reports
    the paper, and vice versa.
    """
    result = await session.execute(
        select(ScientificEventModel)
        .where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.event_type.in_(
                (FINAL_PAPER_DRAFTED, FINAL_PAPER_FAILED)
            ),
        )
        .order_by(ScientificEventModel.sequence.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None, None
    payload = dict(row.payload)
    if row.event_type == FINAL_PAPER_FAILED:
        return None, str(payload.get("reason", "unknown synthesis failure"))
    return payload, None


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


@router.get("/{task_id}/paper", response_model=None)
async def get_paper(
    task_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
    format: str = Query("json", pattern="^(json|markdown)$"),
) -> Response | dict[str, object]:
    """Return the synthesised final paper.

    Never 404s for "no paper yet": a missing paper is a legal business state,
    not a missing resource. JSON answers ``{"available", "paper"|None,
    "reason"|None}``; markdown renders the paper or an honest stub saying the
    paper was not generated and pointing at the Research Brief -- a download
    must never return a template pretending to be a conclusion (CLAUDE.md 10).
    """
    try:
        task = await ResearchRepository(session).get_task(task_id, current_user.id)
    except TaskNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown task {task_id}",
        ) from error

    paper_payload, failure_reason = await _paper_state(session, task_id)
    reason = failure_reason
    paper = None
    if paper_payload is not None:
        try:
            paper = paper_payload_to_dataclass(paper_payload)
        except ValueError as error:
            # A stored paper that no longer parses is a real failure: report
            # it honestly rather than render a half paper.
            reason = f"stored paper failed to parse: {error}"
            paper = None
    elif reason is None:
        reason = (
            "synthesis pending"
            if task.status not in ("COMPLETED", "COMPLETED_WITH_GAPS", "FAILED")
            else "paper not generated"
        )

    if format == "markdown":
        brief = await ReportService(session).build(task_id)
        return PlainTextResponse(
            render_paper_markdown(
                paper,
                task_id,
                task.question,
                reason,
                is_mental_health=brief.is_mental_health,
            ),
            media_type="text/markdown; charset=utf-8",
        )
    return {
        "available": paper is not None,
        "paper": paper_payload,
        "reason": reason,
    }
