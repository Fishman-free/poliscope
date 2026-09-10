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
    Standpoint,
    SynthesisOutcome,
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

# Internal seat ids must never reach a reader: the deterministic fallback paper
# (and its standpoint headings) used to print raw ids such as
# "缺席席位：theory_builder". Unknown values keep the raw id rather than guess.
SEAT_DISPLAY_NAMES: dict[str, str] = {
    "theory_builder": "理论建构者",
    "causal_scientist": "因果推断专家",
    "measurement_scientist": "测量与构念专家",
    "replication_scientist": "统计与复现专家",
    "boundary_scientist": "边界与情境专家",
    "adversarial_falsifier": "对抗性证伪者",
    "evidence_auditor": "证据与溯源审计员",
}


def _seat_display(seat: object) -> str:
    text = str(seat)
    return SEAT_DISPLAY_NAMES.get(text, text)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value if item is not None and str(item))


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def _fallback_references(brief: ResearchBrief) -> tuple[PaperReference, ...]:
    """References for the deterministic fallback paper.

    The fallback has no model to resolve paper titles, so it reuses exactly
    the literature the council already worked from: every admitted finding's
    DOI, with the finding statement as the in-list label when no paper title
    is available. The DOI stays clickable in the renderer, which is the only
    honest reference an offline summary can produce -- it never invents a
    title (用户点6h：论文总结部分补充参考文献，复用思考链路已引文献).
    """
    refs: list[PaperReference] = []
    seen: set[str] = set()
    for item in brief.findings:
        payload = item.payload
        doi = payload.get("doi")
        doi_text = _as_str(doi) if doi is not None else ""
        statement = _as_str(
            payload.get("finding_statement") or payload.get("statement")
        ).strip()
        key = doi_text or str(item.node_id)
        if key in seen:
            continue
        seen.add(key)
        title = statement or doi_text or str(item.node_id)
        refs.append(
            PaperReference(
                id=str(item.node_id),
                title=title[:120],
                doi=doi_text or None,
            )
        )
    return tuple(refs)


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


def _finding_references(brief: ResearchBrief) -> tuple[PaperReference, ...]:
    """Build the fallback paper's reference list from admitted findings.

    Round-24 「论文总结补充参考文献」: the deterministic fallback paper used to
    ship an empty reference list even when the council had admitted findings
    anchored to real DOIs. Each finding is the council's already-cited take on
    one study, and its DOI is that study's identifier -- reuse it instead of
    inventing titles. The title fallback is the DOI itself, which stays honest
    (a DOI is a resolvable identifier; a guessed title would not be).
    """
    references: list[PaperReference] = []
    seen: set[str] = set()
    for item in brief.findings:
        doi = item.payload.get("doi")
        if not isinstance(doi, str) or not doi.strip():
            continue
        doi = doi.strip()
        if doi in seen:
            continue
        seen.add(doi)
        title = item.payload.get("source_title") or item.payload.get("title")
        references.append(
            PaperReference(
                id=str(item.node_id),
                title=(
                    title if isinstance(title, str) and title.strip()
                    else f"DOI {doi}"
                ),
                doi=doi,
            )
        )
    return tuple(references)


