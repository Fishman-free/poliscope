"""Unit coverage for run_acquisition's source-diversity wiring (registry.py).

The pure decision logic is covered in tests/unit/test_source_diversity.py;
this file checks that run_acquisition actually calls it over
``acquisition.acquired`` and turns a finding into a Blindspot event, scoped to
one ACQUISITION round for the task (see the scope-trim note in
packages/evidence/source_diversity.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import EmittedEvent, PhaseContext, run_acquisition
from packages.epistemo.contracts import TaskPhase
from packages.evidence.contracts import EvidenceNodeType

_DOI_A = "10.1234/study-a"
_DOI_B = "10.1234/study-b"


@dataclass(frozen=True, slots=True)
class _Acquired:
    source_id: UUID
    doi: str | None
    title: str
    evidence_level: str
    already_known: bool = False
    authors: tuple[str, ...] = ()
    dataset_id: str | None = None
    object_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _Refused:
    query: str
    reason: str


@dataclass(frozen=True, slots=True)
class _AcquisitionResult:
    acquired: tuple[_Acquired, ...] = ()
    refused: tuple[_Refused, ...] = ()
    unresolvable: tuple[str, ...] = ()


class _FakeAcquirer:
    def __init__(self, result: _AcquisitionResult) -> None:
        self._result = result

    async def acquire(
        self, requests: list[tuple[Seat, str]]
    ) -> _AcquisitionResult:
        return self._result

    async def acquire_uploaded(
        self, object_ids: tuple[UUID, ...]
    ) -> _AcquisitionResult:
        # Not exercised by this file's scenarios -- none pass pdf_object_ids --
        # but required to satisfy SourceAcquirer.
        return _AcquisitionResult()


class _RequestingDeliberator:
    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> dict[str, object] | None:
        if phase is TaskPhase.ACQUISITION and seat is Seat.THEORY_BUILDER:
            return {"requests": [f"doi {_DOI_A}", f"doi {_DOI_B}"]}
        return None


def _context(acquired: tuple[_Acquired, ...]) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.ACQUISITION,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(),
        deliberator=_RequestingDeliberator(),
        acquirer=_FakeAcquirer(_AcquisitionResult(acquired=acquired)),
    )


def _blindspot_events(outcome: object) -> list[EmittedEvent]:
    return [
        event
        for event in outcome.events  # type: ignore[attr-defined]
        if event.event_type == EvidenceNodeType.BLINDSPOT.value
    ]


async def test_shared_dataset_across_acquired_sources_emits_a_blindspot() -> None:
    acquired = (
        _Acquired(
            source_id=uuid4(),
            doi=_DOI_A,
            title="Study A",
            evidence_level="B",
            dataset_id="add-health",
        ),
        _Acquired(
            source_id=uuid4(),
            doi=_DOI_B,
            title="Study B",
            evidence_level="B",
            dataset_id="add-health",
        ),
    )

    outcome = await run_acquisition(_context(acquired))

    blindspots = _blindspot_events(outcome)
    assert len(blindspots) == 1
    payload = blindspots[0].payload
    assert payload["kind"] == "source_diversity"
    assert "add-health" in str(payload["statement"])


async def test_diverse_sources_emit_no_diversity_blindspot() -> None:
    acquired = (
        _Acquired(
            source_id=uuid4(),
            doi=_DOI_A,
            title="Study A",
            evidence_level="B",
            dataset_id="add-health",
        ),
        _Acquired(
            source_id=uuid4(),
            doi=_DOI_B,
            title="Study B",
            evidence_level="B",
            dataset_id="a-different-cohort",
        ),
    )

    outcome = await run_acquisition(_context(acquired))

    assert _blindspot_events(outcome) == []


async def test_single_acquired_source_emits_no_diversity_blindspot() -> None:
    acquired = (
        _Acquired(
            source_id=uuid4(),
            doi=_DOI_A,
            title="Study A",
            evidence_level="B",
        ),
    )

    outcome = await run_acquisition(_context(acquired))

    assert _blindspot_events(outcome) == []


async def test_diversity_blindspot_idempotency_key_is_replay_stable() -> None:
    """Two identical replays of the same round must not collide with, but also
    must not duplicate, the same idempotency key (CLAUDE.md 10)."""
    acquired = (
        _Acquired(
            source_id=uuid4(),
            doi=_DOI_A,
            title="Study A",
            evidence_level="B",
            dataset_id="add-health",
        ),
        _Acquired(
            source_id=uuid4(),
            doi=_DOI_B,
            title="Study B",
            evidence_level="B",
            dataset_id="add-health",
        ),
    )
    context = _context(acquired)

    first = await run_acquisition(context)
    second = await run_acquisition(context)

    first_keys = [event.idempotency_key for event in _blindspot_events(first)]
    second_keys = [event.idempotency_key for event in _blindspot_events(second)]
    assert first_keys == second_keys
