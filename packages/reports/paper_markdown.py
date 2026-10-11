"""Markdown rendering for the synthesised final paper.

Same section-order rule as the Research Brief renderer: limitations sit
immediately after the conclusions, because CLAUDE.md 11 requires them side by
side and a reader who stops halfway must not have read only the findings.
The stub renderer exists so a download request always returns an honest
document -- a missing paper is never replaced by a template pretending to be
one.
"""

from __future__ import annotations

from uuid import UUID

from packages.reports.contracts import (
    FinalPaper,
    MapItem,
    PaperReviewReport,
    ReasoningMap,
)
from packages.reports.markdown import AI_ASSISTANCE_NOTICE
from packages.reports.reasoning_map import (
    DETERMINISTIC_NOTICE,
    KIND_LABELS,
    MODE_DETERMINISTIC,
    VERDICT_LABELS,
    seat_display,
)
from packages.reports.safety import apply_safety_notice, sanitize_export


def _doi_link(doi: str | None) -> str:
    if not doi:
        return ""
    return f" <https://doi.org/{doi}>"


def _ref_list(refs: tuple[str, ...]) -> str:
    return f"（溯源：{'、'.join(refs)}）" if refs else ""


def _render_map_markdown(paper: FinalPaper, rmap: ReasoningMap) -> list[str]:
    """Render the reasoning map in the same order as the workspace view:
    answer → verdicts → the step-by-step chain (each step's conclusion with
    its support, limits and unknowns directly beneath it) → cruxes → next
    steps → appendix. Limitations sit beside the conclusions inside every
    step, and again in full right after the chain (CLAUDE.md 11)."""
    items: dict[str, MapItem] = {item.id: item for item in rmap.items}
    lines = [f"# {paper.title}", ""]

    lines += ["## 一句话回答", ""]
    if rmap.mode == MODE_DETERMINISTIC:
        lines += [f"> {DETERMINISTIC_NOTICE}", ""]
    if rmap.bottom_line:
        lines += [rmap.bottom_line, ""]
    for verdict in rmap.verdicts:
        lines.append(
            f"- **{verdict.claim}**：{VERDICT_LABELS[verdict.verdict]}。"
            f"{verdict.reason}{_ref_list(verdict.refs)}"
        )
    if rmap.verdicts:
        lines.append("")

    lines += ["## 推理链", ""]
    for number, step in enumerate(rmap.steps, start=1):
        lines += [f"### {number}. {step.question}", "", step.answer, ""]
        for label, values in (
            ("依据", step.support),
            ("边界与反例", step.limits),
            ("尚未知道", step.unknowns),
        ):
            if values:
                lines.append(f"**{label}**")
                lines.extend(f"- {value}" for value in values)
                lines.append("")
        if step.resolve:
            lines += [f"**如何验证**：{step.resolve}", ""]
        if step.refs:
            lines += [_ref_list(step.refs), ""]
        if step.related:
            count = len(step.related)
            lines += [f"<details><summary>同一环节的其余条目（{count}）</summary>", ""]
            lines.extend(
                f"- [{ref}] {items[ref].headline}"
                for ref in step.related
                if ref in items
            )
            lines += ["", "</details>", ""]

    if rmap.cruxes:
        lines += ["## 关键分歧", ""]
        for crux in rmap.cruxes:
            lines.append(f"### {crux.question}")
            lines.append("")
            lines.extend(
                f"- **{position.holders or '一方'}**：{position.view}"
                for position in crux.positions
            )
            if crux.decider:
                lines.append(f"- **什么证据能裁决**：{crux.decider}")
            lines.append("")

    if rmap.next_steps:
        lines += ["## 下一步研究", ""]
        lines.extend(
            f"{index}. {action.action}" + (f" —— {action.why}" if action.why else "")
            for index, action in enumerate(rmap.next_steps, start=1)
        )
        lines.append("")

    lines += ["## 局限", ""]
    if paper.limitations:
        lines += [f"- {item}" for item in paper.limitations]
    else:
        lines.append("_无记录局限。_")
    lines.append("")

    lines += ["## 附录", ""]
    judgments = [item for item in rmap.items if item.kind == "judgment"]
    if judgments:
        lines += ["### 专家终审原文", ""]
        for item in judgments:
            dissent = "（保留异议）" if item.has_dissent else ""
            heading = f"#### [{item.id}] {seat_display(item.seat)}{dissent}"
            lines += [heading, "", item.text, ""]
    if rmap.unassigned:
        lines += ["### 未归入推理链的条目", ""]
        lines.extend(
            f"- [{ref}] {KIND_LABELS.get(items[ref].kind, items[ref].kind)}："
            f"{items[ref].headline}"
            for ref in rmap.unassigned
            if ref in items
        )
        lines.append("")
    if paper.investigation_process:
        lines += ["### 调查过程", ""]
        lines += [f"- {item}" for item in paper.investigation_process]
        lines.append("")
    lines += ["### 参考文献", ""]
    if not paper.references:
        lines.append("_本次没有可绑定 DOI 的已采纳研究发现。_")
    for ref in paper.references:
        lines.append(f"- {ref.title}{_doi_link(ref.doi)}")
    lines.append("")
    return lines


