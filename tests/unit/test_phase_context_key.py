"""Regression tests for ``PhaseContext.key``'s idempotency-key construction.

Real production incident: ``ScientificEventModel.idempotency_key`` is a
``VARCHAR(255)`` (packages/evidence/models.py). ``PhaseContext.key()`` used to
join its parts with no length bound at all, and one caller --
``registry.py``'s ACQUISITION-phase ``SOURCE_REFUSED`` event -- passes a
model-generated free-text search query as a key part. Before the Model
Gateway's thinking-mode fix (packages/models/openai_compatible.py), every
model call failed with a 400 before any real, long query could ever reach
this code path, so the defect was invisible. Once that fix landed, a genuine
live task produced a query long enough that ``"ACQUISITION:refused:" + query``
exceeded 255 characters, and ``SqlEventLedger.append``'s ``session.flush()``
(packages/evidence/sql_ledger.py) raised
``asyncpg.exceptions.StringDataRightTruncationError``, rolling back the whole
ACQUISITION phase and leaving the task stuck at QUEUED forever.

These tests exercise ``PhaseContext.key`` directly, with no database and no
model provider, because the defect lives entirely in that one pure function.
"""

from __future__ import annotations

from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import PhaseContext, UnavailableDeliberator
from packages.epistemo.contracts import TaskPhase

MAX_IDEMPOTENCY_KEY_LENGTH = 255


def _context(phase: TaskPhase = TaskPhase.ACQUISITION) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=phase,
        seats=(Seat.THEORY_BUILDER,),
        question="Does X cause Y?",
        confirmed_claims=(),
        deliberator=UnavailableDeliberator(),
    )


def test_key_joins_phase_and_short_parts_unchanged() -> None:
    """Existing short-part behaviour (seat names, indices) must not regress."""
    context = _context()
    assert context.key("unavailable", Seat.THEORY_BUILDER.value) == (
        "ACQUISITION:unavailable:theory_builder"
    )


def test_key_stays_within_the_database_column_bound_for_a_long_free_text_part() -> None:
    """The exact production failure mode: a long model-generated query.

    This is the query shape confirmed against a real live task: a natural
    language search request that, combined with the
    ``"ACQUISITION:refused:"`` prefix, exceeded 255 characters and crashed
    ``SqlEventLedger.append`` on flush.
    """
    long_query = (
        "self-report instruments identified in the confirmed claims such as "
        "CES-D, SMFQ, PHQ-9, and other adolescent depressive symptom scales "
        "used across longitudinal cohort studies measuring social media use "
        "and screen time exposure in relation to mental health outcomes"
    )
    assert len(f"ACQUISITION:refused:{long_query}") > MAX_IDEMPOTENCY_KEY_LENGTH

    context = _context()
    key = context.key("refused", long_query)

    assert len(key) <= MAX_IDEMPOTENCY_KEY_LENGTH


def test_key_is_deterministic_across_replay_for_the_same_long_part() -> None:
    """CLAUDE.md 10's resume/replay guarantee: same input, same key, always.

    A hash-based bound is only safe here if it is stable -- a run derived
    from a non-deterministic source (e.g. object id, random salt) would make
    every requeue mint a fresh idempotency key and silently double the
    evidence, exactly what
    ``test_replaying_a_requeued_task_duplicates_nothing`` (integration suite)
    guards against at the pipeline level.
    """
    long_query = "x" * 400
    context = _context()

    assert context.key("refused", long_query) == context.key("refused", long_query)


def test_key_does_not_collide_for_different_long_parts_sharing_a_prefix() -> None:
    """Naive truncation would silently merge two distinct long queries.

    If two different long, free-text queries were simply cut off at the same
    length, they could collapse onto the same idempotency key whenever they
    share a long-enough common prefix -- corrupting idempotency by making the
    ledger believe a second, genuinely different event is a replay of the
    first (see ``EventConflict`` in packages/evidence/sql_ledger.py, which
    exists precisely to catch this class of identity clash).
    """
    shared_prefix = "cohort studies examining adolescent screen time and " * 5
    query_a = shared_prefix + "depressive symptoms"
    query_b = shared_prefix + "anxiety symptoms"
    assert query_a != query_b
    assert len(shared_prefix) > 200
    assert query_a[:200] == query_b[:200]  # long shared prefix, by construction

    context = _context()

    assert context.key("refused", query_a) != context.key("refused", query_b)


def test_key_bounds_multiple_long_parts_at_once() -> None:
    """Two long parts in one key must still fit the column, not just one."""
    context = _context()
    key = context.key("a" * 400, "b" * 400)

    assert len(key) <= MAX_IDEMPOTENCY_KEY_LENGTH
