"""Tests for the persistent Scientific Event Ledger.

The in-memory ledger cannot demonstrate the properties that matter here, because
idempotency and total ordering are enforced by database constraints. These tests
run against a real PostgreSQL for that reason.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.evidence.ledger import EventConflict
from packages.evidence.sql_ledger import SqlEventLedger


async def test_append_assigns_sequences_starting_at_one(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    ledger = SqlEventLedger(app_session)
    first = await ledger.append(seeded_task, "CLAIM_PROPOSED", {"n": 1}, "k1")
    second = await ledger.append(seeded_task, "CLAIM_SUPPORTED", {"n": 2}, "k2")
    assert (first.sequence, second.sequence) == (1, 2)
    await app_session.rollback()


async def test_replayed_append_returns_the_existing_event(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """A resumed task re-runs a round, so the same append must not duplicate."""
    ledger = SqlEventLedger(app_session)
    first = await ledger.append(seeded_task, "CLAIM_PROPOSED", {"n": 1}, "same")
    again = await ledger.append(seeded_task, "CLAIM_PROPOSED", {"n": 1}, "same")
    assert again.event_id == first.event_id
    assert again.sequence == first.sequence
    assert await ledger.latest_sequence(seeded_task) == 1
    await app_session.rollback()


async def test_key_reused_with_a_different_payload_is_a_conflict(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """Two different events sharing one identity would corrupt the replay."""
    ledger = SqlEventLedger(app_session)
    await ledger.append(seeded_task, "CLAIM_PROPOSED", {"n": 1}, "same")
    with pytest.raises(EventConflict):
        await ledger.append(seeded_task, "CLAIM_PROPOSED", {"n": 2}, "same")
    await app_session.rollback()


async def test_on_conflict_skip_keeps_the_existing_event(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """Round-13: a process-only event (e.g. the captured chain of thought) is
    a transient trace -- a re-run's reasoning is a *different* text by nature,
    and that collision must not fail the task. ``on_conflict="skip"`` returns
    the existing event untouched instead of raising."""
    ledger = SqlEventLedger(app_session)
    first = await ledger.append(
        seeded_task,
        "MODEL_REASONING_CAPTURED",
        {"reasoning": "first run's thoughts"},
        "reasoning:1",
    )
    again = await ledger.append(
        seeded_task,
        "MODEL_REASONING_CAPTURED",
        {"reasoning": "second run's different thoughts"},
        "reasoning:1",
        on_conflict="skip",
    )
    assert again.event_id == first.event_id
    assert again.payload["reasoning"] == "first run's thoughts"
    # The audit guard stays on for ordinary events: skip is opt-in.
    with pytest.raises(EventConflict):
        await ledger.append(
            seeded_task, "CLAIM_PROPOSED", {"n": 2}, "reasoning:1"
        )
    await app_session.rollback()


async def test_payload_key_order_does_not_create_a_false_conflict(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    ledger = SqlEventLedger(app_session)
    first = await ledger.append(seeded_task, "X", {"a": 1, "b": 2}, "ordered")
    again = await ledger.append(seeded_task, "X", {"b": 2, "a": 1}, "ordered")
    assert again.event_id == first.event_id
    await app_session.rollback()


async def test_idempotency_is_scoped_per_task(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """Unrelated tasks may legitimately choose the same idempotency key.

    The uniqueness constraint is on (task_id, idempotency_key), so a global scope
    would reject a valid append from a second task.
    """
    from decimal import Decimal

    from packages.research.models import ResearchTaskModel

    other_task = uuid4()
    app_session.add(
        ResearchTaskModel(
            id=uuid4(),
            task_id=other_task,
            question="A second, unrelated question.",
            status="AWAITING_CLAIM_CONFIRMATION",
            created_by="test_harness",
            wall_clock_minutes=30,
            model_cost_usd=Decimal("5.0000"),
            tool_call_limit=10,
            source_limit=10,
            user_evidence={},
        )
    )
    await app_session.flush()

    ledger = SqlEventLedger(app_session)
    first = await ledger.append(seeded_task, "X", {"n": 1}, "shared-key")
    second = await ledger.append(other_task, "X", {"n": 1}, "shared-key")
    assert first.event_id != second.event_id
    assert second.sequence == 1
    await app_session.rollback()


async def test_list_since_returns_only_missed_events_in_order(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """This is the SSE resume path: Last-Event-ID becomes after_sequence."""
    ledger = SqlEventLedger(app_session)
    for index in range(1, 6):
        await ledger.append(seeded_task, "TICK", {"n": index}, f"k{index}")
    missed = await ledger.list_since(seeded_task, after_sequence=2)
    assert [entry.sequence for entry in missed] == [3, 4, 5]
    assert await ledger.list_since(seeded_task, after_sequence=5) == []
    await app_session.rollback()


async def test_list_admitted_excludes_pending_and_quarantined(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """The projector must only ever see events the Evidence Gate admitted."""
    ledger = SqlEventLedger(app_session)
    await ledger.append(seeded_task, "A", {"n": 1}, "k1", status="admitted")
    await ledger.append(seeded_task, "B", {"n": 2}, "k2", status="pending")
    await ledger.append(seeded_task, "C", {"n": 3}, "k3", status="quarantined")
    admitted = await ledger.list_admitted(seeded_task)
    assert [entry.event_type for entry in admitted] == ["A"]
    await app_session.rollback()


async def test_latest_sequence_is_zero_for_a_task_with_no_events(
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    assert await SqlEventLedger(app_session).latest_sequence(seeded_task) == 0
    await app_session.rollback()


async def test_concurrent_appends_do_not_collide_on_sequence(
    migrated_db: str,
    app_session: AsyncSession,
    seeded_task: UUID,
) -> None:
    """Two writers appending at once must produce two distinct sequences.

    Without the per-task advisory lock both would read the same maximum and one
    would fail uq_event_sequence. Separate sessions are required because the lock
    is transaction scoped.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.conftest import APP_PASSWORD, APP_ROLE, _role_url

    await app_session.commit()

    engine = create_async_engine(_role_url(migrated_db, APP_ROLE, APP_PASSWORD))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def append(key: str) -> int:
        async with maker() as session:
            entry = await SqlEventLedger(session).append(
                seeded_task, "RACE", {"key": key}, key
            )
            await session.commit()
            return entry.sequence

    try:
        sequences = await asyncio.gather(append("a"), append("b"))
    finally:
        await engine.dispose()
    assert sorted(sequences) == [1, 2]
