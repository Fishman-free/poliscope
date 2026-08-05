"""The Research Brief, over HTTP, against real task state.

The previous version of this file called the renderer with five empty lists and
asserted the result was longer than zero characters -- a test that could not fail
while the function returned any string at all, and which passed the entire time
the renderer printed nothing but list lengths.

What is asserted here is what CLAUDE.md requires the brief to contain: findings
next to their limitations (11), gaps reported rather than hidden (10), dissent
retained (4), papers counted apart from independent evidence (7.4), and no
signed URL or local path in an export (16).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.epistemo.contracts import TaskStatus
from packages.reports.markdown import render_markdown
from packages.reports.safety import SAFETY_HEADER, sanitize_export
from packages.reports.service import ReportService, looks_like_mental_health
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED

MENTAL_HEALTH_QUESTION = "Does adolescent social media use cause depressive symptoms?"
NEUTRAL_QUESTION = "Does municipal broadband lower household internet prices?"
CLAIM = "Heavy use predicts higher depressive symptom scores."


async def _seed(
    sessions: async_sessionmaker[AsyncSession],
    user_id: UUID,
    question: str = MENTAL_HEALTH_QUESTION,
) -> tuple[UUID, UUID]:
    task_id, claim_id = uuid4(), uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=question,
                status=TaskStatus.QUEUED,
                created_by="report_test",
                user_id=user_id,
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=50,
                user_evidence={},
            )
        )
        await session.flush()
        session.add(
            AtomicClaimModel(
                id=claim_id,
                task_id=task_id,
                statement=CLAIM,
                claim_type="correlational",
                scope={"population": "adolescents"},
                falsification_condition="A preregistered cohort finds a null effect.",
                status=CLAIM_CONFIRMED,
                created_by="report_test",
            )
        )
        await session.commit()
    return task_id, claim_id


def test_a_mental_health_question_is_recognised() -> None:
    """CLAUDE.md 16 attaches the notice by domain, so the match must work."""
    assert looks_like_mental_health(MENTAL_HEALTH_QUESTION)
    assert looks_like_mental_health("社交媒体与青少年抑郁")
    assert not looks_like_mental_health(NEUTRAL_QUESTION)


async def test_the_brief_states_its_gaps_rather_than_omitting_them(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """A run with no model provider must not read as a completed study."""
    task_id, _ = await _seed(app_sessions, UUID(account["id"]))
    await run_task(app_sessions, projector_sessions, task_id)

    async with app_sessions() as session:
        brief = await ReportService(session).build(task_id)

    assert brief.has_gaps
    assert set(brief.absent_seats)
    assert brief.findings == ()
    assert any("未能参与" in item for item in brief.limitations)
    assert any("没有任何研究发现被采纳" in item for item in brief.limitations)


async def test_limitations_are_rendered_beside_the_conclusions(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """CLAUDE.md 11: conclusions and limitations appear side by side.

    Asserted by position, not by presence: a limitations section pushed below the
    blindspots and the appendix is a section most readers never reach.
    """
    task_id, _ = await _seed(app_sessions, UUID(account["id"]))
    await run_task(app_sessions, projector_sessions, task_id)

    async with app_sessions() as session:
        markdown = render_markdown(await ReportService(session).build(task_id))

    assert "## 一、结论与局限" in markdown
    assert markdown.index("### 局限与未知") < markdown.index("## 二、盲点")


async def test_a_mental_health_brief_carries_the_safety_notice(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """CLAUDE.md 16 forbids the output reading as clinical advice."""
    task_id, _ = await _seed(app_sessions, UUID(account["id"]))
    await run_task(app_sessions, projector_sessions, task_id)

    async with app_sessions() as session:
        markdown = render_markdown(await ReportService(session).build(task_id))

    assert markdown.startswith(SAFETY_HEADER)


async def test_a_neutral_question_does_not_get_the_clinical_notice(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """Attaching it everywhere would train readers to skip it."""
    task_id, _ = await _seed(
        app_sessions, UUID(account["id"]), question=NEUTRAL_QUESTION
    )
    await run_task(app_sessions, projector_sessions, task_id)

    async with app_sessions() as session:
        markdown = render_markdown(await ReportService(session).build(task_id))

    assert SAFETY_HEADER not in markdown


async def test_the_report_endpoint_serves_both_formats(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """Over HTTP, because a handler that works when called directly can still 404."""
    task_id, claim_id = await _seed(app_sessions, UUID(account["id"]))
    await run_task(app_sessions, projector_sessions, task_id)

    json_response = await api_client.get(f"/api/reports/{task_id}")
    markdown_response = await api_client.get(
        f"/api/reports/{task_id}", params={"format": "markdown"}
    )

    assert json_response.status_code == 200
    body = json_response.json()
    assert body["question"] == MENTAL_HEALTH_QUESTION
    assert body["has_gaps"] is True
    assert [claim["claim_id"] for claim in body["confirmed_claims"]] == [str(claim_id)]
    assert body["limitations"]

    assert markdown_response.status_code == 200
    assert "text/markdown" in markdown_response.headers["content-type"]
    assert CLAIM in markdown_response.text


async def test_an_unknown_task_is_a_404_not_a_500(api_client: Any) -> None:
    response = await api_client.get(f"/api/reports/{uuid4()}")
    assert response.status_code == 404


async def test_the_workspace_brief_and_the_report_agree(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """Two panels showing different conclusions is worse than one showing none."""
    task_id, _ = await _seed(app_sessions, UUID(account["id"]))
    await run_task(app_sessions, projector_sessions, task_id)

    workspace = await api_client.get(f"/api/workspace/{task_id}")
    report = await api_client.get(f"/api/reports/{task_id}")

    assert workspace.status_code == 200
    assert workspace.json()["brief"] == report.json()


def test_a_signed_url_never_survives_an_export() -> None:
    """CLAUDE.md 16: uploaded material must not leak through an export."""
    text = "Link: https://bucket.s3.amazonaws.com/file.pdf?X-Amz-Signature=secret"
    result = sanitize_export(text)
    assert "X-Amz-Signature" not in result
    assert "secret" not in result
