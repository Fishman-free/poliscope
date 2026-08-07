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

from packages.reports.contracts import FinalPaper
from packages.reports.markdown import AI_ASSISTANCE_NOTICE
from packages.reports.safety import apply_safety_notice, sanitize_export


def _doi_link(doi: str | None) -> str:
    if not doi:
        return ""
    return f" <https://doi.org/{doi}>"


def render_paper_markdown(
    paper: FinalPaper | None,
    task_id: UUID,
    question: str,
    reason: str | None = None,
    *,
    is_mental_health: bool = False,
) -> str:
    """Render the paper, or an honest stub when no paper exists.

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
