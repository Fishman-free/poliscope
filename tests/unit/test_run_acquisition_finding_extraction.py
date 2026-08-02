"""Unit coverage for run_acquisition's StudyFinding wiring (registry.py).

The full path (real Postgres, real acquisition, real extractor) is exercised
in ``tests/integration/test_source_acquisition.py`` under Docker. Everything
this test checks -- which acquired sources get handed to the extractor, what
event a success/failure produces, and that an already-known source is never
re-extracted -- is pure application logic over Protocols, so it is verified
here without Docker using hand-written fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    PhaseContext,
    UnavailableDeliberator,
    run_acquisition,
)
from packages.epistemo.contracts import TaskPhase
from packages.evidence.contracts import EvidenceNodeType

_DOI = "10.1234/example"


@dataclass(frozen=True, slots=True)
class _Acquired:
    source_id: UUID
    doi: str
    title: str
    evidence_level: str
    already_known: bool = False
    authors: tuple[str, ...] = ()
    dataset_id: str | None = None


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


class _RequestingDeliberator:
    """Only THEORY_BUILDER asks for anything; every other seat is absent."""

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> dict[str, object] | None:
        if phase is TaskPhase.ACQUISITION and seat is Seat.THEORY_BUILDER:
            return {"requests": [f"doi {_DOI}"]}
        return None


@dataclass(frozen=True, slots=True)
class _Extraction:
    ok: bool
    reason: str = ""
    finding_id: UUID | None = None
    evidence_level: str = ""
    exact_quote: str = ""
    finding_statement: str = ""
    method_quality: dict[str, float] = field(default_factory=dict)


class _FakeFindingExtractor:
    def __init__(self, result: _Extraction) -> None:
        self._result = result
        self.calls: list[tuple[UUID, str]] = []

    async def extract(self, source_id: UUID, doi: str) -> _Extraction:
        self.calls.append((source_id, doi))
        return self._result


def _context(
    acquired: tuple[_Acquired, ...],
    finding_extractor: _FakeFindingExtractor | None,
) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.ACQUISITION,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(),
        deliberator=_RequestingDeliberator(),
        acquirer=_FakeAcquirer(_AcquisitionResult(acquired=acquired)),
        finding_extractor=finding_extractor,
    )


async def test_successful_extraction_emits_study_finding_event() -> None:
    source_id = uuid4()
    finding_id = uuid4()
    acquired = (
        _Acquired(source_id=source_id, doi=_DOI, title="A Study", evidence_level="B"),
    )
    extractor = _FakeFindingExtractor(
        _Extraction(
            ok=True,
            finding_id=finding_id,
            evidence_level="A",
            exact_quote="a verbatim supporting sentence",
            finding_statement="Screen time correlates with anxiety.",
            method_quality={"directness": 0.8},
        )
    )

    outcome = await run_acquisition(_context(acquired, extractor))

    assert extractor.calls == [(source_id, _DOI)]
    findings = [
        event
        for event in outcome.events
        if event.event_type == EvidenceNodeType.STUDY_FINDING.value
    ]
    assert len(findings) == 1
    event = findings[0]
    assert event.source_id == source_id
    assert event.finding_id == finding_id
    assert event.evidence_level == "A"
    assert event.payload["exact_quote"] == "a verbatim supporting sentence"
    assert event.payload["method_quality"] == {"directness": 0.8}
    assert not any(
        slot.startswith("ACQUISITION:no_finding:") for slot in outcome.unfilled_slots
    )


async def test_failed_extraction_records_gap_not_an_event() -> None:
    source_id = uuid4()
    acquired = (
        _Acquired(source_id=source_id, doi=_DOI, title="A Study", evidence_level="B"),
    )
    extractor = _FakeFindingExtractor(
        _Extraction(ok=False, reason="no open access full text url")
    )

    outcome = await run_acquisition(_context(acquired, extractor))

    assert extractor.calls == [(source_id, _DOI)]
    assert not any(
        event.event_type == EvidenceNodeType.STUDY_FINDING.value
        for event in outcome.events
    )
    assert f"ACQUISITION:no_finding:{_DOI}:no open access full text url" in (
        outcome.unfilled_slots
    )


async def test_already_known_source_is_never_re_extracted() -> None:
    source_id = uuid4()
    acquired = (
        _Acquired(
            source_id=source_id,
            doi=_DOI,
            title="A Study",
            evidence_level="B",
            already_known=True,
        ),
    )
    extractor = _FakeFindingExtractor(_Extraction(ok=True, finding_id=uuid4()))

    outcome = await run_acquisition(_context(acquired, extractor))

    assert extractor.calls == []
    assert not any(
        event.event_type == EvidenceNodeType.STUDY_FINDING.value
        for event in outcome.events
    )
    assert not any(
        slot.startswith("ACQUISITION:no_finding:") for slot in outcome.unfilled_slots
    )


async def test_no_finding_extractor_configured_is_unchanged_behaviour() -> None:
    source_id = uuid4()
    acquired = (
        _Acquired(source_id=source_id, doi=_DOI, title="A Study", evidence_level="B"),
    )

    outcome = await run_acquisition(_context(acquired, None))

    assert not any(
        event.event_type == EvidenceNodeType.STUDY_FINDING.value
        for event in outcome.events
    )
    assert not any(
        slot.startswith("ACQUISITION:no_finding:") for slot in outcome.unfilled_slots
    )


async def test_unavailable_deliberator_still_records_seat_absence() -> None:
    """Sanity check that the fixtures above match the existing contract:
    UnavailableDeliberator is the module default and should behave the same
    way it always has once finding_extractor is layered on top of it."""
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.ACQUISITION,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(),
        deliberator=UnavailableDeliberator(),
    )

    outcome = await run_acquisition(context)

    assert outcome.absent_seats == {Seat.THEORY_BUILDER}
