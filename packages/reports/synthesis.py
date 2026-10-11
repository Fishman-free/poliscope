"""Synthesis step: one model call turns the council's outputs into a summary.

The council's REPORTING phase is deliberately empty (registry.py's
``run_reporting`` -- "the report is assembled from the graph, not from a
round"), and the Research Brief is a zero-model template over the graph and
ledger. The summary this module writes is different and complementary: it is
the *integrating* step the researcher asked for.

For a deep-research task that integration is a **reasoning map**
(:mod:`packages.reports.reasoning_map`): the model is shown a compact,
numbered catalogue of everything the council produced and answers with one
connected argument -- a direct answer, per-claim verdicts, a six-step chain
(evidence → measurement → robustness → causality → boundary → mechanism)
whose every step carries its own support, limits and unknowns, the cruxes
that remain, and what to study next -- citing catalogue ids instead of
re-writing the material. That keeps the output small (the old "write the
whole paper" call routinely ran past the gateway's 300s deadline and fell
back to a ~180-line dump) and lets the deterministic fallback produce the
same shape. The paper's prose fields are derived from the map. Paper-review
tasks keep their own report schema.

Three invariants shape it:

* **The paper is not evidence.** It is stored as ledger events
  (``FINAL_PAPER_DRAFTED`` / ``FINAL_PAPER_FAILED``) that the projector marks
  ``process_only``; it never becomes graph nodes, and it never changes the
  task's terminal status (COMPLETED vs COMPLETED_WITH_GAPS stays a pure
  function of evidence gaps). A missing paper is reported honestly, never
  filled with a template.
* **Every fact it writes was already admitted.** The prompt is built from
  the Research Brief (confirmed claims, admitted findings, blindspots,
  dissents, limitations, evidence coverage) plus the ledger's conditioned
  consensus and the seven final judgments -- nothing the gate refused.
* **Nothing it writes is new evidence.** The reference list cites source
  ids/DOIs that already exist in the task; the model names them, it does not
  fetch them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.council.deliberation import OUTPUT_LANGUAGE_DIRECTIVES
from packages.evidence.models import ScientificEventModel
from packages.evidence.sql_ledger import SqlEventLedger
from packages.kernel.database import canonical_uuid
from packages.models.contracts import (
    ModelClass,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    SchemaStatus,
)
from packages.models.gateway import AuditedModelGateway
from packages.reports.contracts import (
    EvidenceGap,
    FinalPaper,
    MapItem,
    PaperOverview,
    PaperReference,
    PaperReviewReport,
    PaperSection,
    ReasoningMap,
    ReviewClaim,
    ReviewIssue,
    Standpoint,
    SynthesisOutcome,
)
from packages.reports.reasoning_map import (
    DIMENSIONS,
    MODE_DETERMINISTIC,
    JudgmentRow,
    build_catalog,
    catalog_prompt_lines,
    deterministic_map,
    finding_references,
    map_from_payload,
    map_to_payload,
    meaningful_consensus,
    paper_abstract,
    paper_sections,
    parse_model_map,
    seat_display,
)
from packages.reports.safety import sanitize_export
from packages.reports.service import ReportService, ResearchBrief
from packages.research.atomization import (
    claim_type_label,
    is_placeholder_statement,
)
from packages.research.language import detect_output_language
from packages.research.models import ResearchTaskModel

logger = logging.getLogger(__name__)

# Ledger event names. Both stay out of NODE_EVENT_TYPES, so the projector
# marks them process_only: the paper is auditable history, not evidence.
FINAL_PAPER_DRAFTED = "FINAL_PAPER_DRAFTED"
FINAL_PAPER_FAILED = "FINAL_PAPER_FAILED"

_PAPER_IDEMPOTENCY_KEY = "REPORTING:final_paper"
_EMERGENCY_PAPER_IDEMPOTENCY_KEY = "REPORTING:emergency_fallback"
_FAILED_IDEMPOTENCY_KEY = "REPORTING:final_paper_failed"

# Live-view progress for the synthesis call (process stream, never ledger):
# the REPORTING phase used to sit silent for up to five minutes while the
# model wrote, which read as a hung run.
SYNTHESIS_STARTED = "synthesis_started"
SYNTHESIS_WORKING = "synthesis_working"
SYNTHESIS_DONE = "synthesis_done"
_SYNTHESIS_HEARTBEAT_SECONDS = 10.0

ProcessEmit = Callable[[str, dict[str, object]], None]
ProcessFlush = Callable[[], Awaitable[None]]


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value if item is not None and str(item))


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def _parse_references(value: object) -> tuple[PaperReference, ...]:
    """Tolerantly parse the model's reference list.

    A reference whose id or title is missing is dropped rather than rendered
    as a broken entry -- the paper must not cite nothing. DOIs stay as the
    model wrote them; ``sanitize_export`` runs over the whole prompt and the
    final render, so a malformed URL cannot leak through either.

    Accepts a list or tuple: the gateway's FrozenDict freezes lists into
    tuples before ``_parse_paper`` ever sees the payload, while a stored
    ledger payload (read back from JSONB) keeps its native list shape.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    references: list[PaperReference] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        ref_id = _as_str(item.get("id"))
        title = _as_str(item.get("title"))
        if not ref_id or not title:
            continue
        doi = item.get("doi")
        references.append(
            PaperReference(
                id=ref_id,
                title=title,
                doi=None if doi is None else _as_str(doi),
            )
        )
    return tuple(references)


def _phase_coverage(brief: ResearchBrief) -> str:
    """A one-line account of which protocol phases ran, for the fallback
    paper's process section. Mirrors the brief's own gap vocabulary so the
    fallback never reads as a full run when seats were absent."""
    skipped = len(brief.skipped_phases)
    failed = len(brief.failed_phases)
    if not skipped and not failed:
        return "全部阶段完成"
    parts = []
    if skipped:
        parts.append(f"{skipped} 个阶段未执行")
    if failed:
        parts.append(f"{failed} 个阶段失败")
    return "；".join(parts)