def _render_review_markdown(paper: PaperReviewReport) -> list[str]:
    """Render a paper-review report (round-7). Same shape as the web view:
    paper overview -> rigor issues -> evidence gaps -> improvement
    suggestions -> conclusion and limitations side by side."""
    lines = [f"# {paper.title}", ""]

    overview = paper.paper_overview
    lines += ["## 论文概况", ""]
    if overview.title:
        lines.append(f"- **标题**：{overview.title}")
    lines.append(f"- **研究问题**：{overview.research_question}")
    lines.append("- **主要观点与佐证**：")
    if not overview.main_claims:
        lines.append("  - _未提取到可核验的主要观点。_")
    for claim in overview.main_claims:
        if claim.supporting_evidence:
            evidence = "；".join(claim.supporting_evidence)
            lines.append(f"  - {claim.statement}（论文佐证：{evidence}）")
        else:
            lines.append(f"  - {claim.statement}（论文未提供可辨识佐证）")
    lines.append("")

    if paper.investigation_process:
        lines += ["## 调查过程", ""]
        lines += [f"- {item}" for item in paper.investigation_process]
        lines.append("")

    lines += ["## 不严谨之处", ""]
    if not paper.rigor_issues:
        lines.append("_未记录不严谨之处。_")
    for issue in paper.rigor_issues:
        ref = f"（{issue.claim_ref}）" if issue.claim_ref else "（论文整体）"
        severity = f" [{issue.severity}]" if issue.severity else ""
        lines.append(f"- {issue.issue}{ref}{severity}")
    lines.append("")

    lines += ["## 证据不充分之处", ""]
    if not paper.evidence_insufficiency:
        lines.append("_未记录证据不充分之处。_")
    for gap in paper.evidence_insufficiency:
        ref = f"（{gap.claim_ref}）" if gap.claim_ref else "（论文整体）"
        lines.append(f"- {gap.missing_evidence}{ref}")
        if gap.suggested_evidence:
            lines.append(f"  - 建议补充：{gap.suggested_evidence}")
    lines.append("")

    lines += ["## 改进建议", ""]
    if not paper.improvement_suggestions:
        lines.append("_未记录改进建议。_")
    for suggestion in paper.improvement_suggestions:
        ref = f"（{suggestion.claim_ref}）" if suggestion.claim_ref else "（论文整体）"
        lines.append(f"- {suggestion.issue}{ref}")
    lines.append("")

    # Conclusion and limitations beside each other -- CLAUDE.md 11.
    lines += ["## 结论与局限", ""]
    lines += ["### 结论", "", paper.conclusion, ""]
    lines += ["### 局限（本次审查自身的局限）", ""]
    if paper.limitations:
        lines += [f"- {item}" for item in paper.limitations]
    else:
        lines.append("_无记录局限。_")
    lines.append("")
    return lines


def render_paper_markdown(
    paper: FinalPaper | PaperReviewReport | None,
    task_id: UUID,
    question: str,
    reason: str | None = None,
    *,
    is_mental_health: bool = False,
) -> str:
    """Render the paper (or review report), or an honest stub when none exists.

    ``reason`` is the FINAL_PAPER_FAILED reason or a presentation-side note
    ("synthesis pending", "paper not generated"). The stub never pretends the
    paper exists -- CLAUDE.md 10 forbids fabricating completeness.
    """
    if paper is None:
        lines = [
            "# 综合论文未生成",
            "",
            f"研究问题：{question}",
            "",
            f"原因：{reason or '未知'}",
            "",
            "本任务尚未产出整合论文（模型调用未完成或未配置模型网关）。"
            "结论与局限以 Research Brief 为准：请查看研究简报或证据图。",
            "",
        ]
        content = "\n".join(lines)
        content = apply_safety_notice(content, is_mental_health)
        return sanitize_export(content)

    if isinstance(paper, PaperReviewReport):
        lines = _render_review_markdown(paper)
    elif paper.reasoning_map is not None:
        lines = _render_map_markdown(paper, paper.reasoning_map)
    else:
        lines = [f"# {paper.title}", ""]
        if paper.abstract:
            lines += ["## 摘要", "", paper.abstract, ""]
        if paper.investigation_process:
            lines += ["## 调查过程", ""]
            lines += [f"- {item}" for item in paper.investigation_process]
            lines.append("")

        lines.append("## 正文")
        lines.append("")
        for section in paper.sections:
            lines.append(f"### {section.heading}")
            lines.append("")
            lines.extend(section.paragraphs)
            lines.append("")

        if paper.standpoints:
            lines += ["## 各方观点与缺陷", ""]
            for standpoint in paper.standpoints:
                lines.append(f"### {standpoint.seat}")
                lines.append("")
                lines.append(f"- **观点**：{standpoint.position}")
                if standpoint.weakness:
                    lines.append(f"- **缺陷**：{standpoint.weakness}")
                if standpoint.disagreement:
                    lines.append(f"- **分歧**：{standpoint.disagreement}")
                if standpoint.supporting_evidence:
                    lines.append("- **支撑证据**：")
                    lines.extend(
                        f"  - {item}" for item in standpoint.supporting_evidence
                    )
                lines.append("")

        if paper.overall_conclusion:
            lines += ["## 总体结论", "", paper.overall_conclusion, ""]
            if paper.conclusion_evidence:
                lines.append("**支撑证据：**")
                lines.extend(f"- {item}" for item in paper.conclusion_evidence)
                lines.append("")

        # Limitations beside the conclusions, not at the end -- CLAUDE.md 11.
        lines += ["## 结论与局限", ""]
        if paper.limitations:
            lines += [f"- {item}" for item in paper.limitations]
        else:
            lines.append("_无记录局限。_")
        lines.append("")

        lines += ["## 参考文献", ""]
        if not paper.references:
            lines.append("_论文未引用任何来源（模型未能将结论绑定到已有来源）。_")
        for ref in paper.references:
            lines.append(f"- {ref.title}{_doi_link(ref.doi)}")
        lines.append("")

    lines += ["---", "", AI_ASSISTANCE_NOTICE, ""]
    content = "\n".join(lines)
    content = apply_safety_notice(content, is_mental_health)
    # Sanitised last so a redaction cannot be reintroduced by a later section.
    return sanitize_export(content)


__all__ = ["render_paper_markdown"]
