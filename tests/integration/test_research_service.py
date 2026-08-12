"""Tests for the research task lifecycle against a real database.

These used to run against an in-memory service, which meant they proved the
dictionary worked rather than that a task survives the process that created it.
CLAUDE.md 8 makes the database the source of truth, so the assertions here are
about what a second session can read back.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.epistemo.contracts import TaskStatus
from packages.research.repository import (
    CLAIM_CONFIRMED,
    CLAIM_DISCARDED,
    CLAIM_SUGGESTED,
    ResearchRepository,
    TaskNotFound,
)
from packages.research.service import (
    InvalidPauseState,
    ResearchService,
    UnconfirmedClaims,
)
from tests.factories import make_research_contract


def _service(session: AsyncSession) -> ResearchService:
    return ResearchService(ResearchRepository(session))


async def test_create_persists_the_task_and_its_suggested_claims(
    app_session: AsyncSession,
) -> None:
    created = await _service(app_session).create(make_research_contract())
    assert created.status == TaskStatus.AWAITING_CLAIM_CONFIRMATION
    assert len(created.suggested_claims) > 0
    assert all(
        claim.status == CLAIM_SUGGESTED for claim in created.suggested_claims
    )

    stored = await _service(app_session).get_task(created.task_id)
    assert stored.question == make_research_contract().question
    await app_session.rollback()


async def test_create_does_not_queue_the_task(
    app_session: AsyncSession,
) -> None:
    """The researcher directs the scope, so nothing starts on its own."""
    created = await _service(app_session).create(make_research_contract())
    stored = await _service(app_session).get_task(created.task_id)
    assert stored.status != TaskStatus.QUEUED
    await app_session.rollback()


async def test_queue_requires_claim_confirmation(
    app_session: AsyncSession,
) -> None:
    created = await _service(app_session).create(make_research_contract())
    with pytest.raises(UnconfirmedClaims):
        await _service(app_session).queue(created.task_id)
    await app_session.rollback()


async def test_queue_succeeds_after_confirmation(
    app_session: AsyncSession,
) -> None:
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    assert await service.queue(created.task_id) == TaskStatus.QUEUED
    assert (await service.get_task(created.task_id)).status == TaskStatus.QUEUED
    await app_session.rollback()


async def test_unconfirmed_claims_are_discarded_not_deleted(
    app_session: AsyncSession,
) -> None:
    """CLAUDE.md 5.3 forbids removing what the council once considered.

    A claim the researcher set aside stays in the audit trail with a status that
    says so, rather than vanishing from the record.
    """
    service = _service(app_session)
    created = await service.create(make_research_contract())
    assert len(created.suggested_claims) >= 2
    chosen = created.suggested_claims[0].claim_id

    claims = await service.confirm_claims(created.task_id, (chosen,))
    assert len(claims) == len(created.suggested_claims)
    by_id = {claim.claim_id: claim.status for claim in claims}
    assert by_id[chosen] == CLAIM_CONFIRMED
    assert all(
        by_id[claim.claim_id] == CLAIM_DISCARDED
        for claim in created.suggested_claims
        if claim.claim_id != chosen
    )
    await app_session.rollback()


async def test_confirming_a_claim_from_another_task_is_rejected(
    app_session: AsyncSession,
) -> None:
    """Claim ids are not capabilities: they must belong to the named task."""
    service = _service(app_session)
    first = await service.create(make_research_contract())
    second = await service.create(make_research_contract())
    foreign = second.suggested_claims[0].claim_id
    with pytest.raises(ValueError, match="do not belong to task"):
        await service.confirm_claims(first.task_id, (foreign,))
    await app_session.rollback()


async def test_confirming_an_empty_claim_set_is_rejected(
    app_session: AsyncSession,
) -> None:
    service = _service(app_session)
    created = await service.create(make_research_contract())
    with pytest.raises(ValueError, match="at least one atomic claim"):
        await service.confirm_claims(created.task_id, ())
    await app_session.rollback()


async def test_claim_statements_round_trip_without_mangling(
    app_session: AsyncSession,
) -> None:
    """Suggested claims are written in Chinese and must come back unchanged.

    A client encoding mismatch corrupts them silently, and the corruption only
    surfaces in an exported report where nobody can tell what the claim said.
    """
    service = _service(app_session)
    created = await service.create(make_research_contract())
    written = {claim.claim_id: claim.statement for claim in created.suggested_claims}
    assert any("一" <= character <= "鿿" for character in "".join(written.values()))

    app_session.expunge_all()
    read_back = await service.suggested_claims(created.task_id)
    assert {claim.claim_id: claim.statement for claim in read_back} == written
    await app_session.rollback()


async def test_unknown_task_is_distinguishable_from_a_validation_error(
    app_session: AsyncSession,
) -> None:
    """The API answers 404 for this and 422 for a bad payload."""
    with pytest.raises(TaskNotFound):
        await _service(app_session).get_task(uuid4())
    await app_session.rollback()


async def test_pausing_a_queued_task_then_resuming_returns_it_to_queued(
    app_session: AsyncSession,
) -> None:
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)

    assert await service.pause(created.task_id) == TaskStatus.PAUSED
    assert (await service.get_task(created.task_id)).status == TaskStatus.PAUSED

    assert await service.resume(created.task_id) == TaskStatus.QUEUED
    assert (await service.get_task(created.task_id)).status == TaskStatus.QUEUED
    await app_session.rollback()


async def test_pausing_a_task_that_is_not_queued_is_rejected(
    app_session: AsyncSession,
) -> None:
    """A task still awaiting claim confirmation was never going to be run."""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    with pytest.raises(InvalidPauseState):
        await service.pause(created.task_id)
    await app_session.rollback()


async def test_resuming_a_task_that_is_not_paused_is_rejected(
    app_session: AsyncSession,
) -> None:
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)
    with pytest.raises(InvalidPauseState):
        await service.resume(created.task_id)
    await app_session.rollback()


async def test_pausing_an_unknown_task_raises_not_found(
    app_session: AsyncSession,
) -> None:
    with pytest.raises(TaskNotFound):
        await _service(app_session).pause(uuid4())
    await app_session.rollback()


async def test_cancelling_a_queued_task_flips_straight_to_cancelled(
    app_session: AsyncSession,
) -> None:
    """Round-10 停止研究: a QUEUED task has no worker holding its row."""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)

    status = await service.cancel(created.task_id, requested_by="researcher")

    assert status == TaskStatus.CANCELLED
    assert (await service.get_task(created.task_id)).status == TaskStatus.CANCELLED
    # The side-channel table holds no row for a direct flip.
    assert not await ResearchRepository(app_session).check_cancel_request(
        created.task_id
    )
    await app_session.rollback()


async def test_cancelling_a_running_task_records_a_cancel_request(
    app_session: AsyncSession,
) -> None:
    """A RUNNING task's row is locked by the worker; the stop goes to the
    side channel the worker polls between phases."""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)
    await service._repository.set_status(created.task_id, TaskStatus.RUNNING)

    status = await service.cancel(created.task_id, requested_by="researcher")

    assert status == TaskStatus.CANCELLED
    # Status is still RUNNING (the worker owns it); the request is what the
    # worker's between-phase poll will act on.
    assert (await service.get_task(created.task_id)).status == TaskStatus.RUNNING
    assert await ResearchRepository(app_session).check_cancel_request(created.task_id)
    await app_session.rollback()


async def test_cancelling_an_already_terminal_task_is_a_noop(
    app_session: AsyncSession,
) -> None:
    """Stopping a finished task reports the existing terminal status honestly."""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)
    await service._repository.set_status(created.task_id, TaskStatus.COMPLETED)

    status = await service.cancel(created.task_id)

    assert status == TaskStatus.COMPLETED
    await app_session.rollback()


async def test_re_research_accepts_a_cancelled_task(
    app_session: AsyncSession,
) -> None:
    """A researcher-stopped task can be re-run (round-10 重新研究)."""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)
    await service._repository.set_status(created.task_id, TaskStatus.CANCELLED)

    assert await service.re_research(created.task_id) == TaskStatus.QUEUED
    assert (await service.get_task(created.task_id)).status == TaskStatus.QUEUED
    await app_session.rollback()


async def test_re_research_full_mode_clears_checkpoint(
    app_session: AsyncSession,
) -> None:
    """Round-12 「重新研究模式」: mode=full clears the council checkpoint so
    the worker re-runs the whole protocol from PRECOMMITMENT."""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)
    await service._repository.set_checkpoint(
        created.task_id,
        {"run_phases": ["PRECOMMITMENT", "ACQUISITION"], "carried": {}},
    )
    await service._repository.set_status(created.task_id, TaskStatus.FAILED)

    assert await service.re_research(created.task_id, mode="full") == TaskStatus.QUEUED

    task = await service.get_task(created.task_id)
    assert task.status == TaskStatus.QUEUED
    assert task.council_checkpoint is None
    await app_session.rollback()


async def test_re_research_first_gap_without_gap_clears_checkpoint(
    app_session: AsyncSession,
) -> None:
    """Round-12: first_gap with no recorded gap degrades to a full restart --
    the checkpoint is cleared, exactly as the user asked ("no gap -> start
    from scratch")."""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)
    await service._repository.set_checkpoint(
        created.task_id,
        {
            "run_phases": ["PRECOMMITMENT", "ACQUISITION"],
            "carried": {},
            "unfilled": [],
            "failures": [],
        },
    )
    await service._repository.set_status(created.task_id, TaskStatus.FAILED)

    assert (
        await service.re_research(created.task_id, mode="first_gap")
        == TaskStatus.QUEUED
    )

    task = await service.get_task(created.task_id)
    assert task.status == TaskStatus.QUEUED
    assert task.council_checkpoint is None
    await app_session.rollback()