def _parse_paper(payload: dict[str, object]) -> FinalPaper:
    """Parse a model payload into a FinalPaper, or raise on a broken shape.

    Raising (rather than coercing) is deliberate: the model call already went
    through the gateway's schema repair, so a payload that still fails here is
    a real failure and must surface as FINAL_PAPER_FAILED with an honest
    reason -- not as a half-filled paper.
    """
    title = _as_str(payload.get("title"))
    abstract = _as_str(payload.get("abstract"))
    if not title or not abstract:
        raise ValueError("paper payload missing title or abstract")

    raw_sections = payload.get("sections")
    # A list or a tuple: FrozenDict freezes lists to tuples on the gateway
    # path, the ledger stores native lists (see _parse_references).
    if not isinstance(raw_sections, (list, tuple)):
        raise ValueError("paper payload missing sections list")
    sections: list[PaperSection] = []
    for item in raw_sections:
        if not isinstance(item, Mapping):
            continue
        heading = _as_str(item.get("heading"))
        paragraphs = _strings(item.get("paragraphs"))
        if not heading or not paragraphs:
            continue
        sections.append(PaperSection(heading=heading, paragraphs=paragraphs))

    references = _parse_references(payload.get("references"))
    limitations = _strings(payload.get("limitations"))
    process = _strings(payload.get("investigation_process"))
    if not sections:
        raise ValueError("paper payload produced no sections")

    standpoints: list[Standpoint] = []
    raw_standpoints = payload.get("standpoints")
    if isinstance(raw_standpoints, (list, tuple)):
        for item in raw_standpoints:
            if not isinstance(item, Mapping):
                continue
            seat = _as_str(item.get("seat"))
            position = _as_str(item.get("position"))
            weakness = _as_str(item.get("weakness"))
            if not seat or not position:
                continue
            standpoints.append(
                Standpoint(
                    seat=seat,
                    position=position,
                    weakness=weakness,
                    supporting_evidence=_strings(item.get("supporting_evidence")),
                    disagreement=_as_str(item.get("disagreement")),
                )
            )

    return FinalPaper(
        title=title,
        abstract=abstract,
        sections=tuple(sections),
        references=references,
        limitations=limitations,
        investigation_process=process,
        standpoints=tuple(standpoints),
        overall_conclusion=_as_str(payload.get("overall_conclusion")),
        conclusion_evidence=_strings(payload.get("conclusion_evidence")),
        reasoning_map=map_from_payload(payload.get("reasoning_map")),
    )


def _paper_payload_dict(paper: FinalPaper) -> dict[str, object]:
    """Serialize a FinalPaper to the ledger payload shape (both the model
    path and the deterministic fallback write through here)."""
    payload: dict[str, object] = {
        "title": paper.title,
        "abstract": paper.abstract,
        "sections": [
            {"heading": section.heading, "paragraphs": list(section.paragraphs)}
            for section in paper.sections
        ],
        "references": [
            {"id": ref.id, "title": ref.title, "doi": ref.doi}
            for ref in paper.references
        ],
        "limitations": list(paper.limitations),
        "investigation_process": list(paper.investigation_process),
        "standpoints": [
            {
                "seat": standpoint.seat,
                "position": standpoint.position,
                "weakness": standpoint.weakness,
                "supporting_evidence": list(standpoint.supporting_evidence),
                "disagreement": standpoint.disagreement,
            }
            for standpoint in paper.standpoints
        ],
        "overall_conclusion": paper.overall_conclusion,
        "conclusion_evidence": list(paper.conclusion_evidence),
    }
    if paper.reasoning_map is not None:
        payload["reasoning_map"] = map_to_payload(paper.reasoning_map)
    return payload


# ------------------------------------------------------------ reasoning map


def _brief_catalog(
    brief: ResearchBrief,
    consensus: Mapping[str, object],
    judgments: Sequence[JudgmentRow],
) -> tuple[MapItem, ...]:
    return build_catalog(
        findings=[node.payload for node in brief.findings],
        blindspots=[node.payload for node in brief.blindspots],
        dissents=[node.payload for node in brief.dissents],
        studies=[node.payload for node in brief.discriminating_studies],
        consensus=consensus,
        judgments=judgments,
    )


def _claims_under_test(brief: ResearchBrief) -> list[str]:
    """Confirmed claims with real content. Placeholder claims
    (关联主张：/因果主张：…) only scope the investigation; claim_type renders
    in Chinese so no English token reaches the reader."""
    return [
        f"{claim.statement}（类型：{claim_type_label(str(claim.claim_type).lower())}）"
        for claim in brief.confirmed_claims
        if not is_placeholder_statement(claim.statement)
    ]


def _gap_limitations(brief: ResearchBrief) -> tuple[str, ...]:
    limitations = list(brief.limitations)
    if brief.has_gaps:
        gap_parts: list[str] = []
        if brief.absent_seats:
            gap_parts.append(f"{len(set(brief.absent_seats))} 位专家至少一轮缺席")
        if brief.failed_phases:
            gap_parts.append(f"{len(brief.failed_phases)} 个阶段失败")
        if brief.skipped_phases:
            gap_parts.append(f"{len(brief.skipped_phases)} 个阶段未执行")
        if brief.unadmitted_events:
            gap_parts.append(f"{len(brief.unadmitted_events)} 项证据被证据门拒绝")
        limitations.append(
            "；".join(gap_parts) + "。以上结论仅基于已完成的研究部分，需谨慎对待。"
        )
    return tuple(limitations)


