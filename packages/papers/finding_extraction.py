"""Turns an acquired Source's open-access full text into a StudyFinding.

The acquisition pipeline (``packages/papers/acquisition.py``) only ever
reaches Level B: an adapter call returns bibliographic metadata, never full
text, so a ``StudyFinding`` node -- and the ``DERIVED_FROM`` edge the
projector already knows how to draw (``packages/evidence/sql_projector.py``
lines 415-425) -- never appeared. This module is the first thing that tries
to go further: open-access lookup, PDF fetch, page extraction, a model call,
and a citation check, ending in the first production writes to
``studies`` / ``findings`` / ``citation_anchors``.

Mirrors ``packages.papers.acquisition.SourceAcquisition``'s budget-aware,
never-raise-on-one-bad-item design throughout: no open access URL, an
unparsable PDF, a failed model call, or a quote the model claims but the
source text does not actually contain are all recorded as a gap (CLAUDE.md 7:
"the system must admit unknown") rather than fabricating a downgraded
record. Only a located quote reaches the database -- CLAUDE.md 7.3 forbids
writing a formal result on an unverified citation, so a location mismatch is
a gap, not a Level B fallback.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, SupportsFloat, cast
from uuid import UUID, uuid4

from sqlalchemy import select, update

from packages.epistemo.budget import BudgetExhausted, BudgetTracker
from packages.knowledge.models import KnowledgeDocumentModel
from packages.models.contracts import (
    ModelClass,
    ModelGateway,
    ModelMessage,
    ModelRequest,
)
from packages.papers.contracts import (
    AvailabilityStatus,
    EvidenceLevel,
    PaperEvidencePacket,
)
from packages.papers.models import (
    CitationAnchorModel,
    FindingModel,
    ObjectModel,
    SourceModel,
    SourceVersionModel,
    StudyModel,
)
from packages.papers.object_store import ObjectNotFound, PrivateObjectStore
from packages.papers.packet import build_packet
from packages.papers.parser import (
    PageText,
    PdfExtractionError,
    detect_dataset_identifier,
    extract_pages,
)
from packages.tools.adapters.unpaywall import UnpaywallAdapter
from packages.tools.contracts import ToolGateway
from packages.tools.fulltext_fetcher import FullTextFetcher, FullTextFetchError

# Must equal packages.evidence.gate._METHOD_SCORE_KEYS -- these are the only
# dimensions Stage 5 of the evidence gate knows how to audit.
_METHOD_SCORE_KEYS = (
    "directness",
    "design_quality",
    "measurement_quality",
    "precision",
    "replicability",
    "external_validity",
)

_AVAILABILITY_VALUES = {item.value for item in AvailabilityStatus}

_SYSTEM_PROMPT = (
    "You extract one StudyFindingCandidate from the pages of a research "
    "paper. Quote the exact supporting sentence(s) verbatim -- do not "
    "paraphrase -- because your quote is verified against the source text "
    "before this finding is ever written to the evidence graph. Score "
    "method_quality honestly; do not default every dimension to a high score."
)


@dataclass(frozen=True, slots=True)
class FindingExtractionResult:
    """Outcome of one extraction attempt for one (source_id, doi) pair.

    A single result type carrying an ``ok`` discriminant, rather than a
    success/failure dataclass pair: the caller in
    ``packages.council.rounds.registry`` only needs to branch on ``ok`` to
    decide between emitting a ``STUDY_FINDING`` event or recording an
    unfilled slot, so there is exactly one shape to duck-type against across
    the package boundary (``council`` must not import ``papers`` directly).
    """

    doi: str | None
    source_id: UUID
    ok: bool
    reason: str = ""
    finding_id: UUID | None = None
    study_id: UUID | None = None
    exact_quote: str = ""
    evidence_level: str = ""
    finding_statement: str = ""
    method_quality: dict[str, float] = field(default_factory=dict)
    dataset_id: str | None = None


def _availability(value: object) -> AvailabilityStatus:
    text = str(value) if value else ""
    if text in _AVAILABILITY_VALUES:
        return AvailabilityStatus(text)
    return AvailabilityStatus.NOT_REPORTED


def _method_quality(value: object) -> dict[str, float]:
    # payload comes from dict(result.payload), a FrozenDict -- that call only
    # unwraps the top level, so a nested sub-object like method_quality stays
    # a FrozenDict (a Mapping, not a dict). Checking isinstance(value, dict)
    # here silently zeroed out every real score.
    scores = value if isinstance(value, Mapping) else {}
    result: dict[str, float] = {}
    for key in _METHOD_SCORE_KEYS:
        raw = scores.get(key)
        if isinstance(raw, (int, float, str)):
            try:
                result[key] = float(cast(SupportsFloat, raw))
                continue
            except ValueError:
                pass
        result[key] = 0.0
    return result


def _render_pages(pages: list[PageText]) -> str:
    return "\n\n".join(f"[page {page.page_number}]\n{page.text}" for page in pages)


@dataclass(frozen=True, slots=True)
class _Attempt:
    """One model-extraction-and-verify pass, before anything is persisted.

    Kept separate from ``FindingExtractionResult`` because dual extraction
    (CLAUDE.md 7.4, design spec 7.9, mechanism 3 of 4) needs to compare two
    of these before either is allowed to become a persisted, ``ok=True``
    result -- a shape ``FindingExtractionResult`` does not have, since it
    already commits to the single-pass ``ok``/``reason`` discriminant the
    round handler consumes.
    """

    ok: bool
    reason: str = ""
    packet: PaperEvidencePacket | None = None
    exact_quote: str = ""
    effect_direction: str = ""
    finding_statement: str = ""
    method_quality: dict[str, float] = field(default_factory=dict)


class _SessionWriter(Protocol):
    """The only AsyncSession operations this module needs.

    Kept narrow and local rather than depending on the concrete
    ``sqlalchemy.ext.asyncio.AsyncSession`` so unit tests can exercise the
    persistence path with an in-memory fake instead of a Docker-backed
    Postgres session. A real ``AsyncSession`` satisfies this Protocol
    structurally, so nothing changes for callers that pass one.
    """

    def add(self, instance: object) -> None: ...
    async def flush(self) -> None: ...
    async def execute(self, statement: Any) -> Any: ...


class FindingExtractor:
    """Extracts one StudyFinding from an already-acquired Source's full text."""

    def __init__(
        self,
        session: _SessionWriter,
        tools: ToolGateway,
        model_gateway: ModelGateway,
        task_id: UUID,
        budget: BudgetTracker | None = None,
        *,
        fulltext_fetcher: FullTextFetcher | None = None,
        object_store: PrivateObjectStore | None = None,
        researcher_skills: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._session = session
        self._tools = tools
        self._model = model_gateway
        self._task_id = task_id
        self._budget = budget
        self._fetcher = fulltext_fetcher or FullTextFetcher.from_env()
        self._object_store = object_store or PrivateObjectStore.from_env()
        # Round-5 request: an enabled skill instructs *every* process that
        # calls a model -- extraction included -- not just the council. Same
        # non-evidence labelling as the council's rendering: a skill steers
        # how the extraction is performed, it never supports a claim.
        self._skill_instructions = "".join(
            (
                f"\n【研究者提供的技能指令（非正式证据，来源：{name}）】{markdown}"
                if markdown.strip()
                else ""
            )
            for name, markdown in researcher_skills
        )

    async def extract(
        self,
        source_id: UUID,
        doi: str,
        *,
        dual_extraction: bool = False,
    ) -> FindingExtractionResult:
        """Try to turn ``doi``'s open-access full text into a StudyFinding.

        Never raises: every failure mode along the chain is returned as a
        ``FindingExtractionResult`` with ``ok=False`` and a ``reason``, for
        the caller to record as an unfilled evidence slot rather than crash
        the acquisition round it is attached to.

        ``dual_extraction`` (CLAUDE.md 7.4, design spec 7.9, mechanism 3 of
        4): when set, a Level A candidate is run through the model twice
        against the same fetched pages and the two passes' ``exact_quote``
        and ``effect_direction`` are compared. A disagreement is recorded as
        a gap needing manual audit rather than silently picking one pass --
        auto-resolving a disagreement between two of the pipeline's own
        extractions would be exactly the confident-guess CLAUDE.md 7 forbids.
        """

        dataset_id: str | None = None

        def _gap(reason: str) -> FindingExtractionResult:
            return FindingExtractionResult(
                doi=doi,
                source_id=source_id,
                ok=False,
                reason=reason,
                dataset_id=dataset_id,
            )

        if not self._spend_tool_call():
            return _gap("tool call budget exhausted before open access lookup")
        unpaywall = UnpaywallAdapter(self._tools, self._task_id)
        try:
            normalized = await unpaywall.lookup_doi(doi)
        except Exception as error:
            return _gap(f"open access lookup failed: {error!r}")
        if not normalized.controlled_fulltext_urls:
            return _gap("no open access full text url")

        if not self._spend_tool_call():
            return _gap("tool call budget exhausted before full text fetch")
        try:
            fetched = await self._fetcher.fetch(normalized.controlled_fulltext_urls[0])
        except FullTextFetchError as error:
            return _gap(f"full text unusable: {error}")
        except Exception as error:
            return _gap(f"full text fetch failed: {error!r}")

        try:
            pages = extract_pages(fetched.content)
        except PdfExtractionError as error:
            return _gap(f"pdf parsing failed: {error}")
        if not pages:
            return _gap("pdf produced no extractable text")

        # Deterministic, model-independent: runs whenever full text was
        # fetched, regardless of whether the model extraction below succeeds,
        # since a dataset declaration's presence has nothing to do with
        # whether a quote-verified finding also comes out of this paper.
        # dataset_id lives on `sources`, not `studies`/`findings`, and this
        # method never holds the original SourceModel row (only its id), so
        # the match is written back with a targeted UPDATE rather than
        # threaded through session identity.
        dataset_id = detect_dataset_identifier(pages)
        if dataset_id is not None:
            await self._session.execute(
                update(SourceModel)
                .where(SourceModel.id == source_id)
                .values(dataset_id=dataset_id)
            )

        first = await self._attempt_extraction(source_id, doi, pages)
        if not first.ok:
            return _gap(first.reason)

        if dual_extraction:
            second = await self._attempt_extraction(source_id, doi, pages)
            if not second.ok:
                return _gap(
                    "dual extraction: second pass failed "
                    f"({second.reason}) -- needs manual audit"
                )
            pairs = (
                ("exact_quote", first.exact_quote, second.exact_quote),
                (
                    "effect_direction",
                    first.effect_direction,
                    second.effect_direction,
                ),
            )
            mismatched = [name for name, left, right in pairs if left != right]
            if mismatched:
                return _gap(
                    "dual extraction disagreement on "
                    f"{', '.join(mismatched)} -- needs manual audit"
                )

        return await self._persist_finding(doi, source_id, first, dataset_id)

    async def _persist_finding(
        self,
        doi: str | None,
        source_id: UUID,
        first: _Attempt,
        dataset_id: str | None,
    ) -> FindingExtractionResult:
        """Write a verified attempt's packet to ``studies``/``findings``/
        ``citation_anchors``. Shared by ``extract`` and ``extract_uploaded`` --
        the two differ only in how ``pages`` was obtained (open-access fetch
        vs. an uploaded object), not in what happens once a Level A packet
        exists.
        """
        packet = first.packet
        assert packet is not None  # first.ok guarantees this
        study = packet.studies[0]
        finding = study.findings[0]
        anchor = finding.anchors[0]
        method_quality = first.method_quality

        # None of these five models declare an ORM relationship() to one
        # another (packages/papers/models.py has plain Column-level
        # ForeignKey only), so SQLAlchemy's unit-of-work cannot infer their
        # insert order from the pending set alone -- it will not reliably
        # place a parent row's INSERT ahead of a child's. A single add-then-
        # flush() at the end previously sent citation_anchors before findings
        # even existed, which read as a data bug (a FK violation naming
        # finding_id) but was really an ordering bug. Flushing after each add
        # forces the real dependency order: source_version, then study, then
        # finding, then anchor.
        #
        # SourceVersionModel is also this call's first write: build_packet
        # only returns the SourceVersion contract value, never persists it,
        # so without this add() studies.source_version_id would reference a
        # row that was never created.
        self._session.add(
            SourceVersionModel(
                id=packet.source_version.id,
                source_id=packet.source_version.source_id,
                version_hash=packet.source_version.version_hash,
            )
        )
        await self._session.flush()
        self._session.add(
            StudyModel(
                id=study.id,
                source_version_id=packet.source_version.id,
                research_question=study.research_question,
                design=study.design.value,
            )
        )
        await self._session.flush()
        self._session.add(
            FindingModel(
                id=finding.id,
                study_id=study.id,
                statement=finding.statement,
                origin=finding.origin,
                effect_direction=finding.effect.direction,
            )
        )
        await self._session.flush()
        self._session.add(
            CitationAnchorModel(
                id=uuid4(),
                finding_id=finding.id,
                section=anchor.section,
                page=anchor.page,
                locator=anchor.locator,
                exact_quote=anchor.exact_quote,
                extraction_agent=anchor.extraction_agent,
                verification_status=anchor.verification_status.value,
            )
        )
        await self._session.flush()

        return FindingExtractionResult(
            doi=doi,
            source_id=source_id,
            ok=True,
            finding_id=finding.id,
            study_id=study.id,
            exact_quote=first.exact_quote,
            evidence_level=packet.evidence_level.value,
            finding_statement=finding.statement,
            method_quality=method_quality,
            dataset_id=dataset_id,
        )

    async def extract_uploaded(
        self,
        source_id: UUID,
        object_id: UUID,
    ) -> FindingExtractionResult:
        """Try to turn an uploaded PDF's own bytes into a StudyFinding.

        Mirrors ``extract`` from the point full text exists onward -- same
        parsing, same model call, same Level A quote check, same persistence
        -- but skips the Unpaywall lookup and full-text fetch entirely, since
        the bytes are already sitting in the private object store under the
        key ``acquire_uploaded`` recorded on this source's ``object_id``.
        """

        dataset_id: str | None = None

        def _gap(reason: str) -> FindingExtractionResult:
            return FindingExtractionResult(
                doi=None,
                source_id=source_id,
                ok=False,
                reason=reason,
                dataset_id=dataset_id,
            )

        object_key_row = await self._session.execute(
            select(SourceModel.object_id).where(SourceModel.id == source_id)
        )
        stored_object_id = object_key_row.scalar_one_or_none()
        if stored_object_id is None:
            return _gap("source has no uploaded object to extract from")

        content = await self._retrieve_uploaded(stored_object_id)
        if content is None:
            return _gap("uploaded object not found in private object store")

        try:
            pages = extract_pages(content)
        except PdfExtractionError as error:
            return _gap(f"pdf parsing failed: {error}")
        if not pages:
            return _gap("pdf produced no extractable text")

        dataset_id = detect_dataset_identifier(pages)
        if dataset_id is not None:
            await self._session.execute(
                update(SourceModel)
                .where(SourceModel.id == source_id)
                .values(dataset_id=dataset_id)
            )

        first = await self._attempt_extraction(source_id, None, pages)
        if not first.ok:
            return _gap(first.reason)

        return await self._persist_finding(None, source_id, first, dataset_id)

    async def extract_knowledge_document(
        self,
        source_id: UUID,
        document_id: UUID,
    ) -> FindingExtractionResult:
        """Try to turn a knowledge-base document into a StudyFinding.

        Identical to ``extract_uploaded`` from the point pages exist onward;
        the only difference is where they come from: the knowledge_documents
        row the Source's ``knowledge_document_id`` points at, instead of the
        ``objects`` row an upload's ``object_id`` points at. The document was
        parsed to text at ingest, so it is Level A material by construction.

        Two page sources, chosen by the stored ``content_type``: an uploaded
        PDF is re-read from the object store so page numbers stay truthful
        for quote location; a pasted-text document has no file (its
        ``text_content`` is all there is) and is turned into one page here --
        a document that has no PDF form must never be parsed as one
        (CLAUDE.md 7).
        """

        dataset_id: str | None = None

        def _gap(reason: str) -> FindingExtractionResult:
            return FindingExtractionResult(
                doi=None,
                source_id=source_id,
                ok=False,
                reason=reason,
                dataset_id=dataset_id,
            )

        key_row = await self._session.execute(
            select(
                KnowledgeDocumentModel.object_key,
                KnowledgeDocumentModel.content_type,
                KnowledgeDocumentModel.text_content,
            ).where(KnowledgeDocumentModel.id == document_id)
        )
        row = key_row.one_or_none()
        if row is None:
            return _gap("source has no knowledge document to extract from")
        object_key, content_type, text_content = row

        if content_type != "application/pdf":
            pages = [
                PageText(page_number=1, text=text_content or "")
            ]
            if not text_content or not text_content.strip():
                return _gap("knowledge document has no extractable text")
        else:
            try:
                content = self._object_store.retrieve(object_key)
            except ObjectNotFound:
                return _gap(
                    "knowledge document not found in private object store"
                )

            try:
                pages = extract_pages(content)
            except PdfExtractionError as error:
                return _gap(f"pdf parsing failed: {error}")
            if not pages:
                return _gap("pdf produced no extractable text")

        dataset_id = detect_dataset_identifier(pages)
        if dataset_id is not None:
            await self._session.execute(
                update(SourceModel)
                .where(SourceModel.id == source_id)
                .values(dataset_id=dataset_id)
            )

        first = await self._attempt_extraction(source_id, None, pages)
        if not first.ok:
            return _gap(first.reason)

        return await self._persist_finding(None, source_id, first, dataset_id)

    async def _retrieve_uploaded(self, object_id: UUID) -> bytes | None:
        """Look up the stored object key, then read its bytes off disk.

        Two lookups because ``objects.object_key`` -- not the id -- is what
        ``PrivateObjectStore`` actually keys on; the id only identifies the
        row. Returns ``None`` rather than raising on either miss, so the
        caller records an honest gap instead of a stack trace.
        """
        key_row = await self._session.execute(
            select(ObjectModel.object_key).where(ObjectModel.id == object_id)
        )
        object_key = key_row.scalar_one_or_none()
        if object_key is None:
            return None
        try:
            return self._object_store.retrieve(object_key)
        except ObjectNotFound:
            return None

    async def _attempt_extraction(
        self, source_id: UUID, doi: str | None, pages: list[PageText]
    ) -> _Attempt:
        """One model call, one packet build, one Level A check -- no persistence.

        Split out of ``extract`` so dual extraction can run this twice against
        the same fetched pages and compare the two results before either is
        allowed to reach the database.
        """
        request = ModelRequest(
            task_id=self._task_id,
            actor="finding_extractor",
            purpose="finding_extraction",
            model_class=ModelClass.MEDIUM,
            messages=(
                ModelMessage(
                    role="system",
                    content=_SYSTEM_PROMPT + self._skill_instructions,
                ),
                ModelMessage(role="user", content=_render_pages(pages)),
            ),
            output_schema="StudyFindingExtraction",
            evidence_refs=(source_id,),
        )
        try:
            result = await self._model.invoke(request)
        except Exception as error:
            return _Attempt(ok=False, reason=f"model extraction failed: {error!r}")

        # Actual cost is only known once the call returns, unlike the fixed
        # per-call tool budget spent up front -- so this is charged after the
        # fact and a BudgetExhausted here still blocks the write below.
        if not self._spend_model_cost(result.cost_usd):
            return _Attempt(ok=False, reason="model cost budget exhausted")

        payload = cast(dict[str, Any], dict(result.payload))
        exact_quote = str(payload.get("exact_quote", ""))
        effect_direction = str(payload.get("effect_direction", "not_reported"))

        packet = build_packet(
            source_id=source_id,
            source={"doi": doi},
            pages=pages,
            study_question=str(payload.get("study_question", "")),
            population=str(payload.get("population", "")),
            design=str(payload.get("design", "other")),
            exposure_variable=str(payload.get("exposure_variable", "")),
            outcome_variable=str(payload.get("outcome_variable", "")),
            analysis_method=str(payload.get("analysis_method", "")),
            finding_statement=str(payload.get("finding_statement", "")),
            origin=str(payload.get("origin", "AI_DERIVED")),
            effect_direction=effect_direction,
            exact_quote=exact_quote,
            extraction_agent="finding_extractor",
            author_conclusions=tuple(
                str(item) for item in (payload.get("author_conclusions") or ())
            ),
            author_limitations=tuple(
                str(item) for item in (payload.get("author_limitations") or ())
            ),
            data_availability=_availability(payload.get("data_availability")),
            code_availability=_availability(payload.get("code_availability")),
            preregistration=_availability(payload.get("preregistration")),
        )

        if packet.evidence_level != EvidenceLevel.A:
            # build_packet already ran locate_quote internally and could not
            # find the model's claimed exact_quote verbatim in the parsed
            # pages (packages/papers/packet.py). CLAUDE.md 7.3: an
            # unlocatable quote must not become a formal result -- this is a
            # gap, not a Level B downgrade.
            return _Attempt(ok=False, reason="extracted quote not found in source text")

        return _Attempt(
            ok=True,
            packet=packet,
            exact_quote=exact_quote,
            effect_direction=effect_direction,
            finding_statement=packet.studies[0].findings[0].statement,
            method_quality=_method_quality(payload.get("method_quality")),
        )

    def _spend_tool_call(self) -> bool:
        if self._budget is None:
            return True
        try:
            self._budget.consume_tool_call()
        except BudgetExhausted:
            return False
        return True

    def _spend_model_cost(self, cost_usd: Decimal) -> bool:
        if self._budget is None:
            return True
        try:
            self._budget.consume_model_cost(cost_usd)
        except BudgetExhausted:
            return False
        return True


__all__ = ["FindingExtractionResult", "FindingExtractor"]
