"""Collective executive memory: index over structured cognitive events.

The collective holds the council's *scientific* record -- who proposed what,
who challenged whom, which blindspots are open -- and answers "what next",
without leaking a seat's private reasoning. This is the one thing upstream
MemoBrain does not do, and the one thing the design doc makes the core
contribution.
"""

from __future__ import annotations

from uuid import uuid4

from packages.council.contracts import ALL_SEATS
from packages.memory.collective import (
    BLINDSPOT_EVENT,
    CHALLENGE_EVENT,
    CLAIM_EVENT,
    CONSENSUS_EVENT,
    DISSENT_EVENT,
    CollectiveMemory,
)


def _claim(statement: str, claim_id: str = "c1") -> dict[str, object]:
    return {
        "node_id": claim_id,
        "statement": statement,
        "claim_type": "causal",
        "status": "contested",
    }


def test_collective_tracks_active_claims_and_blindspots() -> None:
    memory = CollectiveMemory()
    memory.absorb(
        (
            (CLAIM_EVENT, _claim("短视频导致抑郁")),
            (BLINDSPOT_EVENT, {"node_id": "b1", "statement": "自报告偏差", "kind": "bounty"}),
        )
    )
    view = memory.view()
    assert "短视频导致抑郁" in view.summary
    assert "自报告偏差" in view.summary
    assert "短视频导致抑郁" in view.active_claims


def test_collective_tracks_challenges_and_dissents() -> None:
    memory = CollectiveMemory()
    memory.absorb(
        (
            (CHALLENGE_EVENT, {"seat": "causal_scientist", "claim_id": "c1", "statement": "反向因果"}),
            (DISSENT_EVENT, {"seat": "adversarial_falsifier", "statement": "证据不足", "target_id": "c1"}),
        )
    )
    view = memory.view()
    assert "反向因果" in view.unresolved
    assert "证据不足" in view.dissents


def test_collective_reports_consensus() -> None:
    memory = CollectiveMemory()
    memory.absorb(((CONSENSUS_EVENT, {"conditional_consensus": "弱正相关，因果未决"}),))
    assert memory.has_consensus()
    assert "弱正相关" in memory.view().summary


def test_division_of_labor_assigns_all_seven_seats() -> None:
    memory = CollectiveMemory()
    assignments = memory.division_of_labor(
        ({"blindspot_id": "b1", "statement": "自报告偏差"},)
    )
    seats = {a["seat"] for a in assignments}
    assert seats == {seat.value for seat in ALL_SEATS}
    # Every seat got a distinct, non-empty task.
    tasks = {a["seat"]: a["task"] for a in assignments}
    assert all(tasks[seat] for seat in seats)
    assert len(set(tasks.values())) == 7


def test_absorb_is_idempotent_by_shape() -> None:
    memory = CollectiveMemory()
    memory.absorb(((CLAIM_EVENT, _claim("主张 A")),))
    memory.absorb(((CLAIM_EVENT, _claim("主张 A")),))
    view = memory.view()
    # Same claim absorbed twice does not double-list it.
    assert view.active_claims.count("主张 A") == 1


def test_process_noise_is_ignored() -> None:
    memory = CollectiveMemory()
    memory.absorb((("PHASE_STARTED", {"phase": "ACQUISITION"}),))
    assert memory.view().summary == ""