def _process_lines(brief: ResearchBrief) -> tuple[str, ...]:
    process = [
        f"证据覆盖：{brief.paper_count} 篇论文，"
        f"{brief.independent_cluster_count} 个独立证据簇，"
        f"{len(brief.findings)} 条已采纳发现。",
        f"研究阶段：{_phase_coverage(brief)}。",
    ]
    if brief.absent_seats:
        absent = dict.fromkeys(seat_display(seat) for seat in brief.absent_seats)
        process.append(f"缺席专家：{'、'.join(absent)}。")
    return tuple(process)


def _map_paper(rmap: ReasoningMap, brief: ResearchBrief, question: str) -> FinalPaper:
    """The FinalPaper around a reasoning map: prose fields derived from the
    map so the Markdown export and every older reader stay in step with it."""
    abstract = paper_abstract(rmap)
    claims = _claims_under_test(brief)
    if rmap.mode == MODE_DETERMINISTIC and claims:
        abstract += "\n\n检验的主张：\n" + "\n".join(f"- {line}" for line in claims)
    sections = paper_sections(rmap) or (
        PaperSection(heading="证据概况", paragraphs=_process_lines(brief)),
    )
    return FinalPaper(
        title=f"关于「{question}」的研究结论",
        abstract=abstract,
        sections=sections,
        references=finding_references(
            [(str(node.node_id), node.payload) for node in brief.findings]
        ),
        limitations=_gap_limitations(brief),
        investigation_process=_process_lines(brief),
        overall_conclusion=rmap.bottom_line,
        reasoning_map=rmap,
    )


def _fallback_integrated_paper(
    brief: ResearchBrief,
    consensus: Mapping[str, object],
    question: str,
    judgments: Sequence[JudgmentRow] = (),
) -> FinalPaper:
    """Assemble the summary from the brief alone, no model involved.

    Round-9 「最终论文总是整合结论」: when the synthesis model call fails or is
    quarantined, the task must still end with a readable conclusion. The
    deterministic reasoning map files the same catalogue the model would have
    been shown, so this fallback and the model path can never disagree about
    what the council produced -- they differ only in who organised it, and
    the map's ``mode`` says which.
    """
    items = _brief_catalog(brief, consensus, judgments)
    rmap = deterministic_map(
        items, bottom_line=_as_str(consensus.get("conditional_consensus"))
    )
    return _map_paper(rmap, brief, question)


_MAP_SYSTEM_PROMPT = (
    "You are the reporting synthesizer for a seven-seat research council. "
    "You organise the council's admitted outputs into one connected "
    "argument. You are an editor, not an eighth scientist: you cast no "
    "judgment of your own and add no source, number, or conclusion that is "
    "not in the material. Write for an ordinary academic reader who knows "
    "nothing about the software that produced the material."
)


def _build_map_prompt(
    question: str, brief: ResearchBrief, items: Sequence[MapItem]
) -> str:
    spine = [
        f"  {index}. {dimension.key} -- {dimension.question}"
        for index, dimension in enumerate(DIMENSIONS, start=1)
    ]
    claims = _claims_under_test(brief) or [
        "(the research question itself, tested as a correlational claim and "
        "as a causal claim)"
    ]
    lines = [
        f"Research question: {question}",
        "",
        "Claims under test:",
        *(f"- {claim}" for claim in claims),
        "",
        f"Evidence coverage: {brief.paper_count} papers, "
        f"{brief.independent_cluster_count} independent evidence clusters, "
        f"{len(brief.findings)} admitted findings bound to a source; "
        f"{len(brief.unadmitted_events)} submissions refused by the evidence gate.",
        "",
        "TASK: the reader currently receives the catalogue below as dozens of "
        "disconnected lists and cannot see how they fit together. Turn it into "
        "ONE argument that answers the research question step by step.",
        "",
        "Fields:",
        "- bottom_line: 2-4 sentences answering the question directly: the most "
        "specific conclusion the material supports, its direction and strength, "
        "then the single biggest caveat. Never an avoidance formula such as "
        "'the evidence is insufficient to draw a conclusion'.",
        "- verdicts: one per claim under test: claim (plain words), verdict "
        "(supported | partially_supported | not_supported | undetermined), "
        "reason (one sentence), refs.",
        "- steps: walk this spine in order, one step per dimension the material "
        "addresses (skip a dimension with no material):",
        *spine,
        "  Each step: dimension (the key above), question (the default question "
        "rephrased for THIS topic), answer (1-3 sentences that start from where "
        "the previous step left off, so the steps read as one chain), support "
        "(<=3 bullets: what backs the answer), limits (<=3: where the answer "
        "stops holding -- say whose conclusion it bounds), unknowns (<=3: the "
        "blindspots or unresolved conflicts that threaten THIS answer, "
        "strongest first), resolve (one sentence: the study or test that would "
        "settle the biggest unknown), refs (catalogue ids this step rests on).",
        "- cruxes: <=3 disagreements the evidence has not settled: question, "
        "positions (holders = expert roles as named in the catalogue; view), "
        "decider (the evidence that would settle it), refs.",
        "- next_steps: <=4 research actions in priority order: action, why, refs.",
        "",
        "Rules:",
        "- Every blindspot, boundary or conflict you mention sits inside the "
        "step whose conclusion it threatens; never list them on their own.",
        "- A dissenting expert's position stays visible: in a crux or in the "
        "step it concerns. Do not decide a dispute by counting experts.",
        "- Bullets are short: at most ~60 Chinese characters (or ~35 words). "
        "Plain, problem-oriented language.",
        "- No internal process vocabulary anywhere: seat, council, round, "
        "phase, precommitment, conditioned consensus, joint modeling, "
        "cross-examination, bounty, atomic claim (席位, 议会, 轮次, 预承诺, "
        "条件化共识, 联合建模, 交叉质询, 盲点悬赏, 原子主张, 本席).",
        "- refs are catalogue ids exactly as written (B3, L12, J2 ...). Cite "
        "the items each step rests on; uncited items are filed automatically, "
        "so do not try to cite everything.",
        "- Keep the whole answer under about 2500 Chinese characters.",
        "",
        "## Catalogue",
        "",
        *catalog_prompt_lines(items),
    ]
    return sanitize_export("\n".join(lines))


