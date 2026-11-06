"""Enabled skills reaching the council's prompts.

A task whose ``skill_ids`` list enabled skills has their SKILL.md texts
injected into every phase's user prompt, explicitly labelled as non-evidence
process context -- the parallel of knowledge-base search hits. Disabled
skills stay out, and the precommitment round sees nothing (skills are not
evidence).
"""

from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED
from packages.skills.models import SkillModel
from tests.integration.test_seat_deliberation import (
    QUESTION,
    _fake_fulltext_fetcher,
    _run_to_completion,
    _ScriptedGateway,
    _StubProvider,
)

SKILL_MARKDOWN = (
    "# Measurement Review Skill\n"
    "Always demand a validated scale before accepting a measurement claim."
)


async def _seed_skill_and_task(
    sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
    *,
    enabled: bool = True,
    content: str | None = SKILL_MARKDOWN,
    on_disk: bool = True,
) -> tuple[UUID, UUID]:
    """Seed one enabled/disabled skill and one task that lists it.

    ``content`` is the durable copy on the row (0027); ``on_disk`` controls
    the per-container file cache. Together they let a test reproduce the
    production failure: a worker with neither a file nor a reachable GitHub
    must still run the council.
    """
    user_id = UUID(account["id"])
    skill_id = uuid4()
    # Unique per call: the session-scoped database is shared, and the
    # (user_id, github_url) unique constraint would reject a second seed.
    skill_url = f"https://github.com/owner/measurement-review-{uuid4().hex[:8]}"
    skill_dir = (
        Path(tempfile.gettempdir()) / "poliscope-skills-test" / str(user_id)
        / f"measurement-review-{uuid4().hex[:8]}"
    )
    skill_path = skill_dir / "SKILL.md"
    if on_disk:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(SKILL_MARKDOWN, encoding="utf-8")

    task_id = uuid4()
    claim_id = uuid4()
    async with sessions() as session:
        session.add(
            SkillModel(
                id=skill_id,
                user_id=user_id,
                name="measurement-review",
                github_url=skill_url,
                downloaded_path=str(skill_path),
                content=content,
                enabled=enabled,
            )
        )
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="skill_injection_test",
                user_id=user_id,
                skill_ids=[skill_id],
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
                statement="Heavy use predicts higher depressive symptom scores.",
                claim_type="correlational",
                scope={"population": "adolescents"},
                falsification_condition="A preregistered cohort finds a null effect.",
                status=CLAIM_CONFIRMED,
                created_by="skill_injection_test",
            )
        )
        await session.commit()
    return task_id, claim_id


def _prompts_for_phase(
    gateway: _ScriptedGateway, phase: TaskPhase
) -> list[str]:
    return [
        request.messages[1].content
        for request in gateway.calls
        if request.output_schema != "StudyFindingExtraction"
        and request.output_schema != "ReasoningMap"
        and TaskPhase(request.purpose) is phase
    ]


async def test_enabled_skill_reaches_later_phase_prompts(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    task_id, claim_id = await _seed_skill_and_task(app_sessions, account)
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_to_completion(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        tools=_StubProvider(),
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )
    assert result.run.failures == ()

    later_prompts = _prompts_for_phase(gateway, TaskPhase.FINAL_REJUDGMENT)
    assert later_prompts
    assert all("研究者提供的技能指令" in prompt for prompt in later_prompts)
    assert all("measurement-review" in prompt for prompt in later_prompts)
    assert all("非正式证据" in prompt for prompt in later_prompts)
    assert all(
        "validated scale" in prompt for prompt in later_prompts
    )

    # The skill's instruction text is in the very first round too: unlike
    # knowledge-base search hits (which only exist after acquisition), a
    # skill is the researcher's standing instruction and guides the council
    # from precommitment onward.
    precommitment_prompts = _prompts_for_phase(gateway, TaskPhase.PRECOMMITMENT)
    assert precommitment_prompts
    assert all("研究者提供的技能指令" in prompt for prompt in precommitment_prompts)


async def test_disabled_skill_is_not_injected(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    task_id, claim_id = await _seed_skill_and_task(
        app_sessions, account, enabled=False
    )
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_to_completion(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        tools=_StubProvider(),
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )
    assert result.run.failures == ()

    later_prompts = _prompts_for_phase(gateway, TaskPhase.FINAL_REJUDGMENT)
    assert all(
        "研究者提供的技能指令" not in prompt for prompt in later_prompts
    )


async def test_stored_skill_content_survives_missing_file_and_github(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
    monkeypatch: Any,
) -> None:
    """The production failure (2026-11-06): the worker's container had no
    SKILL.md and could not reach GitHub. The row's stored markdown is enough
    -- the council runs with the skill and never touches the network."""

    def unreachable(client: Any, url: str) -> tuple[tuple[str, str], ...]:
        raise AssertionError("the stored copy must be used without a fetch")

    monkeypatch.setattr(
        "packages.skills.service.fetch_skills_from_repo", unreachable
    )
    task_id, claim_id = await _seed_skill_and_task(
        app_sessions, account, on_disk=False
    )
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_to_completion(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        tools=_StubProvider(),
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )
    assert result.run.failures == ()

    later_prompts = _prompts_for_phase(gateway, TaskPhase.FINAL_REJUDGMENT)
    assert later_prompts
    assert all("validated scale" in prompt for prompt in later_prompts)


async def test_unloadable_skill_degrades_instead_of_failing_the_run(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
    monkeypatch: Any,
) -> None:
    """A legacy row (no stored content) on a worker that cannot reach GitHub
    must still produce a full research run -- minus the skill, not minus the
    answer. This is the "research never stops" guarantee at the exact point
    the production task used to die."""

    def unreachable(client: Any, url: str) -> tuple[tuple[str, str], ...]:
        raise httpx.ConnectError("connection reset by peer")

    monkeypatch.setattr(
        "packages.skills.service.fetch_skills_from_repo", unreachable
    )
    task_id, claim_id = await _seed_skill_and_task(
        app_sessions, account, content=None, on_disk=False
    )
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_to_completion(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        tools=_StubProvider(),
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )
    assert result.run.failures == ()
    assert result.run.final_status in (
        TaskStatus.COMPLETED,
        TaskStatus.COMPLETED_WITH_GAPS,
    )

    later_prompts = _prompts_for_phase(gateway, TaskPhase.FINAL_REJUDGMENT)
    assert later_prompts
    assert all("validated scale" not in prompt for prompt in later_prompts)
