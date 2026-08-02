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

from packages.epistemo.budget import BudgetExhausted, BudgetTracker
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
    SourceVersionModel,
    StudyModel,
)
from packages.papers.packet import build_packet
from packages.papers.parser import PageText, PdfExtractionError, extract_pages
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

    doi: str
    source_id: UUID
    ok: bool
    reason: str = ""
    finding_id: UUID | None = None
    study_id: UUID | None = None
    exact_quote: str = ""
    evidence_level: str = ""
    finding_statement: str = ""
    method_quality: dict[str, float] = field(default_factory=dict)


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
    """The only two AsyncSession operations this module needs.

    Kept narrow and local rather than depending on the concrete
    ``sqlalchemy.ext.asyncio.AsyncSession`` so unit tests can exercise the
    persistence path with an in-memory fake instead of a Docker-backed
    Postgres session. A real ``AsyncSession`` satisfies this Protocol
    structurally, so nothing changes for callers that pass one.
    """

    def add(self, instance: object) -> None: ...
    async def flush(self) -> None: ...


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
    ) -> None:
        self._session = session
        self._tools = tools
        self._model = model_gateway
        self._task_id = task_id
        self._budget = budget
        self._fetcher = fulltext_fetcher or FullTextFetcher.from_env()

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

        def _gap(reason: str) -> FindingExtractionResult:
            return FindingExtractionResult(
                doi=doi, source_id=source_id, ok=False, reason=reason
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
        )

    async def _attempt_extraction(
        self, source_id: UUID, doi: str, pages: list[PageText]
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
                ModelMessage(role="system", content=_SYSTEM_PROMPT),
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