class _SynthesisProgress:
    """Announce the synthesis call on the process stream while it runs.

    Same contract as the seats' heartbeat (packages/council/deliberation.py):
    display-only rows, flushed through the writer's own session, and a broken
    flush degrades to a warning -- progress can never fail the synthesis.
    """

    def __init__(self, emit: ProcessEmit | None, flush: ProcessFlush | None) -> None:
        self._emit = emit
        self._flush = flush
        self._started = time.monotonic()
        self._heartbeat: asyncio.Task[None] | None = None

    async def _push(self, kind: str, payload: dict[str, object]) -> None:
        if self._emit is None:
            return
        self._emit(kind, payload)
        if self._flush is None:
            return
        try:
            await self._flush()
        except Exception:  # noqa: BLE001 -- display rows only
            logger.warning("synthesis progress flush failed", exc_info=True)

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(_SYNTHESIS_HEARTBEAT_SECONDS)
            await self._push(
                SYNTHESIS_WORKING,
                {"elapsed": int(time.monotonic() - self._started)},
            )

    async def start(self, mode: str) -> None:
        await self._push(SYNTHESIS_STARTED, {"mode": mode})
        if self._emit is not None:
            self._heartbeat = asyncio.create_task(self._beat())

    async def finish(self, outcome: SynthesisOutcome) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat
        await self._push(
            SYNTHESIS_DONE,
            {
                "outcome": (
                    "unavailable"
                    if not outcome.available
                    else "deterministic"
                    if outcome.fallback
                    else "model"
                ),
                "elapsed": int(time.monotonic() - self._started),
            },
        )


def _material_brief_lines(brief: ResearchBrief) -> list[str]:
    lines = [f"Research question: {brief.question}", ""]
    # Placeholder claims (关联主张：/因果主张：/…) only scope the council's
    # investigation; feeding them to the model as "confirmed atomic claims"
    # makes the paper echo the research question as if it were a finding.
    material_claims = [
        claim
        for claim in brief.confirmed_claims
        if not is_placeholder_statement(claim.statement)
    ]
    lines.append("### Confirmed atomic claims")
    for claim in material_claims:
        lines.append(
            f"- {claim.statement} (type: {claim.claim_type}; "
            f"falsification condition: {claim.falsification_condition})"
        )
    if not material_claims:
        lines.append("- (none)")

    lines.append("")
    lines.append("### Admitted findings (each bound to a Source)")
    for finding in brief.findings:
        payload = finding.payload
        statement = payload.get("finding_statement") or payload.get("statement")
        doi = payload.get("doi")
        lines.append(
            f"- {_as_str(statement)} (doi: {_as_str(doi)})"
        )
    if not brief.findings:
        lines.append("- (none)")

    lines.append("")
    lines.append("### Blindspots")
    for item in brief.blindspots:
        lines.append(f"- {_as_str(item.payload.get('statement'))}")
    if not brief.blindspots:
        lines.append("- (none)")

    lines.append("")
    lines.append("### Dissents (minority positions, preserved auditable)")
    lines += [
        f"- {_as_str(item.payload.get('statement'))}"
        for item in brief.dissents
    ]
    if not brief.dissents:
        lines.append("- (none)")

    lines.append("")
    lines.append("### Discriminating study suggestions")
    lines += [
        f"- {_as_str(item.payload.get('statement'))}"
        for item in brief.discriminating_studies
    ]
    if not brief.discriminating_studies:
        lines.append("- (none)")

    lines.append("")
    lines.append("### Limitations (already known, must appear in the paper)")
    lines += [f"- {item}" for item in brief.limitations]

    lines.append("")
    lines.append(
        f"### Evidence coverage: {brief.paper_count} papers, "
        f"{brief.independent_cluster_count} independent clusters; "
        f"{len(brief.unadmitted_events)} submissions refused by the evidence "
        "gate (audited, not in conclusions); "
        f"{len(set(brief.absent_seats))} seats absent in at least one round."
    )
    return lines


def _consensus_lines(consensus: dict[str, object]) -> list[str]:
    lines: list[str] = []
    text = consensus.get("conditional_consensus")
    if isinstance(text, str) and text:
        lines.append(f"Conditioned consensus: {text}")
    boundary = consensus.get("boundary_conditions")
    if isinstance(boundary, list):
        lines += [f"- Boundary: {item}" for item in boundary]
    conflicts = consensus.get("unresolved_conflicts")
    if isinstance(conflicts, list):
        lines += [f"- Unresolved conflict: {item}" for item in conflicts]
    falsifiable = consensus.get("falsification_conditions")
    if isinstance(falsifiable, list):
        lines += [f"- Falsification condition: {item}" for item in falsifiable]
    return lines


async def _load_consensus(session: AsyncSession, task_id: UUID) -> dict[str, object]:
    result = await session.execute(
        select(ScientificEventModel)
        .where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.event_type == "CONSENSUS_DRAFTED",
        )
        .order_by(ScientificEventModel.sequence.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {}
    payload = dict(row.payload)
    return {
        key: payload.get(key)
        for key in (
            "conditional_consensus",
            "boundary_conditions",
            "unresolved_conflicts",
            "falsification_conditions",
        )
    }


async def load_judgment_rows(
    session: AsyncSession, task_id: UUID
) -> tuple[JudgmentRow, ...]:
    """Every seat's FINAL_JUDGMENT, in ledger order."""
    result = await session.execute(
        select(ScientificEventModel)
        .where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.event_type == "FINAL_JUDGMENT",
        )
        .order_by(ScientificEventModel.sequence)
    )
    rows: list[JudgmentRow] = []
    for row in result.scalars():
        payload = dict(row.payload)
        seat = payload.get("seat")
        judgment = payload.get("final_judgment")
        if not isinstance(seat, str) or not isinstance(judgment, str) or not judgment:
            continue
        rows.append(
            JudgmentRow(
                seat=seat,
                text=judgment,
                confidence=payload.get("confidence"),
                has_dissent=payload.get("has_dissent") is True,
            )
        )
    return tuple(rows)


