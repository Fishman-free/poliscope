"""The workspace's ``seats`` and ``evolution`` fields, over a real council run.

Both fields were hardcoded to ``()`` in ``apps/api/routers/workspace.py`` until
now. This file proves they carry real, non-fabricated data once a scripted
council run actually produces precommitments, a challenge, a fork, and a
dissent -- not just that the endpoint returns *some* shape for them.

CLAUDE.md 11 requires the council panel to show only structured actions,
evidence used, challenges and responses, and confidence changes -- never a
seat's private chain of thought -- which is why the assertions below check
for exactly those fields and nothing resembling free-form model reasoning.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.council.contracts import Seat
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED

QUESTION = "Does adolescent social media use cause depressive symptoms?"

# The seat whose CROSS_EXAMINATION challenge also carries a ``fork`` -- the
# one path that populates the ledger's dedicated ``claim_id`` column (see
# packages/council/rounds/registry.py::_fork_events), so this run is the one
# that actually exercises the Claim side of the evolution feed, not only the
# challenge/dissent side.
_FORKING_SEAT = Seat.CAUSAL_SCIENTIST
_CHALLENGING_SEAT = Seat.THEORY_BUILDER


class _WorkspacePanelGateway:
    """Answers every phase this test needs; deterministic per seat.

    A separate, minimal double rather than importing the one from
    ``test_seat_deliberation.py`` -- each test file scripts the smallest
    gateway that proves its own claim, the same convention already used by
    ``tests/unit/test_evaluation_demo_case.py``.
    """

    def __init__(self, claim_id: UUID) -> None:
        self._claim_id = claim_id

    def _payload(self, request: ModelRequest) -> dict[str, object]:
        if request.output_schema == "StudyFindingExtraction":
            return {}
        phase = TaskPhase(request.purpose)
        seat = request.actor
        if phase is TaskPhase.PRECOMMITMENT:
            return {
                "initial_judgment": f"{seat}: correlational support only",
                "confidence": 0.4,
                "update_condition": "a preregistered cohort study",
            }
        if phase is TaskPhase.ACQUISITION:
            return {"requests": []}
        if phase is TaskPhase.CROSS_EXAMINATION:
            if seat == _FORKING_SEAT.value:
                return {
                    "challenges": [
                        {
                            "claim_id": str(self._claim_id),
                            "statement": f"{seat} disputes the exposure measure",
                            "is_fatal": True,
                            "fork": {
                                "statement": (
                                    "A narrower claim limited to preregistered "
                                    "cohorts only."
                                ),
                                "falsification_condition": (
                                    "A preregistered cohort finds a null effect."
                                ),
                            },
                        }
                    ]
                }
            if seat == _CHALLENGING_SEAT.value:
                return {
                    "challenges": [
                        {
                            "claim_id": str(self._claim_id),
                            "statement": f"{seat} disputes the measurement window",
                            "is_fatal": False,
                        }
                    ]
                }
            return {"challenges": []}
        if phase is TaskPhase.BLINDSPOT_BOUNTY:
            if seat == Seat.ADVERSARY_FALSIFIER.value:
                return {
                    "blindspots": [
                        {
                            "id": str(uuid4()),
                            "statement": "Self-reported screen time is misremembered.",
                            "impact": "0.9",
                            "uncertainty": "0.8",
                            "investigability": "0.7",
                            "novelty": "0.6",
                            "normalized_cost": "0.2",
                        }
                    ]
                }
            return {"blindspots": []}
        if phase is TaskPhase.JOINT_MODELING:
            return {
                "strongest_opposition_refs": [str(self._claim_id)],
                "falsification_conditions": ["A null effect in a preregistered RCT."],
                "boundary_conditions": ["Western adolescent samples only."],
                "unresolved_conflicts": ["Effect direction across sexes."],
            }
        if phase is TaskPhase.FINAL_REJUDGMENT:
            if seat == Seat.ADVERSARY_FALSIFIER.value:
                return {
                    "final_judgment": (
                        f"{seat}: I dissent -- the causal claim overreaches "
                        "the cross-sectional evidence."
                    )
                }
            return {"final_judgment": f"{seat}: narrowed, not withdrawn"}
        return {}

    async def invoke(self, request: ModelRequest) -> ModelResult:
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(self._payload(request)),
            input_tokens=50,
            output_tokens=50,
            cost_usd=Decimal("0"),
            latency_ms=5,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


async def _seed_queued_task(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[UUID, UUID]:
    task_id = uuid4()
    claim_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="workspace_panel_test",
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
                created_by="workspace_panel_test",
            )
        )
        await session.commit()
    return task_id, claim_id


async def test_seats_panel_carries_real_precommitment_challenge_and_judgment(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _WorkspacePanelGateway(claim_id)
    await run_task(app_sessions, projector_sessions, task_id, gateway=gateway)

    body = (await api_client.get(f"/api/workspace/{task_id}")).json()
    seats = {entry["seat"]: entry for entry in body["seats"]}

    # All seven seats appear, in the canonical order, whether or not each one
    # did anything this round -- an absent seat must stay visible, not vanish.
    assert [entry["seat"] for entry in body["seats"]] == sorted(
        seat.value for seat in Seat
    )

    theory_builder = seats[Seat.THEORY_BUILDER.value]
    assert theory_builder["precommitment"]["confidence"] == 0.4
    assert theory_builder["precommitment"]["update_condition"]
    assert len(theory_builder["challenges_raised"]) == 1
    assert theory_builder["challenges_raised"][0]["is_fatal"] is False
    assert theory_builder["final_judgment"]["has_dissent"] is False

    falsifier = seats[Seat.ADVERSARY_FALSIFIER.value]
    assert falsifier["final_judgment"]["has_dissent"] is True
    assert "dissent" in falsifier["final_judgment"]["final_judgment"].lower()

    # Nothing here is free-form model reasoning beyond what the round itself
    # already emits as a structured field (CLAUDE.md 11).
    for entry in body["seats"]:
        assert set(entry) == {
            "seat",
            "precommitment",
            "challenges_raised",
            "final_judgment",
            "unavailable_phases",
        }


async def test_evolution_feed_carries_the_fork_challenge_and_dissent(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _WorkspacePanelGateway(claim_id)
    await run_task(app_sessions, projector_sessions, task_id, gateway=gateway)

    body = (await api_client.get(f"/api/workspace/{task_id}")).json()
    evolution = body["evolution"]
    event_types = [entry["event_type"] for entry in evolution]

    # The forking seat's fatal challenge produces two Claim events (the
    # anchor and the fork itself -- see _fork_events), so Claim must appear.
    assert "Claim" in event_types
    assert "CHALLENGE_RAISED" in event_types
    assert "DissentCertificate" in event_types

    # Every entry names the claim it is about wherever the production code
    # can determine one -- that is the whole point of this feed.
    claim_ids = {entry["claim_id"] for entry in evolution}
    assert str(claim_id) in claim_ids or any(
        entry["claim_id"] is not None
        for entry in evolution
        if entry["event_type"] == "Claim"
    )

    for entry in evolution:
        assert set(entry) == {"sequence", "event_type", "status", "claim_id", "payload"}
