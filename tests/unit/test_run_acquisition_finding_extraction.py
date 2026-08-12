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
    doi: str | None
    title: str
    evidence_level: str
    already_known: bool = False
    authors: tuple[str, ...] = ()
    dataset_id: str | None = None
    object_id: UUID | None = None
    document_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _Refused:
    query: str
    reason: str


@dataclass(frozen=True, slots=True)
class _AcquisitionResult:
    acquired: tuple[_Acquired, ...] = ()
    refused: tuple[_Refused, ...] = ()
    unresolvable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _KnowledgeDocument:
    document_id: UUID
    object_key: str
    title: str


class _FakeAcquirer:
    def __init__(
        self,
        result: _AcquisitionResult,
        *,
        uploaded: _AcquisitionResult | None = None,
        user_dois: _AcquisitionResult | None = None,
        knowledge_documents: _AcquisitionResult | None = None,
    ) -> None:
        self._result = result
        self._uploaded = uploaded or _AcquisitionResult()
        self._user_dois = user_dois or _AcquisitionResult()
        self._knowledge_documents = knowledge_documents or _AcquisitionResult()

    async def acquire(
        self, requests: list[tuple[Seat, str]]
    ) -> _AcquisitionResult:
        return self._result

    async def acquire_uploaded(
        self, object_ids: tuple[UUID, ...]
    ) -> _AcquisitionResult:
        return self._uploaded

    async def acquire_dois(self, dois: tuple[str, ...]) -> _AcquisitionResult:
        return self._user_dois

    async def acquire_knowledge_documents(
        self, documents: tuple[object, ...]
    ) -> _AcquisitionResult:
        return self._knowledge_documents


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
        self.uploaded_calls: list[tuple[UUID, UUID]] = []

    async def extract(self, source_id: UUID, doi: str) -> _Extraction:
        self.calls.append((source_id, doi))
        return self._result

    async def extract_uploaded(
        self, source_id: UUID, object_id: UUID
    ) -> _Extraction:
        # Not exercised by this file's scenarios -- see acquire_uploaded above.
        self.uploaded_calls.append((source_id, object_id))
        return self._result

    async def extract_knowledge_document(
        self, source_id: UUID, document_id: UUID
    ) -> _Extraction:
        # Not exercised by this file's scenarios -- none link a knowledge base.
        return self._result


def _context(
    acquired: tuple[_Acquired, ...],
    finding_extractor: _FakeFindingExtractor | None,
    *,
    uploaded: tuple[_Acquired, ...] = (),
    user_dois: tuple[_Acquired, ...] = (),
    knowledge_sources: tuple[_Acquired, ...] = (),
    pdf_object_ids: tuple[UUID, ...] = (),
    requested_dois: tuple[str, ...] = (),
    knowledge_documents: tuple[_KnowledgeDocument, ...] = (),
) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.ACQUISITION,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(),
        deliberator=_RequestingDeliberator(),
        acquirer=_FakeAcquirer(
            _AcquisitionResult(acquired=acquired),
            uploaded=_AcquisitionResult(acquired=uploaded),
            user_dois=_AcquisitionResult(acquired=user_dois),
            knowledge_documents=_AcquisitionResult(acquired=knowledge_sources),
        ),
        finding_extractor=finding_extractor,
        pdf_object_ids=pdf_object_ids,
        user_dois=requested_dois,
        knowledge_documents=knowledge_documents,
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


async def test_acquisition_carries_every_real_acquired_source() -> None:
    seat_source = _Acquired(uuid4(), _DOI, "Seat source", "B")
    object_id = uuid4()
    uploaded_source = _Acquired(
        uuid4(), None, "Uploaded source", "A", object_id=object_id
    )
    user_doi = "10.1234/user"
    user_source = _Acquired(uuid4(), user_doi, "User DOI source", "B")
    document_id = uuid4()
    knowledge_source = _Acquired(
        uuid4(), None, "Knowledge source", "A", document_id=document_id
    )

    outcome = await run_acquisition(
        _context(
            (seat_source,),
            None,
            uploaded=(uploaded_source,),
            user_dois=(user_source,),
            knowledge_sources=(knowledge_source,),
            pdf_object_ids=(object_id,),
            requested_dois=(user_doi,),
            knowledge_documents=(
                _KnowledgeDocument(
                    document_id=document_id,
                    object_key="knowledge/document.pdf",
                    title="Knowledge source",
                ),
            ),
        )
    )

    assert outcome.carry["available_sources"] == (
        {
            "source_id": str(seat_source.source_id),
            "title": seat_source.title,
            "level": seat_source.evidence_level,
        },
        {
            "source_id": str(uploaded_source.source_id),
            "title": uploaded_source.title,
            "level": uploaded_source.evidence_level,
        },
        {
            "source_id": str(user_source.source_id),
            "title": user_source.title,
            "level": user_source.evidence_level,
        },
        {
            "source_id": str(knowledge_source.source_id),
            "title": knowledge_source.title,
            "level": knowledge_source.evidence_level,
        },
    )