def _judgment_lines(
    rows: Sequence[JudgmentRow],
) -> tuple[tuple[str, object], ...]:
    """Judgments as the paper-review prompt reads them: text plus a
    confidence/dissent suffix."""
    judgments: list[tuple[str, object]] = []
    for row in rows:
        suffix = "（持异议）" if row.has_dissent else ""
        # A seat that did not report a confidence gets no number here. The
        # frontend already renders the same case as 未记录; an empty
        # "（置信度：）" would read as a value that failed to print.
        recorded = "未记录" if row.confidence is None else _as_str(row.confidence)
        judgments.append((row.seat, f"{row.text}（置信度：{recorded}{suffix}）"))
    return tuple(judgments)


def _understanding_lines(understanding: dict[str, object] | None) -> list[str]:
    """Render the paper-understanding summary as prompt material.

    When the understanding is missing (no model provider, parse failure,
    resumed run without a captured event), the report must say it could not
    critique the paper's content -- an honest gap, never an improvisation.
    """
    if understanding is None:
        return [
            "The paper-understanding step produced no summary (no model "
            "provider, an unparsable upload, or a failed call -- see the "
            "ledger). You MUST state in the report that the council could "
            "not verify the paper's content, and keep every critique "
            "explicitly conditional on that gap."
        ]
    lines = ["### Paper understanding (machine summary of the uploaded paper)"]
    title = understanding.get("title")
    if isinstance(title, str) and title:
        lines.append(f"Paper title: {title}")
    question = understanding.get("research_question")
    if isinstance(question, str) and question:
        lines.append(f"Research question: {question}")
    main_claims = understanding.get("main_claims")
    if isinstance(main_claims, (list, tuple)):
        for claim in main_claims:
            if not isinstance(claim, Mapping):
                continue
            statement = _as_str(claim.get("statement"))
            if not statement:
                continue
            support = claim.get("supporting_evidence")
            if isinstance(support, (list, tuple)) and support:
                lines.append(
                    f"- Claim: {statement} | paper's support: "
                    + "; ".join(_as_str(item) for item in support)
                )
            else:
                lines.append(f"- Claim: {statement} | paper's support: (none stated)")
    unverifiable = understanding.get("unverifiable")
    if isinstance(unverifiable, (list, tuple)) and unverifiable:
        lines.append(
            "Unverifiable from the text: "
            + "; ".join(_as_str(item) for item in unverifiable)
        )
    if understanding.get("truncated") is True:
        lines.append(
            "(The uploaded text was truncated for length; the summary may "
            "not cover the whole paper.)"
        )
    return lines


def _build_review_user_prompt(
    brief: ResearchBrief,
    consensus: dict[str, object],
    judgments: tuple[tuple[str, object], ...],
    understanding: dict[str, object] | None,
) -> str:
    lines = [
        "Write the final paper-review report for the council run described "
        "below. The council critiqued an uploaded paper; your report must: "
        "1) state what the paper argues (its research question, its main "
        "claims, and the evidence the paper itself offers for each); 2) list "
        "where the paper's argument is not rigorous (logical breaks, "
        "measurement problems, unfounded generalizations -- each tied to the "
        "claim it concerns); 3) list where its evidence is insufficient and "
        "what evidence would close the gap; 4) give concrete, more "
        "rigorous improvement suggestions; 5) conclude with an overall "
        "assessment. Ground every critique in the admitted findings and the "
        "confirmed claims; do not invent sources, numbers, or references "
        "that are not listed here. State uncertainties and limitations "
        "honestly -- a gap is a correct answer, a confident guess is not.",
        "",
    ]
    lines.extend(_material_brief_lines(brief))
    lines.append("")
    lines.extend(_understanding_lines(understanding))
    if consensus:
        lines.append("")
        lines.append("### Conditioned consensus (from joint modeling)")
        lines.extend(_consensus_lines(consensus))
    if judgments:
        lines.append("")
        lines.append("### Final judgments (seven seats, independent)")
        for seat, judgment in judgments:
            lines.append(f"- {seat}: {judgment}")
    lines.append("")
    lines.append(
        "`paper_overview` must reflect the paper-understanding summary above "
        "(or explicitly note what could not be verified). `rigor_issues`, "
        "`evidence_insufficiency`, and `improvement_suggestions` must each "
        "name the paper claim they concern in `claim_ref` (or be left empty "
        "when they concern the paper as a whole). `limitations` must state "
        "the limits of this review itself, including a missing paper "
        "understanding if the step produced none."
    )
    return sanitize_export("\n".join(lines))