def _fallback_integrated_paper(
    brief: ResearchBrief,
    consensus: dict[str, object],
    question: str,
    judgments: tuple[tuple[str, object], ...] = (),
) -> FinalPaper:
    """Assemble an integrated paper from the brief alone, no model involved.

    Round-9 「最终论文总是整合结论」: when the synthesis model call fails or is
    quarantined (a weak vendor, a transient outage), the task must still end
    with a readable integrated conclusion -- the researcher asked a question
    and must see an answer, not the "综合论文尚未生成" stub (CLAUDE.md 7: a gap
    is reported honestly, but the integration conclusion itself is a
    first-class product, not a privilege of a healthy model). Every section is
    assembled from the same ``ResearchBrief`` the model would have been fed,
    so this fallback and the model path can never disagree about what the
    council concluded.

    ``consensus`` is optional material (absent on a run where joint modeling
    did not complete); every other field comes straight off the brief.
    Round-12 「整合结论详细化」: ``judgments`` (the seven seats' independent
    final judgments from the ledger) feeds the standpoints section -- each
    side named with its stated weakness -- and the overall conclusion, so the
    deterministic path is just as explicit about who argued what as the model
    path.
    """
    # Placeholder claims (关联主张：/因果主张：/…) only scope the council's
    # investigation; they are not findings and must never headline the paper.
    # claim_type renders in Chinese (因果/相关/…) so no English token reaches
    # the reader.
    confirmed_lines = [
        f"- {claim.statement}（类型：{claim_type_label(claim.claim_type)}；"
        f"证伪条件：{claim.falsification_condition}）"
        for claim in brief.confirmed_claims
        if not is_placeholder_statement(claim.statement)
    ]
    # Findings store their text under `finding_statement` (finding_extraction.py);
    # `statement` is the older key kept as a fallback so a finding never
    # renders as its bare node_id UUID.
    finding_lines = [
        f"- {_as_str(item.payload.get('finding_statement') or item.payload.get('statement') or item.node_id)}"
        for item in brief.findings
    ]
    blindspot_lines = [
        f"- {_as_str(item.payload.get('statement') or item.node_id)}"
        for item in brief.blindspots
    ]
    dissent_lines = [
        f"- {_as_str(item.payload.get('statement') or item.node_id)}"
        for item in brief.dissents
    ]
    consensus_lines = _consensus_lines_zh(consensus)

    sections: list[PaperSection] = []

    background: list[str] = [
        f"研究问题：{question}",
        "",
        "本文围绕研究问题，汇总已有证据支持的主要结论、研究之间的关键分歧，以及当前证据的局限。",
    ]
    if confirmed_lines:
        background.append("")
        background.append("已有证据支持的主要结论：")
        background.extend(confirmed_lines)
    sections.append(
        PaperSection(heading="研究背景", paragraphs=tuple(background))
    )

    if finding_lines:
        sections.append(
            PaperSection(
                heading="研究结果",
                paragraphs=(
                    "以下结果均有可核验的文献来源：",
                    *finding_lines,
                ),
            )
        )

    if consensus_lines:
        sections.append(
            PaperSection(heading="综合判断", paragraphs=tuple(consensus_lines))
        )

    controversy: list[str] = []
    if dissent_lines:
        controversy.append("少数不同意见（均如实保留，不并入主流结论）：")
        controversy.extend(dissent_lines)
    if blindspot_lines:
        controversy.append("")
        controversy.append("现有研究尚未回答的问题：")
        controversy.extend(blindspot_lines)
    if not controversy:
        controversy.append("未记录到明显分歧或尚未回答的问题。")
    sections.append(
        PaperSection(heading="分歧与尚未回答的问题", paragraphs=tuple(controversy))
    )

    # Round-24 「各方观点与缺陷必须为实质内容」: 确定性兜底没有模型可生成
    # 逐席位的论证细节，但已采纳发现与终审结论都在账本里——把每席终审作为
    # 「观点」、已采纳发现作为「支撑证据」、是否保留异议作为「缺陷/终审情况」，
    # 逐块纵向呈现，而不是「该观点与主流结论不同…见正文」这类流程套话。
    standpoints: tuple[Standpoint, ...] = ()
    if judgments:
        built_standpoints: list[Standpoint] = []
        for seat, judgment in judgments:
            judgment_text = str(judgment)
            has_dissent = "持异议" in judgment_text
            weakness = (
                "终审保留异议：该观点未并入主流结论，作为独立意见保留。"
                if has_dissent
                else "终审未保留异议。"
            )
            built_standpoints.append(
                Standpoint(
                    seat=_seat_display(seat),
                    position=judgment_text,
                    weakness=weakness,
                    supporting_evidence=tuple(
                        line.lstrip("- ")
                        for line in finding_lines[:4]
                        if isinstance(line, str)
                    ),
                )
            )
        standpoints = tuple(built_standpoints)
    overall: list[str] = []
    if consensus_lines:
        overall.append("综合各方面证据，可以得到如下总体判断：")
        overall.extend(consensus_lines)
    else:
        overall.append("本次研究未形成统一结论。不同立场的观点、各自依赖的证据与局限如下：")
        if standpoints:
            overall.append("")
            for standpoint in standpoints:
                overall.append(
                    f"- {standpoint.position}"
                )
                overall.append(
                    f"  局限：{standpoint.weakness}"
                )
        else:
            overall.append(
                "现有材料不足以展开任何明确立场；需要更多原始研究才能判断。"
            )
    sections.append(
        PaperSection(heading="总体结论", paragraphs=tuple(overall))
    )

    limitations = list(brief.limitations)
    if brief.has_gaps:
        gap_parts: list[str] = []
        if brief.absent_seats:
            gap_parts.append(f"{len(set(brief.absent_seats))} 席缺席")
        if brief.failed_phases:
            gap_parts.append(f"{len(brief.failed_phases)} 个阶段失败")
        if brief.skipped_phases:
            gap_parts.append(f"{len(brief.skipped_phases)} 个阶段未执行")
        if brief.unadmitted_events:
            gap_parts.append(f"{len(brief.unadmitted_events)} 项证据被证据门拒绝")
        limitations.append(
            "；".join(gap_parts) + "。以下结论仅基于已完成的研究部分，需谨慎对待。"
        )
    if not consensus_lines and standpoints:
        # 未达成共识时：在结论与局限板块逐个列出每个立场的观点、证据与局限，
        # 让读者不必翻审计轨迹就能看到每一方靠什么、缺什么。
        per_position: list[str] = [
            "未形成统一结论。以下逐一列出各方观点及其证据与局限："
        ]
        for standpoint in standpoints:
            per_position.append("")
            per_position.append(f"观点：{standpoint.position}")
            per_position.append(f"局限：{standpoint.weakness}")
        per_position.append("")
        per_position.extend(f"- {item}" for item in limitations)
        sections.append(
            PaperSection(
                heading="结论与局限",
                paragraphs=tuple(per_position),
            )
        )
    else:
        sections.append(
            PaperSection(
                heading="结论与局限",
                paragraphs=(*limitations,),
            )
        )

    process: list[str] = [
        f"证据覆盖：{brief.paper_count} 篇论文，"
        f"{brief.independent_cluster_count} 个独立证据簇。",
        f"议会阶段：{_phase_coverage(brief)}。",
    ]
    if brief.absent_seats:
        process.append(
            f"缺席席位：{', '.join(_seat_display(seat) for seat in brief.absent_seats)}。"
        )
    # 参考文献复用思考链路已引用的文献：确定性兜底无法生成新引用，但可以
    # 把已进入证据图的发现原文（带 DOI）作为可点击引用列出（round-12 反馈）。
    references: tuple[PaperReference, ...] = _finding_references(brief)

    title = f"关于「{question}」的研究整合结论"
    overall_conclusion = _as_str(consensus.get("conditional_consensus")) or (
        "本次研究未形成统一结论；各方观点、依赖的证据与局限已在上文逐一列出。"
    )
    # The overall conclusion rests on the admitted findings; list them plainly
    # (the same statements the sections above already show, capped so the
    # field stays a summary, not a second copy of the paper).
    conclusion_evidence = tuple(finding_lines[:6])
    # 社科论文摘要规范（round-19）：研究问题 → 方法证据 → 主要发现 → 结论，
    # 第三人称、结论明确；覆盖全部主要主张/发现/争议，而非只取前 2-3 条。
    abstract_parts = [f"**【研究问题】**：针对「{question}」的证据链整合与审视。\n\n"]
    abstract_parts.append(
        f"**【研究方法与证据】**：基于 {brief.paper_count} 篇论文、"
        f"{brief.independent_cluster_count} 个独立证据簇的系统性审视。\n\n"
    )
    if confirmed_lines or finding_lines:
        abstract_parts.append("**【主要发现】**：\n")
        if confirmed_lines:
            shown = confirmed_lines[:5]
            for idx, line in enumerate(shown, 1):
                abstract_parts.append(f"{idx}. {line.lstrip('- ')}\n")
            if len(confirmed_lines) > len(shown):
                abstract_parts.append(
                    f"（共 {len(confirmed_lines)} 项主张，此处列前 {len(shown)} 项，其余见正文）\n"
                )
        elif finding_lines:
            shown = finding_lines[:5]
            for idx, line in enumerate(shown, 1):
                abstract_parts.append(f"{idx}. {line.lstrip('- ')}\n")
            if len(finding_lines) > len(shown):
                abstract_parts.append(
                    f"（共 {len(finding_lines)} 项发现，此处列前 {len(shown)} 项，其余见正文）\n"
                )
        abstract_parts.append("\n")
    if dissent_lines:
        shown = dissent_lines[:4]
        disagreement = "；".join(line.lstrip("- ") for line in shown)
        if len(dissent_lines) > len(shown):
            disagreement += f"（共 {len(dissent_lines)} 项异议）"
        abstract_parts.append(f"**【关键分歧】**：{disagreement}。\n\n")
    if blindspot_lines:
        shown = blindspot_lines[:3]
        blinds = "；".join(line.lstrip("- ") for line in shown)
        if len(blindspot_lines) > len(shown):
            blinds += f"（共 {len(blindspot_lines)} 项）"
        abstract_parts.append(f"**【尚未回答的问题】**：{blinds}。\n\n")
    consensus_text = consensus.get("conditional_consensus")
    if isinstance(consensus_text, str) and consensus_text and not consensus_text.startswith("综合结论以"):
        abstract_parts.append(f"**【结论】**：{consensus_text}\n")
    elif confirmed_lines:
        abstract_parts.append(
            f"**【结论】**：综合实证证据，{confirmed_lines[0].lstrip('- ')}。\n"
        )
    elif finding_lines:
        abstract_parts.append(
            f"**【结论】**：综合实证证据，{finding_lines[0].lstrip('- ')}。\n"
        )
    else:
        # Round-24 「结论不得用回避套话」: 不能落入「现有实证证据尚不足以形成
        # 统一因果推断」这类空话——研究者要的不是统一因果推断，而是各方观点与
        # 全部影响因素都被表述出来。此处把已记录在案的盲点（尚未回答的问题）
        # 与局限一并列出，让结论落在「已知什么、还未知什么」上，而不是一句
        # 不表态的否定。
        if blindspot_lines:
            abstract_parts.append(
                "**【结论】**：现有证据可确认的结论有限；"
                "当前尚未回答的问题主要包括："
                + "；".join(line.lstrip("- ") for line in blindspot_lines[:3])
                + "。\n"
            )
        elif limitations:
            abstract_parts.append(
                "**【结论】**：在现有证据范围内，结论受以下局限约束："
                + "；".join(limitations[:3])
                + "。\n"
            )
        else:
            abstract_parts.append(
                "**【结论】**：本轮未记录到可支撑明确结论的已采纳发现；"
                "需要更多原始研究方能判断。\n"
            )
    abstract = "".join(abstract_parts)
    return FinalPaper(
        title=title,
        abstract=abstract,
        sections=tuple(sections),
        references=references,
        limitations=tuple(limitations),
        investigation_process=tuple(process),
        standpoints=standpoints,
        overall_conclusion=overall_conclusion,
        conclusion_evidence=conclusion_evidence,
    )


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


