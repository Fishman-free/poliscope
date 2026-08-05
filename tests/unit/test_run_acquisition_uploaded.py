"""Unit coverage for run_acquisition's uploaded-PDF branch (registry.py).

The DOI/free-text branch is covered by test_run_acquisition_source_diversity.py
and test_run_acquisition_adversarial_retrieval.py; this file checks the
separate pass added for ``context.pdf_object_ids`` (README known-gaps item
10 / plan phase 6): SOURCE event payload shape (including the honest
``has_authors``/``authors`` reporting that differs from the DOI branch's
hardcoded ``True``), STUDY_FINDING emission on a successful
``extract_uploaded``, the two distinct unfilled-slot strings, and
idempotency-key stability for the ``uploaded_source`` key scheme.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import EmittedEvent, PhaseContext, run_acquisition
from packages.epistemo.contracts import TaskPhase
from packages.evidence.contracts import EvidenceNodeType


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
class _Extraction:
    ok: bool
    reason: str = ""
    finding_id: UUID | None = None
    evidence_level: str = ""
    exact_quote: str = ""
    finding_statement: str = ""
    method_quality: dict[str, float] = field(default_factory=dict)


class _UploadAcquirer:
    """Ignores ``requests`` (no seat asks for anything in these scenarios) and
    returns a fixed uploaded-source result regardless of the object ids
    passed to ``acquire_uploaded``, matching the scripted-double precedent in
    the sibling registry unit test files."""

    def __init__(self, result: _AcquisitionResult) -> None:
        self._result = result
        self.seen_object_ids: tuple[UUID, ...] = ()

    async def acquire(self, requests: list[tuple[Seat, str]]) -> _AcquisitionResult:
        return _AcquisitionResult()

    async def acquire_uploaded(
        self, object_ids: tuple[UUID, ...]
    ) -> _AcquisitionResult:
        self.seen_object_ids = object_ids
        return self._result

    async def acquire_dois(self, dois: tuple[str, ...]) -> _AcquisitionResult:
        # Not exercised by this file's scenarios -- none pass user DOIs.
        return _AcquisitionResult()

    async def acquire_knowledge_documents(
        self, documents: tuple[object, ...]
    ) -> _AcquisitionResult:
        # Not exercised by this file's scenarios -- none link a knowledge base.
        return _AcquisitionResult()


class _UploadFindingExtractor:
    def __init__(self, by_object_id: dict[UUID, _Extraction]) -> None:
        self._by_object_id = by_object_id
        self.calls: list[tuple[UUID, UUID]] = []

    async def extract(self, source_id: UUID, doi: str) -> _Extraction:
        raise NotImplementedError("not exercised by this file's scenarios")

    async def extract_uploaded(
        self, source_id: UUID, object_id: UUID
    ) -> _Extraction:
        self.calls.append((source_id, object_id))
        return self._by_object_id[object_id]

    async def extract_knowledge_document(
        self, source_id: UUID, document_id: UUID
    ) -> _Extraction:
        # Not exercised by this file's scenarios -- none link a knowledge base.
        return _Extraction(ok=False, reason="not exercised")


class _SilentDeliberator:
    """No seat requests anything -- isolates the uploaded-PDF branch from the
    DOI/free-text branch above it in run_acquisition."""

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> dict[str, object] | None:
        return None


def _context(
    pdf_object_ids: tuple[UUID, ...],
    acquirer: _UploadAcquirer | None,
    finding_extractor: _UploadFindingExtractor | None = None,
) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.ACQUISITION,
        seats=(Seat.THEORY_BUILDER,),
        question="Does screen time affect wellbeing?",
        confirmed_claims=(),
        deliberator=_SilentDeliberator(),
        acquirer=acquirer,
        finding_extractor=finding_extractor,
        pdf_object_ids=pdf_object_ids,
    )


def _sources(outcome: object) -> list[EmittedEvent]:
    return [
        event
        for event in outcome.events  # type: ignore[attr-defined]
        if event.event_type == EvidenceNodeType.SOURCE.value
    ]


def _findings(outcome: object) -> list[EmittedEvent]:
    return [
        event
        for event in outcome.events  # type: ignore[attr-defined]
        if event.event_type == EvidenceNodeType.STUDY_FINDING.value
    ]


async def test_uploaded_source_emits_source_event_with_honest_payload() -> None:
    object_id = uuid4()
    source_id = uuid4()
    acquired = _Acquired(
        source_id=source_id,
        doi=None,
        title="An uploaded manuscript",
        evidence_level="B",
        object_id=object_id,
    )
    acquirer = _UploadAcquirer(_AcquisitionResult(acquired=(acquired,)))

    outcome = await run_acquisition(_context((object_id,), acquirer))

    sources = _sources(outcome)
    assert len(sources) == 1
    payload = sources[0].payload
    assert payload["doi"] is None
    assert payload["has_doi"] is False
    assert payload["has_authors"] is False  # honest: no authors known yet
    assert payload["authors"] == []
    assert payload["object_id"] == str(object_id)
    assert sources[0].idempotency_key == f"ACQUISITION:uploaded_source:{object_id}"


async def test_successful_uploaded_extraction_emits_study_finding() -> None:
    object_id = uuid4()
    source_id = uuid4()
    finding_id = uuid4()
    acquired = _Acquired(
        source_id=source_id,
        doi=None,
        title="An uploaded manuscript",
        evidence_level="B",
        object_id=object_id,
    )
    acquirer = _UploadAcquirer(_AcquisitionResult(acquired=(acquired,)))
    extractor = _UploadFindingExtractor(
        {
            object_id: _Extraction(
                ok=True,
                finding_id=finding_id,
                evidence_level="A",
                exact_quote="a located quote",
                finding_statement="Uploaded study finds an association.",
                method_quality={"directness": 0.7},
            )
        }
    )

    outcome = await run_acquisition(_context((object_id,), acquirer, extractor))

    findings = _findings(outcome)
    assert len(findings) == 1
    assert findings[0].payload["doi"] is None
    assert findings[0].payload["exact_quote"] == "a located quote"
    assert findings[0].finding_id == finding_id
    assert extractor.calls == [(source_id, object_id)]
    # The lone THEORY_BUILDER seat always reports unfilled here -- _SilentDeliberator
    # returns None for every seat by design (it isolates this branch from the
    # DOI/free-text one above it), and _collect() records that as an unfilled
    # slot regardless of the upload branch's own outcome.
    assert outcome.unfilled_slots == ("ACQUISITION:theory_builder",)


async def test_failed_uploaded_extraction_records_unfilled_slot() -> None:
    object_id = uuid4()
    source_id = uuid4()
    acquired = _Acquired(
        source_id=source_id,
        doi=None,
        title="An uploaded manuscript",
        evidence_level="B",
        object_id=object_id,
    )
    acquirer = _UploadAcquirer(_AcquisitionResult(acquired=(acquired,)))
    extractor = _UploadFindingExtractor(
        {object_id: _Extraction(ok=False, reason="pdf produced no extractable text")}
    )

    outcome = await run_acquisition(_context((object_id,), acquirer, extractor))

    assert _findings(outcome) == []
    assert (
        f"ACQUISITION:no_finding:upload:{object_id}:pdf produced no extractable text"
        in outcome.unfilled_slots
    )


async def test_already_known_uploaded_source_skips_extraction() -> None:
    """A dedup hit from an earlier run must not spend extraction budget again
    (mirrors the DOI branch's already_known short-circuit)."""
    object_id = uuid4()
    acquired = _Acquired(
        source_id=uuid4(),
        doi=None,
        title="Already-seen upload",
        evidence_level="B",
        object_id=object_id,
        already_known=True,
    )
    acquirer = _UploadAcquirer(_AcquisitionResult(acquired=(acquired,)))
    extractor = _UploadFindingExtractor({})

    outcome = await run_acquisition(_context((object_id,), acquirer, extractor))

    assert extractor.calls == []
    assert _findings(outcome) == []
    # Same always-present seat slot as above -- unrelated to the already_known
    # short-circuit this test is actually checking.
    assert outcome.unfilled_slots == ("ACQUISITION:theory_builder",)


async def test_no_acquirer_configured_records_no_tool_provider_slot() -> None:
    """CLAUDE.md 7: an upload with nothing to retrieve it must still be
    visible as an unfilled slot, not silently dropped."""
    outcome = await run_acquisition(_context((uuid4(),), acquirer=None))

    assert "ACQUISITION:no_tool_provider_uploaded" in outcome.unfilled_slots
    assert _sources(outcome) == []


async def test_no_pdf_object_ids_emits_no_uploaded_source_events() -> None:
    """The common case (nothing uploaded) must not fire either branch."""
    acquirer = _UploadAcquirer(_AcquisitionResult())

    outcome = await run_acquisition(_context((), acquirer))

    assert _sources(outcome) == []
    assert not any(
        slot.startswith("ACQUISITION:no_tool_provider_upload")
        for slot in outcome.unfilled_slots
    )


async def test_uploaded_source_idempotency_key_is_replay_stable() -> None:
    """Two identical replays of the same round must produce the same
    uploaded_source key, and it must not collide with the DOI branch's
    doi-keyed scheme (CLAUDE.md 10)."""
    object_id = uuid4()
    acquired = _Acquired(
        source_id=uuid4(),
        doi=None,
        title="An uploaded manuscript",
        evidence_level="B",
        object_id=object_id,
    )
    acquirer = _UploadAcquirer(_AcquisitionResult(acquired=(acquired,)))
    context = _context((object_id,), acquirer)

    first = await run_acquisition(context)
    second = await run_acquisition(context)

    first_keys = [event.idempotency_key for event in _sources(first)]
    second_keys = [event.idempotency_key for event in _sources(second)]
    assert first_keys == second_keys
    assert first_keys == [f"ACQUISITION:uploaded_source:{object_id}"]