def _parse_review_paper(payload: dict[str, object]) -> PaperReviewReport:
    """Parse a model payload into a PaperReviewReport, or raise on a broken
    shape -- same strictness as _parse_paper: a payload that still fails here
    is a real failure and must surface as FINAL_PAPER_FAILED, never as a
    half-filled report.
    """
    title = _as_str(payload.get("title"))
    if not title:
        raise ValueError("review payload missing title")
    conclusion = _as_str(payload.get("conclusion"))
    if not conclusion:
        raise ValueError("review payload missing conclusion")

    overview = payload.get("paper_overview")
    if not isinstance(overview, Mapping):
        raise ValueError("review payload missing paper_overview")
    research_question = _as_str(overview.get("research_question"))
    if not research_question:
        raise ValueError("review payload missing paper_overview.research_question")
    raw_claims = overview.get("main_claims")
    claims: list[ReviewClaim] = []
    if isinstance(raw_claims, (list, tuple)):
        for item in raw_claims:
            if not isinstance(item, Mapping):
                continue
            statement = _as_str(item.get("statement"))
            if not statement:
                continue
            support = item.get("supporting_evidence")
            claims.append(
                ReviewClaim(
                    statement=statement,
                    supporting_evidence=(
                        _strings(support) if support is not None else ()
                    ),
                )
            )

    def _issues(value: object) -> tuple[ReviewIssue, ...]:
        issues: list[ReviewIssue] = []
        if not isinstance(value, (list, tuple)):
            return ()
        for item in value:
            if not isinstance(item, Mapping):
                continue
            text = _as_str(item.get("issue"))
            if not text:
                continue
            ref = item.get("claim_ref")
            severity = item.get("severity")
            issues.append(
                ReviewIssue(
                    claim_ref=None if ref is None else _as_str(ref),
                    issue=text,
                    severity=None if severity is None else _as_str(severity),
                )
            )
        return tuple(issues)

    def _gaps(value: object) -> tuple[EvidenceGap, ...]:
        gaps: list[EvidenceGap] = []
        if not isinstance(value, (list, tuple)):
            return ()
        for item in value:
            if not isinstance(item, Mapping):
                continue
            missing = _as_str(item.get("missing_evidence"))
            if not missing:
                continue
            ref = item.get("claim_ref")
            suggested = item.get("suggested_evidence")
            gaps.append(
                EvidenceGap(
                    claim_ref=None if ref is None else _as_str(ref),
                    missing_evidence=missing,
                    suggested_evidence=(
                        None if suggested is None else _as_str(suggested)
                    ),
                )
            )
        return tuple(gaps)

    return PaperReviewReport(
        title=title,
        paper_overview=PaperOverview(
            title=(
                None
                if overview.get("title") is None
                else _as_str(overview.get("title"))
            ),
            research_question=research_question,
            main_claims=tuple(claims),
        ),
        rigor_issues=_issues(payload.get("rigor_issues")),
        evidence_insufficiency=_gaps(payload.get("evidence_insufficiency")),
        improvement_suggestions=_issues(payload.get("improvement_suggestions")),
        conclusion=conclusion,
        limitations=_strings(payload.get("limitations")),
        investigation_process=_strings(payload.get("investigation_process")),
    )


def _review_payload_dict(report: PaperReviewReport) -> dict[str, object]:
    """Serialize a PaperReviewReport to the ledger payload shape."""
    return {
        "title": report.title,
        "paper_overview": {
            "title": report.paper_overview.title,
            "research_question": report.paper_overview.research_question,
            "main_claims": [
                {
                    "statement": claim.statement,
                    "supporting_evidence": list(claim.supporting_evidence),
                }
                for claim in report.paper_overview.main_claims
            ],
        },
        "rigor_issues": [
            {
                "claim_ref": item.claim_ref,
                "issue": item.issue,
                "severity": item.severity,
            }
            for item in report.rigor_issues
        ],
        "evidence_insufficiency": [
            {
                "claim_ref": item.claim_ref,
                "missing_evidence": item.missing_evidence,
                "suggested_evidence": item.suggested_evidence,
            }
            for item in report.evidence_insufficiency
        ],
        "improvement_suggestions": [
            {"claim_ref": item.claim_ref, "issue": item.issue}
            for item in report.improvement_suggestions
        ],
        "conclusion": report.conclusion,
        "limitations": list(report.limitations),
        "investigation_process": list(report.investigation_process),
    }


def _fallback_review_report(
    brief: ResearchBrief,
    question: str,
    understanding: dict[str, object] | None,
    fallback_reason: str,
) -> PaperReviewReport:
    """Build a review-shaped, gap-explicit report without model inference."""
    raw = understanding or {}
    raw_claims = raw.get("main_claims")
    claims: list[ReviewClaim] = []
    if isinstance(raw_claims, (list, tuple)):
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            statement = _as_str(item.get("statement"))
            if not statement:
                continue
            claims.append(
                ReviewClaim(
                    statement=statement,
                    supporting_evidence=_strings(item.get("supporting_evidence")),
                )
            )
    paper_title = _as_str(raw.get("title")) or None
    research_question = _as_str(raw.get("research_question")) or question
    reason_text = f"确定性降级原因：{fallback_reason}"
    gaps = tuple(
        EvidenceGap(
            claim_ref=None,
            missing_evidence=limitation,
            suggested_evidence="补充可核验材料后重新审查该项。",
        )
        for limitation in brief.limitations
        if limitation.strip()
    )
    if not gaps:
        gaps = (
            EvidenceGap(
                claim_ref=None,
                missing_evidence=reason_text,
                suggested_evidence="恢复完整研究流程后重新生成审查。",
            ),
        )
    limitations = tuple(dict.fromkeys((*brief.limitations, reason_text)))
    return PaperReviewReport(
        title=f"论文审查（确定性降级）：{paper_title or question}",
        paper_overview=PaperOverview(
            title=paper_title,
            research_question=research_question,
            main_claims=tuple(claims),
        ),
        rigor_issues=(),
        evidence_insufficiency=gaps,
        improvement_suggestions=tuple(
            ReviewIssue(
                claim_ref=gap.claim_ref,
                issue=gap.suggested_evidence or "补充证据后重新审查。",
                severity=None,
            )
            for gap in gaps
        ),
        conclusion=(
            "本产物仅汇总最后一次持久化检查点中可读的信息；"
            "由于报告综合未完整完成，不能据此声称论文已通过严谨性审查。"
        ),
        limitations=limitations,
        investigation_process=(
            f"证据覆盖：{brief.paper_count} 篇论文，"
            f"{brief.independent_cluster_count} 个独立证据簇。",
            f"议会阶段：{_phase_coverage(brief)}。",
            reason_text,
        ),
    )