async def test_re_research_first_gap_with_gap_marks_restart_from(
    app_session: AsyncSession,
) -> None:
    """Round-12: first_gap with a recorded failed phase marks the checkpoint
    restart_from so the worker rewinds to that first unfinished phase."""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    chosen = created.suggested_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)
    await service._repository.set_checkpoint(
        created.task_id,
        {
            "run_phases": [
                "PRECOMMITMENT",
                "ACQUISITION",
                "EVIDENCE_EXCHANGE",
                "CROSS_EXAMINATION",
            ],
            "carried": {},
            "unfilled": ["EVIDENCE_EXCHANGE:round_failed"],
            "failures": ["EVIDENCE_EXCHANGE: ValueError('boom')"],
        },
    )
    await service._repository.set_status(created.task_id, TaskStatus.FAILED)

    assert (
        await service.re_research(created.task_id, mode="first_gap")
        == TaskStatus.QUEUED
    )

    task = await service.get_task(created.task_id)
    assert task.status == TaskStatus.QUEUED
    from packages.epistemo.contracts import CouncilCheckpoint

    restored = CouncilCheckpoint.model_validate(task.council_checkpoint)
    assert restored.restart_from == "EVIDENCE_EXCHANGE"
    assert restored.failures == ("EVIDENCE_EXCHANGE: ValueError('boom')",)
    await app_session.rollback()