def _consensus_lines_zh(consensus: dict[str, object]) -> list[str]:
    """Chinese rendering of the synthesis material for the deterministic
    fallback paper (``_consensus_lines`` stays English on purpose: it feeds
    the English model prompt)."""
    lines: list[str] = []
    text = consensus.get("conditional_consensus")
    if isinstance(text, str) and text:
        lines.append(f"综合判断：{text}")
    boundary = consensus.get("boundary_conditions")
    if isinstance(boundary, list):
        lines += [f"- 适用边界：{item}" for item in boundary]
    conflicts = consensus.get("unresolved_conflicts")
    if isinstance(conflicts, list):
        lines += [f"- 尚未解决的分歧：{item}" for item in conflicts]
    falsifiable = consensus.get("falsification_conditions")
    if isinstance(falsifiable, list):
        lines += [f"- 可被推翻的条件：{item}" for item in falsifiable]
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
        suffix = "（持异议）" if dissent is True else ""
        judgments.append(
            (
                seat,
                f"{judgment}（置信度：{_as_str(confidence)}{suffix}）",
            )
        )
    return tuple(judgments)


def _build_user_prompt(
    brief: ResearchBrief,
    consensus: dict[str, object],
    judgments: tuple[tuple[str, object], ...],
) -> str:
    lines = [
        "Write the final research paper for the research run described below.",
        "The reader is an ordinary educated academic in the field who knows",
        "NOTHING about the software system that produced the evidence. Write",
        "for THAT reader: explain the QUESTION first, then what the evidence",
        "says, where competent studies disagree, and what can be concluded.",
        "Integrate the expert positions and the overall synthesis into one",
        "coherent document. Ground every claim in the admitted findings and",
        "confirmed claims listed below; never invent sources, numbers, or",
        "references that are not listed here. State uncertainties and",
        "limitations honestly -- a gap is a correct answer, a confident guess",
        "is not.",
        "",
        "TYPOGRAPHY & READABILITY REQUIREMENT (critical): do NOT output dense walls",
        "of unformatted text. Break long thoughts into crisp paragraphs. Use blank lines",
        "between paragraphs. Use **bold** for key concepts, factor names, effect sizes,",
        "and takeaways. Use bullet points or numbered lists where comparing factors or",
        "hypotheses. Academic prose must be clear, well-structured, and effortless to read.",
        "",
        "LANGUAGE RULE (critical): write plain, problem-oriented prose. The",
        "internal vocabulary below is STRICTLY FORBIDDEN everywhere (including headings):",
        "'atomic claim', 'claim bifurcation', 'dissent timeline', 'debate capsule',",
        "'blindspot bounty', 'seat', 'council', 'precommitment/precommitted',",
        "'conditioned consensus', 'joint modeling', 'cross-examination', 'evidence exchange',",
        "'final rejudgment', 'round/phase', 'theory_builder', 'causal_scientist',",
        "'measurement_scientist', 'replication_scientist', 'boundary_scientist',",
        "'adversarial_falsifier', 'evidence_auditor', and their Chinese equivalents",
        "(原子主张, 主张分叉, 异议时间线, 争论胶囊, 盲点悬赏, 席位, 议会,",
        "预承诺, 条件化共识, 联合建模, 交叉质询, 证据交换, 最终复判, 轮次, 缺席席位).",
        "DO NOT output phrases like 'conditional on 2 claims', 'bounded by 41 conditions',",
        "or list system roles. Use genuine scientific terminology.",
        "",
        "ABSTRACT (follow a Chinese social-science journal abstract, in the third person;"
        "do NOT use '本文' or '作者'; the conclusion must be explicit and definite):"
        "structure the abstract in this exact order --"
        "1. **【研究问题与目的】**: one sentence naming the research question and why it matters."
        "2. **【研究方法与证据】**: the analytic approach and the evidence base the paper rests on"
        "   (how many independent studies, what kind of evidence)."
        "3. **【主要发现】**: state the concrete findings explicitly and completely -- this is the"
        "   body and longest part of the abstract. List EVERY major finding with its direction and"
        "   strength, never as a vague hint."
        "4. **【结论与启示】**: the definite conclusion drawn from the findings, stated explicitly"
        "   and concretely -- name the actual answer, never hedge with '具有重要理论意义' filler."
        "Use Markdown bold headings and clean paragraph breaks. 300-500 Chinese characters"
        "(or 250-400 words).",
        "",
        "The paper MUST preserve the controversy, not blend it into one voice.",
        "In the standpoints field, write one entry per distinct scientific",
        "position that emerged -- group experts holding the same position",
        "rather than forcing one row per expert. For each position: state it in",
        "full (not a slogan); name where it is weak or contested (weakness);",
        "list the admitted evidence supporting it (supporting_evidence --",
        "quote the finding/claim statements below, never invent a source); and",
        "say how it differs from the other positions (disagreement).",
        "SUBSTANCE RULE (critical): every standpoint's position, weakness,",
        "supporting_evidence and disagreement must be REAL scientific content",
        "-- the viewpoint itself, the admitted findings backing it, what the",
        "council retrieved and judged for it, where its evidence is weak, and",
        "how the final rejudgment treated it. FORBIDDEN filler such as 'two",
        "confirmed claims merely restate the research question', 'this view",
        "differs from the majority; see the disagreements section', or any",
        "sentence describing the PROCESS instead of the POSITION. If a position",
        "rests on no admitted finding, say exactly that and why.",
        "",
        "CONCLUSIONS: state clearly whether the evidence supports an overall",
        "conclusion and what it is (overall_conclusion), and list the admitted",
        "evidence it rests on (conclusion_evidence). If NO overall conclusion",
        "was reached -- the sides disagree or the evidence is too weak -- say",
        "so explicitly, and in the conclusions-and-limitations section list",
        "EACH major position with its supporting evidence and its limitations",
        "as separate items, so a reader can see every viewpoint's evidence and",
        "limits without any process record. Never hide a minority position",
        "behind 'no consensus was reached'.",
        "DO NOT refuse to take a position: even when viewpoints diverge or",
        "boundaries are drawn, overall_conclusion MUST state the most confident,",
        "specific conclusion the admitted evidence supports -- name the actual",
        "answer, its direction and its strength -- and only THEN mention",
        "remaining disagreements briefly at the end. A sentence like 'existing",
        "evidence is insufficient to form a unified causal inference' or 'no",
        "unified conclusion can be drawn' is an avoidance formula, not a",
        "conclusion: the reader asked what all sides' views and all influencing",
        "factors are, so state them concretely.",
        "",
        "investigation_process is the ONLY field allowed to narrate how the",
        "research ran (what was retrieved, what was refused, which steps were",
        "absent or failed), and the ONLY field where the internal vocabulary",
        "above may appear. Name absences, refusals and unresolved conflicts as",
        "facts. Be detailed; do not collapse dissenting positions into the",
        "majority view.",
        "",
    ]
    lines.extend(_material_brief_lines(brief))
    if consensus:
        lines.append("")
        lines.append("### Overall synthesis material")
        lines.extend(_consensus_lines(consensus))
    if judgments:
        lines.append("")
        lines.append("### Independent final positions (group by scientific view)")
        for seat, judgment in judgments:
            lines.append(f"- {seat}: {judgment}")
    lines.append("")
    lines.append(
        "The paper's `sections` must be problem-driven, in this order, with "
        "ordinary academic headings (no internal vocabulary in headings): "
        "(1) Background: the question, why it matters, and what existing "
        "evidence already supports; "
        "(2) Results & Key Factors: what the admitted evidence shows, detailed breakdown "
        "of major contributing factors, their relative impacts/magnitudes, and specific findings; "
        "(3) Points of disagreement: where and why studies or positions "
        "diverge, unresolved conflicts, and questions evidence cannot yet answer; "
        "(4) Overall assessment: what can be concluded and under what conditions; "
        "(5) Conclusions and limitations side by side -- if no overall "
        "conclusion was reached, list every major position here as its own "
        "item with supporting evidence and limitations. "
        "APPLICABILITY-BOUNDARY RULE (critical): every limitation or boundary "
        "statement MUST first name WHOSE conclusion it bounds (which finding / "
        "claim / position it applies to), then state the bound itself. A bare "
        "'applies only to X' without naming its owner is unusable. "
        "ORGANIZATION RULE (critical): boundaries and blindspots MUST be "
        "organised vertically BY POSITION -- one position per block with its "
        "own boundaries and blindspots listed under it -- never pooled into a "
        "single global caveat dump. "
        "LOGICAL FLOW (critical): the sections must read as ONE continuous argument, not a "
        "stack of disconnected notes. Each section must open by connecting to the previous one "
        "and close by setting up the next. Do not jump between topics -- finish one point "
        "before starting the next. Every conclusion must follow from the findings just "
        "presented. "
        "EVERY paragraph in sections must use clean formatting: short readable chunks, "
        "bullet points where appropriate, and **bold** for key concepts. "
        "`references` must cite the source ids/DOIs of the admitted findings; "
        "every `id` in references must be one of the finding/source ids "
        "present in the materials above. `investigation_process` is a factual "
        "timeline describing the research steps without robotic system dumps."
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
    consensus: dict[str, object],
    question: str,
    *,
    fallback_reason: str,
    is_review: bool = False,
    understanding: dict[str, object] | None = None,
    judgments: tuple[tuple[str, object], ...] = (),
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
        paper = _fallback_integrated_paper(
            brief, consensus, question, judgments=judgments
        )
        stored_payload = {
            "title": paper.title,
            "abstract": paper.abstract,
            "sections": [
                {
                    "heading": section.heading,
                    "paragraphs": list(section.paragraphs),
                }
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
                    "supporting_evidence": list(
                        standpoint.supporting_evidence
                    ),
                    "disagreement": standpoint.disagreement,
                }
                for standpoint in paper.standpoints
            ],
            "overall_conclusion": paper.overall_conclusion,
            "conclusion_evidence": list(paper.conclusion_evidence),
        }
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
    return SynthesisOutcome(available=True)


async def synthesize_paper(
    session: AsyncSession,
    task_id: UUID,
    gateway: ModelGateway | None,
    output_language: str | None = None,
    *,
    emergency_reason: str | None = None,
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
            judgments=judgments,
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
            "numbers, or conclusions. Write for an ordinary academic "
            "reader who knows nothing about this system: plain, "
            "problem-oriented prose, no internal process jargon (seats, "
            "rounds, precommitment, conditioned consensus, atomic claims) "
            "anywhere except investigation_process; the abstract must "
            "summarise question, findings, disagreements and conclusion by "
            "itself.\n"
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
            judgments=judgments,
        )

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
            "standpoints": [
                {
                    "seat": standpoint.seat,
                    "position": standpoint.position,
                    "weakness": standpoint.weakness,
                    "supporting_evidence": list(
                        standpoint.supporting_evidence
                    ),
                    "disagreement": standpoint.disagreement,
                }
                for standpoint in paper.standpoints
            ],
            "overall_conclusion": paper.overall_conclusion,
            "conclusion_evidence": list(paper.conclusion_evidence),
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