async def _emit_fallback_paper(
    session: AsyncSession,
    task_id: UUID,
    brief: ResearchBrief,
    consensus: Mapping[str, object],
    question: str,
    *,
    fallback_reason: str,
    is_review: bool = False,
    understanding: dict[str, object] | None = None,
    judgments: Sequence[JudgmentRow] = (),
    idempotency_key: str = _PAPER_IDEMPOTENCY_KEY,
    emergency: bool = False,
) -> SynthesisOutcome:
    """Write the task-type-correct deterministic fallback report."""
    if is_review:
        review = _fallback_review_report(
            brief, question, understanding, fallback_reason
        )
        stored_payload = _review_payload_dict(review)
    else:
        stored_payload = _paper_payload_dict(
            _fallback_integrated_paper(brief, consensus, question, judgments)
        )
    stored_payload.update(
        {
            "fallback": True,
            "fallback_reason": fallback_reason,
            "emergency": emergency,
        }
    )
    await SqlEventLedger(session).append(
        task_id,
        FINAL_PAPER_DRAFTED,
        stored_payload,
        idempotency_key,
    )
    return SynthesisOutcome(available=True, fallback=True)


async def synthesize_paper(
    session: AsyncSession,
    task_id: UUID,
    gateway: ModelGateway | None,
    output_language: str | None = None,
    *,
    emergency_reason: str | None = None,
    on_process: ProcessEmit | None = None,
    on_flush: ProcessFlush | None = None,
) -> SynthesisOutcome:
    """Run the synthesis model call and record its result in the ledger.

    Deep-research tasks produce a FinalPaper built around a reasoning map;
    paper-review tasks produce a PaperReviewReport (the same machinery,
    different schema and prompt -- see the round-7 branching below). Never
    raises for a model failure: a failed synthesis is recorded as a
    ``FINAL_PAPER_FAILED`` event and reported through
    :class:`SynthesisOutcome` so the task's terminal status stays a function
    of evidence gaps only.

    ``on_process``/``on_flush`` are the run's process-stream writer: when
    given, the live view shows the synthesis as a running step with a
    heartbeat instead of a silent REPORTING phase.
    """
    progress = _SynthesisProgress(on_process, on_flush)
    await progress.start("deterministic" if gateway is None else "model")
    outcome = SynthesisOutcome(available=False, reason="synthesis did not finish")
    try:
        outcome = await _synthesize(
            session,
            task_id,
            gateway,
            output_language,
            emergency_reason=emergency_reason,
        )
        return outcome
    finally:
        await progress.finish(outcome)


async def _synthesize(
    session: AsyncSession,
    task_id: UUID,
    gateway: ModelGateway | None,
    output_language: str | None,
    *,
    emergency_reason: str | None,
) -> SynthesisOutcome:
    task_id = canonical_uuid(task_id)
    task_query = await session.execute(
        select(ResearchTaskModel).where(
            ResearchTaskModel.task_id == task_id
        )
    )
    task = task_query.scalar_one_or_none()
    if task is None:
        logger.warning("synthesis skipped: task %s not found", task_id)
        return SynthesisOutcome(available=False, reason="task not found")

    language = output_language or task.output_language or "auto"
    if language == "auto":
        language = detect_output_language(task.question)

    brief = await ReportService(session).build(task_id)
    consensus = await _load_consensus(session, task_id)
    judgment_rows = await load_judgment_rows(session, task_id)
    is_review = getattr(task, "task_type", "deep_research") == "paper_review"
    understanding: dict[str, object] | None = None
    if is_review:
        from packages.papers.understanding import load_paper_understanding

        understanding = await load_paper_understanding(session, task_id)

    if gateway is None:
        # No model provider: nothing to call, nothing failed -- but the
        # researcher still asked a question and must see an integrated
        # conclusion. Round-9: assemble the paper from the brief alone so the
        # "综合论文尚未生成" stub never appears; the honest reason travels in
        # the fallback's investigation process.
        fallback_reason = emergency_reason or (
            "no model provider connected to the Model Gateway"
        )
        return await _emit_fallback_paper(
            session,
            task_id,
            brief,
            consensus,
            task.question,
            fallback_reason=fallback_reason,
            is_review=is_review,
            understanding=understanding,
            judgments=judgment_rows,
            idempotency_key=(
                _EMERGENCY_PAPER_IDEMPOTENCY_KEY
                if emergency_reason is not None
                else _PAPER_IDEMPOTENCY_KEY
            ),
            emergency=emergency_reason is not None,
        )
    directive = OUTPUT_LANGUAGE_DIRECTIVES.get(
        language, OUTPUT_LANGUAGE_DIRECTIVES["en"]
    )
    items: tuple[MapItem, ...] = ()
    if is_review:
        # The paper-understanding summary orients the report; a missing one
        # is fed to the prompt as an explicit gap the report must admit.
        system_prompt = (
            "You are the reporting synthesizer for a seven-seat research "
            "council that reviewed an uploaded paper. You integrate the "
            "council's admitted outputs into a paper-review report: what the "
            "paper argues, where its argument is not rigorous or well "
            "evidenced, and how to improve it. You are not an eighth "
            "scientist: you cast no judgment, you only integrate what the "
            "seven wrote. Every critique must trace to the materials you are "
            "given; never add new sources, numbers, or conclusions.\n"
            f"{directive}\n"
            "Reply only with the requested schema."
        )
        user_prompt = _build_review_user_prompt(
            brief, consensus, _judgment_lines(judgment_rows), understanding
        )
        output_schema = "PaperReviewReport"
    else:
        items = _brief_catalog(brief, consensus, judgment_rows)
        system_prompt = (
            f"{_MAP_SYSTEM_PROMPT}\n{directive}\nReply only with the requested schema."
        )
        user_prompt = _build_map_prompt(task.question, brief, items)
        output_schema = "ReasoningMap"

    request = ModelRequest(
        task_id=task_id,
        actor="report_synthesizer",
        purpose="FINAL_SYNTHESIS",
        model_class=ModelClass.STRONG_REASONING,
        messages=(
            ModelMessage(role="system", content=system_prompt),
            ModelMessage(role="user", content=user_prompt),
        ),
        output_schema=output_schema,
        evidence_refs=(
            # asyncpg returns its own UUID subclass, which ContractModel
            # rejects on purpose (packages/kernel/contracts.py) -- normalise
            # at the boundary like the worker does for confirmed claims.
            *(canonical_uuid(claim.claim_id) for claim in brief.confirmed_claims),
            *(canonical_uuid(item.node_id) for item in brief.findings),
        ),
    )

    try:
        audited = AuditedModelGateway(gateway, session)
        model_result = await audited.invoke(request)
        if model_result.schema_status == SchemaStatus.QUARANTINED:
            raise ValueError(
                "synthesis schema could not be repaired; output quarantined"
            )
        payload = dict(model_result.payload)
        paper: FinalPaper | PaperReviewReport = (
            _parse_review_paper(payload)
            if is_review
            else _map_paper(parse_model_map(payload, items), brief, task.question)
        )
    except Exception as error:  # noqa: BLE001 -- a model failure is reported, not raised
        reason = sanitize_export(str(error))[:500]
        logger.warning("paper synthesis failed: %s", reason)
        try:
            await SqlEventLedger(session).append(
                task_id,
                FINAL_PAPER_FAILED,
                {"reason": reason},
                _FAILED_IDEMPOTENCY_KEY,
            )
        except Exception as ledger_error:  # noqa: BLE001
            logger.error(
                "failed to record FINAL_PAPER_FAILED: %s", ledger_error
            )
        # Round-9 「最终论文总是整合结论」: a failed model call must not leave
        # the researcher with the "综合论文尚未生成" stub. Assemble the paper
        # from the brief alone so the integrated conclusion always exists; the
        # FINAL_PAPER_FAILED event above stays as the honest process record.
        return await _emit_fallback_paper(
            session,
            task_id,
            brief,
            consensus,
            task.question,
            fallback_reason=reason,
            is_review=is_review,
            understanding=understanding,
            judgments=judgment_rows,
        )

    stored_payload: dict[str, object] = (
        _review_payload_dict(paper)
        if isinstance(paper, PaperReviewReport)
        else _paper_payload_dict(paper)
    )
    await SqlEventLedger(session).append(
        task_id,
        FINAL_PAPER_DRAFTED,
        stored_payload,
        _PAPER_IDEMPOTENCY_KEY,
    )
    return SynthesisOutcome(available=True)


