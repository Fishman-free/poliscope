"""Markdown rendering for the Research Brief.

The previous renderer printed the length of five lists and a heading for an
appendix it never wrote, so a brief was the same document whatever the council
had found. This one renders the brief's contents.

Section order is deliberate. Limitations come immediately after the conclusions
rather than at the end, because CLAUDE.md 11 requires them side by side and a
reader who stops halfway must not stop having read only the findings.
"""

from __future__ import annotations

from packages.reports.safety import apply_safety_notice, sanitize_export
from packages.reports.service import BriefNode, ResearchBrief

AI_ASSISTANCE_NOTICE = (
    "本报告由 AI 辅助研究系统生成。所有结论均须结合原始文献独立复核。"
)


def _statement(node: BriefNode) -> str:
    for key in ("statement", "question", "summary", "exact_quote"):
        value = node.payload.get(key)
        if value:
            return str(value)
    return f"({node.node_type} {node.node_id})"


def _bullets(nodes: tuple[BriefNode, ...], empty: str) -> list[str]:
    if not nodes:
        return [f"_{empty}_"]
    return [
        f"- {_statement(node)}"
        + (f" `[{node.status}]`" if node.status != "active" else "")
        for node in nodes
    ]


def render_markdown(brief: ResearchBrief) -> str:
    lines = [
        f"# Research Brief: {brief.question}",
        "",
        f"任务状态: `{brief.status}`",
        "",
        "## 一、结论与局限",
        "",
        "### 已确认原子主张",
        "",
    ]
    if brief.confirmed_claims:
        lines += [
            f"- {claim.statement} （类型: {claim.claim_type}；证伪条件: "
            f"{claim.falsification_condition}）"
            for claim in brief.confirmed_claims
        ]
    else:
        lines.append("_无已确认原子主张。_")

    lines += ["", "### 已采纳发现", ""]
    lines += _bullets(brief.findings, "无已采纳的研究发现。")

    # Limitations sit here, not at the end. CLAUDE.md 11.
    lines += ["", "### 局限与未知", ""]
    lines += [f"- {item}" for item in brief.limitations]

    lines += ["", "## 二、盲点", ""]
    lines += _bullets(brief.blindspots, "本轮未发现盲点。")

    lines += ["", "## 三、少数意见与异议", ""]
    lines += _bullets(brief.dissents, "无记录在案的异议。")

    lines += ["", "## 四、可区分性研究建议", ""]
    lines += _bullets(brief.discriminating_studies, "未提出可区分性研究。")

    lines += [
        "",
        "## 五、证据覆盖",
        "",
        f"- 论文数量: {brief.paper_count}",
        f"- 独立证据簇数量: {brief.independent_cluster_count}",
        f"- 被反驳/收窄/撤回节点: {len(brief.refuted_or_withdrawn)}（保留可审计）",
        f"- 被证据门拒绝的提交: {len(brief.unadmitted_events)}",
        "",
        "## 六、AI 辅助声明",
        "",
        AI_ASSISTANCE_NOTICE,
        "",
    ]

    content = "\n".join(lines)
    content = apply_safety_notice(content, brief.is_mental_health)
    # Sanitised last so a redaction cannot be reintroduced by a later section.
    return sanitize_export(content)