async def test_rerun_fresh_creates_a_brand_new_queued_task(
    app_session: AsyncSession,
) -> None:
    """从头研究（round-13）：创建全新任务，从独立预承诺真正重新开始。

    新任务拿到新 id 并直接 QUEUED；问题/范围/预算/模型配置/语言/技能原样
    继承；只复制**已确认**的主张（被丢弃的主张不进入新一轮，其审计留在
    原任务）；原任务保持 FAILED 作为历史。
    """
    service = _service(app_session)
    created = await service.create(make_research_contract())
    all_claims = created.suggested_claims
    chosen = all_claims[0].claim_id
    await service.confirm_claims(created.task_id, (chosen,))
    await service.queue(created.task_id)
    await service._repository.set_status(created.task_id, TaskStatus.FAILED)
    original = await service.get_task(created.task_id)

    fresh_id = await service.rerun_fresh(created.task_id, created_by="api")

    assert fresh_id != created.task_id
    fresh = await service.get_task(fresh_id)
    assert fresh.status == TaskStatus.QUEUED
    assert fresh.question == original.question
    assert fresh.task_type == original.task_type
    assert fresh.model_config == original.model_config
    assert fresh.output_language == original.output_language
    assert fresh.skill_ids == original.skill_ids

    fresh_claims = await service.suggested_claims(fresh_id)
    # 新主张 id（全新任务，绝不与旧主张共享标识），内容来自已确认主张，
    # 且全部处于 CONFIRMED（新一轮无需再次确认）。
    assert {claim.claim_id for claim in fresh_claims} != {
        claim.claim_id for claim in all_claims
    }
    assert [claim.statement for claim in fresh_claims] == [
        claim.statement for claim in all_claims if claim.claim_id == chosen
    ]
    assert all(claim.status == CLAIM_CONFIRMED for claim in fresh_claims)

    # 原任务保持 FAILED —— 从头研究不触碰审计历史。
    assert (await service.get_task(created.task_id)).status == TaskStatus.FAILED
    await app_session.rollback()


async def test_rerun_fresh_refused_for_a_non_terminal_task(
    app_session: AsyncSession,
) -> None:
    """非 FAILED/CANCELLED 的任务不能被从头研究（409 的领域层对应）。"""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    with pytest.raises(InvalidPauseState):
        await service.rerun_fresh(created.task_id, created_by="api")
    await app_session.rollback()


async def test_rerun_fresh_without_confirmed_claims_is_refused(
    app_session: AsyncSession,
) -> None:
    """没有已确认主张的任务没有可继承的研究范围，拒绝而非伪造。"""
    service = _service(app_session)
    created = await service.create(make_research_contract())
    await service._repository.set_status(created.task_id, TaskStatus.FAILED)
    with pytest.raises(UnconfirmedClaims):
        await service.rerun_fresh(created.task_id, created_by="api")
    await app_session.rollback()
