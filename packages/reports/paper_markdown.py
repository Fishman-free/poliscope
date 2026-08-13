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

from packages.reports.contracts import FinalPaper, PaperReviewReport
from packages.reports.markdown import AI_ASSISTANCE_NOTICE
from packages.reports.safety import apply_safety_notice, sanitize_export


def _doi_link(doi: str | None) -> str:
    if not doi:
        return ""
    return f" <https://doi.org/{doi}>"


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