async def ensure_reasoning_map(
    session: AsyncSession,
    task_id: UUID,
    payload: Mapping[str, object] | None,
    *,
    brief: ResearchBrief | None = None,
    consensus: Mapping[str, object] | None = None,
) -> Mapping[str, object] | None:
    """Give a deep-research paper stored before the reasoning map existed a
    map, filed deterministically on read.

    Every completed task keeps its original FINAL_PAPER_DRAFTED event
    untouched (the ledger is append-only); the map is a read-side view over
    the same catalogue, marked ``derived_on_read`` so the workspace can say
    so. A paper whose own conclusion was model-written keeps it as the
    bottom line; the template consensus never becomes one. Any failure
    returns the payload as stored -- the legacy view still renders it.
    """
    if (
        payload is None
        or "paper_overview" in payload
        or isinstance(payload.get("reasoning_map"), Mapping)
    ):
        return payload
    try:
        task_id = canonical_uuid(task_id)
        if brief is None:
            brief = await ReportService(session).build(task_id)
        if consensus is None:
            consensus = await _load_consensus(session, task_id)
        items = _brief_catalog(
            brief, consensus, await load_judgment_rows(session, task_id)
        )
        bottom_line = meaningful_consensus(consensus.get("conditional_consensus"))
        if not bottom_line and payload.get("fallback") is not True:
            bottom_line = meaningful_consensus(payload.get("overall_conclusion"))
        rmap = deterministic_map(items, bottom_line=bottom_line)
    except Exception:  # noqa: BLE001 -- a read-side view must never 500 the workspace
        logger.warning(
            "could not derive a reasoning map for task %s", task_id, exc_info=True
        )
        return payload
    upgraded = dict(payload)
    upgraded["reasoning_map"] = {**map_to_payload(rmap), "derived_on_read": True}
    return upgraded


def paper_payload_to_dataclass(
    payload: dict[str, Any],
) -> FinalPaper | PaperReviewReport:
    """Parse a stored FINAL_PAPER_DRAFTED payload into its dataclass.

    Dispatches on the payload shape: ``paper_overview`` marks a
    paper-review report, everything else parses as a FinalPaper. Distinct
    from ``_parse_paper``/``_parse_review_paper`` only in provenance: this
    reads what was already stored, so a corrupted stored payload raises
    rather than renders as a partial paper.
    """
    if "paper_overview" in payload:
        return _parse_review_paper(payload)
    return _parse_paper(payload)


__all__ = [
    "FINAL_PAPER_DRAFTED",
    "FINAL_PAPER_FAILED",
    "SYNTHESIS_DONE",
    "SYNTHESIS_STARTED",
    "SYNTHESIS_WORKING",
    "SynthesisOutcome",
    "ensure_reasoning_map",
    "load_judgment_rows",
    "paper_payload_to_dataclass",
    "synthesize_paper",
]
