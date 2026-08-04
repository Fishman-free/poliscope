"""Unit coverage for run_acquisition's adversarial-retrieval wiring (registry.py).

The pure query generation is covered in tests/unit/test_adversarial_retrieval.py;
this file checks that run_acquisition actually appends those six-per-claim
queries to what reaches ``context.acquirer.acquire``, attributed to the
adversarial falsifier seat, independent of whatever the other seats requested
(design spec 7.9, mechanism 4 of 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    AcquiredLike,
    PhaseContext,
    RefusedLike,
    SourceAcquirer,
    run_acquisition,
)
from packages.epistemo.contracts import TaskPhase


@dataclass(frozen=True, slots=True)
class _AcquisitionResult:
    acquired: tuple[AcquiredLike, ...] = ()
    refused: tuple[RefusedLike, ...] = ()
    unresolvable: tuple[str, ...] = ()


class _RecordingAcquirer:
    def __init__(self) -> None:
        self.seen_requests: list[tuple[Seat, str]] = []

    async def acquire(
        self, requests: list[tuple[Seat, str]]
    ) -> _AcquisitionResult:
        self.seen_requests = list(requests)
        return _AcquisitionResult()

    async def acquire_uploaded(
        self, object_ids: tuple[UUID, ...]
    ) -> _AcquisitionResult:
        # Not exercised by this file's scenarios -- none pass pdf_object_ids --
        # but required to satisfy SourceAcquirer.
        return _AcquisitionResult()


class _EverythingUnresolvableAcquirer:
    """Mimics the real pipeline: any free-text (non-DOI) query is unresolvable.

    ``CandidatePool.add`` in the real pipeline can only resolve a query that
    contains a DOI-shaped substring -- every adversarial-retrieval query is
    deliberately free-text, so in the real system they always land in
    ``AcquisitionResult.unresolvable``. This fake reproduces exactly that
    behaviour so the test below exercises the actual regression path rather
    than the always-empty ``_RecordingAcquirer`` above.
    """

    def __init__(self) -> None:
        self.seen_requests: list[tuple[Seat, str]] = []

    async def acquire(
        self, requests: list[tuple[Seat, str]]
    ) -> _AcquisitionResult:
        self.seen_requests = list(requests)
        return _AcquisitionResult(
            unresolvable=tuple(query for _, query in requests if "10." not in query)
        )

    async def acquire_uploaded(
        self, object_ids: tuple[UUID, ...]
    ) -> _AcquisitionResult:
        # Not exercised by this file's scenarios -- none pass pdf_object_ids --
        # but required to satisfy SourceAcquirer.
        return _AcquisitionResult()


class _SilentDeliberator:
    """No seat asks for anything -- adversarial retrieval must still fire."""

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> dict[str, object] | None:
        return None


def _context(
    confirmed_claims: tuple[UUID, ...], acquirer: SourceAcquirer
) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.ACQUISITION,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=confirmed_claims,
        deliberator=_SilentDeliberator(),
        acquirer=acquirer,
    )


async def test_confirmed_claim_yields_six_adversarial_requests() -> None:
    claim_id = uuid4()
    acquirer = _RecordingAcquirer()

    await run_acquisition(_context((claim_id,), acquirer))

    adversarial = [
        (seat, query)
        for seat, query in acquirer.seen_requests
        if seat is Seat.ADVERSARY_FALSIFIER
    ]
    assert len(adversarial) == 6
    assert all(str(claim_id) in query for _, query in adversarial)


async def test_two_confirmed_claims_yield_twelve_adversarial_requests() -> None:
    acquirer = _RecordingAcquirer()

    await run_acquisition(_context((uuid4(), uuid4()), acquirer))

    adversarial = [
        query
        for seat, query in acquirer.seen_requests
        if seat is Seat.ADVERSARY_FALSIFIER
    ]
    assert len(adversarial) == 12


async def test_no_confirmed_claims_means_no_adversarial_requests() -> None:
    acquirer = _RecordingAcquirer()

    await run_acquisition(_context((), acquirer))

    assert acquirer.seen_requests == []


async def test_adversarial_requests_fire_even_when_no_acquirer_is_configured() -> None:
    """No tool provider still means the request itself must be visible
    (CLAUDE.md 7: admit unknown), recorded as an unfilled slot rather than
    silently skipped."""
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.ACQUISITION,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(uuid4(),),
        deliberator=_SilentDeliberator(),
        acquirer=None,
    )

    outcome = await run_acquisition(context)

    assert "ACQUISITION:no_tool_provider" in outcome.unfilled_slots
    requested = [
        event
        for event in outcome.events
        if event.payload.get("kind") == "adversarial_retrieval"
    ]
    assert len(requested) == 1
    assert requested[0].payload["request_count"] == 6


async def test_adversarial_unresolvable_queries_do_not_become_unfilled_slots() -> None:
    """The exact regression this test locks in: run_acquisition must not let
    adversarial-retrieval's inherently-unresolvable free-text queries count
    as unfilled_slots, or COMPLETED_WITH_GAPS becomes permanent for every
    task with confirmed claims -- see the scope note in
    packages/evidence/adversarial_retrieval.py."""
    acquirer = _EverythingUnresolvableAcquirer()

    outcome = await run_acquisition(_context((uuid4(),), acquirer))

    assert not any(
        slot.startswith("ACQUISITION:unresolved:claim ")
        for slot in outcome.unfilled_slots
    )
    outcomes = [
        event
        for event in outcome.events
        if event.event_type == "ADVERSARIAL_RETRIEVAL_ATTEMPTED"
    ]
    assert len(outcomes) == 1
    assert outcomes[0].payload["attempted"] == 6
    assert outcomes[0].payload["unresolved_count"] == 6
    assert outcomes[0].payload["resolved_count"] == 0
