"""Private process memory: isolation, recall, and what must not leak.

CLAUDE.md 3 gives each seat its own private state and CLAUDE.md 6 puts that state
in MemoBrain. The properties worth pinning are the negative ones -- what a seat
cannot see -- because a leak between seats destroys the independence the whole
protocol is built on and produces no error.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.council.contracts import ALL_SEATS, Seat
from packages.memory.adapter import create_memory_adapter
from packages.memory.council_memory import CouncilMemory, agent_id
from packages.memory.projection import POLICIES, get_policy_seats
from packages.memory.recall import perspective_recall

SEATS = tuple(sorted(ALL_SEATS, key=lambda seat: seat.value))


def test_every_seat_has_its_own_memory_key() -> None:
    """Two seats sharing a key would share a memory without any error."""
    task = uuid4()
    keys = {agent_id(task, seat) for seat in SEATS}
    assert len(keys) == len(SEATS)


def test_the_same_seat_on_two_tasks_gets_two_keys() -> None:
    """Otherwise one task's recall would prime the next task's judgment."""
    seat = Seat.CAUSAL_SCIENTIST
    assert agent_id(uuid4(), seat) != agent_id(uuid4(), seat)


async def test_a_seat_never_recalls_another_seats_episode() -> None:
    """The leak this guards against is silent: no error, just a shared view."""
    memory = CouncilMemory(create_memory_adapter(), uuid4())
    await memory.open(SEATS, "Does use cause depression?")
    await memory.remember(
        Seat.ADVERSARY_FALSIFIER, "note", "the exposure measure is self-reported"
    )

    recalled = await memory.recall(SEATS)

    assert "self-reported" in recalled[Seat.ADVERSARY_FALSIFIER]
    for seat in SEATS:
        if seat is not Seat.ADVERSARY_FALSIFIER:
            assert "self-reported" not in recalled[seat]


async def test_recall_starts_with_the_task_and_not_empty() -> None:
    """A seat with no recall of the question is deliberating about nothing."""
    question = "Does adolescent social media use cause depressive symptoms?"
    memory = CouncilMemory(create_memory_adapter(), uuid4())
    await memory.open(SEATS, question)

    recalled = await memory.recall(SEATS)

    assert all(question in text for text in recalled.values())


async def test_a_snapshot_restores_every_seats_memory() -> None:
    """CLAUDE.md 10 requires a paused task to resume with its memory intact."""
    task = uuid4()
    memory = CouncilMemory(create_memory_adapter(), task)
    await memory.open(SEATS, "question")
    await memory.remember(Seat.EVIDENCE_AUDITOR, "note", "anchor missing on S3")
    saved = await memory.snapshot(SEATS)

    resumed = CouncilMemory(create_memory_adapter(), task)
    await resumed.restore(saved)

    recalled = await resumed.recall(SEATS)
    assert "anchor missing on S3" in recalled[Seat.EVIDENCE_AUDITOR]


async def test_memory_is_not_initialised_for_an_unknown_seat() -> None:
    """Writing to a seat that was never opened would create a ghost agent."""
    memory = CouncilMemory(create_memory_adapter(), uuid4())
    with pytest.raises(KeyError):
        await memory.remember(Seat.THEORY_BUILDER, "note", "premature")


def test_all_seven_seats_have_a_distinct_projection_policy() -> None:
    """CLAUDE.md 3 forbids seven copies of one agent.

    Five seats previously fell through to an empty weight map, which meant they
    ranked the same evidence in the same arbitrary order as each other.
    """
    assert get_policy_seats() == {seat.value for seat in ALL_SEATS}
    weights = [
        tuple(sorted(policy.evidence_sort_weights.items()))
        for policy in POLICIES.values()
    ]
    assert len(set(weights)) == len(POLICIES)


def test_two_seats_rank_the_same_evidence_differently() -> None:
    """The policies must actually change the order, not merely exist."""
    snapshot = [
        {"type": "measurement", "id": "m"},
        {"type": "experimental", "id": "e"},
    ]
    causal = perspective_recall(POLICIES["causal_scientist"], "", snapshot)
    measurement = perspective_recall(POLICIES["measurement_scientist"], "", snapshot)

    assert causal.evidence_projection.items[0]["id"] == "e"
    assert measurement.evidence_projection.items[0]["id"] == "m"
