"""Synthesis step: one model call turns the council's outputs into a paper.

The council's REPORTING phase is deliberately empty (registry.py's
``run_reporting`` -- "the report is assembled from the graph, not from a
round"), and the Research Brief is a zero-model template over the graph and
ledger. The paper this module writes is different and complementary: it is
the *integrating* step the researcher asked for -- one strong model call that
reads the seven final judgments, the conditioned consensus, the admitted
findings, and the limitations, and writes a full paper (abstract, sections,
references, limitations, investigation process).

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

import logging
from collections.abc import Mapping, Sequence
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
    PaperOverview,
    PaperReference,
    PaperReviewReport,
    PaperSection,
    ReviewClaim,
    ReviewIssue,
    SynthesisOutcome,
)
from packages.reports.safety import sanitize_export
from packages.reports.service import ReportService, ResearchBrief
from packages.research.language import detect_output_language
from packages.research.models import ResearchTaskModel

logger = logging.getLogger(__name__)

# Ledger event names. Both stay out of NODE_EVENT_TYPES, so the projector
# marks them process_only: the paper is auditable history, not evidence.
FINAL_PAPER_DRAFTED = "FINAL_PAPER_DRAFTED"
FINAL_PAPER_FAILED = "FINAL_PAPER_FAILED"

_PAPER_IDEMPOTENCY_KEY = "REPORTING:final_paper"
_FAILED_IDEMPOTENCY_KEY = "REPORTING:final_paper_failed"


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

    return FinalPaper(
        title=title,
        abstract=abstract,
        sections=tuple(sections),
        references=references,
        limitations=limitations,
        investigation_process=process,
    )


def _material_brief_lines(brief: ResearchBrief) -> list[str]:
    lines = [f"Research question: {brief.question}", ""]
    lines.append("### Confirmed atomic claims")
    for claim in brief.confirmed_claims:
        lines.append(
            f"- {claim.statement} (type: {claim.claim_type}; "
            f"falsification condition: {claim.falsification_condition})"
        )
    if not brief.confirmed_claims:
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


async def _load_final_judgments(
    session: AsyncSession, task_id: UUID
) -> tuple[tuple[str, object], ...]:
    result = await session.execute(
        select(ScientificEventModel)
        .where(
            ScientificEventModel.task_id == task_id,
            ScientificEventModel.event_type == "FINAL_JUDGMENT",
        )
        .order_by(ScientificEventModel.sequence)
    )
    judgments: list[tuple[str, object]] = []
    for row in result.scalars():
        payload = dict(row.payload)
        seat = payload.get("seat")
        if not isinstance(seat, str):
            continue
        judgment = payload.get("final_judgment")
        if not isinstance(judgment, str) or not judgment:
            continue
        confidence = payload.get("confidence")
        dissent = payload.get("has_dissent")
        suffix = " [DISSENT]" if dissent is True else ""
        judgments.append(
            (
                seat,
                f"{judgment} (confidence: {_as_str(confidence)}{suffix})",
            )
        )
    return tuple(judgments)


def _build_user_prompt(
    brief: ResearchBrief,
    consensus: dict[str, object],
    judgments: tuple[tuple[str, object], ...],
) -> str:
    lines = [
        "Write the final research paper for the council run described below. ",
        "Integrate the seven scientists' positions and the conditioned ",
        "consensus into one document. Ground every claim in the admitted ",
        "findings and the confirmed claims; do not invent sources, numbers, ",
        "or references that are not listed here. State uncertainties and ",
        "limitations honestly -- a gap is a correct answer, a confident ",
        "guess is not.",
        "",
    ]
    lines.extend(_material_brief_lines(brief))
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
        "The paper's `sections` must cover, at minimum: background / methods "
        "(how the council investigated), findings with evidence, controversy "
        "and dissent, conclusions and limitations. `references` must cite the "
        "source ids/DOIs of the admitted findings; every `id` in references "
        "must be one of the finding/source ids present in the materials above. "
        "`investigation_process` describes how the council ran (phases, "
        "absences, refusals) as plain facts."
    )
    return sanitize_export("\n".join(lines))


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


async def synthesize_paper(
    session: AsyncSession,
    task_id: UUID,
    gateway: ModelGateway | None,
    output_language: str | None = None,
) -> SynthesisOutcome:
    """Run the synthesis model call and record its result in the ledger.

    Deep-research tasks produce a FinalPaper; paper-review tasks produce a
    PaperReviewReport (the same machinery, different schema and prompt --
    see the round-7 branching below). Never raises for a model failure: a
    failed synthesis is recorded as a ``FINAL_PAPER_FAILED`` event and
    reported through :class:`SynthesisOutcome` so the task's terminal status
    stays a function of evidence gaps only.
    """
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
    judgments = await _load_final_judgments(session, task_id)

    if gateway is None:
        # No model provider: nothing to call, nothing failed -- the honest
        # state is "no synthesis attempted". The presentation layer derives
        # the reason from the brief's absent seats.
        return SynthesisOutcome(
            available=False,
            reason="no model provider connected to the Model Gateway",
        )

    is_review = getattr(task, "task_type", "deep_research") == "paper_review"
    directive = OUTPUT_LANGUAGE_DIRECTIVES.get(
        language, OUTPUT_LANGUAGE_DIRECTIVES["en"]
    )
    if is_review:
        # The paper-understanding summary orients the report; a missing one
        # (no model provider on the first pass, parse failure, resumed run
        # without a captured event) is fed to the prompt as an explicit gap
        # the report must admit.
        from packages.papers.understanding import load_paper_understanding

        understanding = await load_paper_understanding(session, task_id)
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
            brief, consensus, judgments, understanding
        )
        output_schema = "PaperReviewReport"
    else:
        system_prompt = (
            "You are the reporting synthesizer for a seven-seat "
            "research council. You integrate the council's admitted "
            "outputs into a single final paper. You are not an eighth "
            "scientist: you cast no judgment, you only integrate what "
            "the seven wrote. Every claim in the paper must trace to "
            "the materials you are given; never add new sources, "
            "numbers, or conclusions.\n"
            f"{directive}\n"
            "Reply only with the requested schema."
        )
        user_prompt = _build_user_prompt(brief, consensus, judgments)
        output_schema = "FinalPaper"

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
            _parse_review_paper(payload) if is_review else _parse_paper(payload)
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
        return SynthesisOutcome(available=False, reason=reason)

    stored_payload: dict[str, object] = (
        _review_payload_dict(paper)
        if isinstance(paper, PaperReviewReport)
        else {
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
        }
    )
    await SqlEventLedger(session).append(
        task_id,
        FINAL_PAPER_DRAFTED,
        stored_payload,
        _PAPER_IDEMPOTENCY_KEY,
    )
    return SynthesisOutcome(available=True)


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
    "SynthesisOutcome",
    "paper_payload_to_dataclass",
    "synthesize_paper",
]
