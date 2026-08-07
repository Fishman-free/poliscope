"""Render a finished task's workspace snapshot into ``docs/poliscope/``.

When Poliscope is used as a skill (``poliscope export-docs`` from a project),
the researcher wants the results *in the project*, not only in the web
workbench: a per-task directory under ``docs/poliscope/`` carrying the seven
scientists' positions (``scientists/``), the evidence map (``evidence.md``),
the council record (``council.md``), the research brief (``brief.md``), and
an index (``README.md``).

This module is pure rendering: every fact comes from the workspace snapshot
the API already returns (seats, graph, evolution, brief), and the brief is
the server-rendered markdown from ``/api/reports`` -- the CLI never
re-serialises or invents content (same philosophy as ``_cmd_export``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Seat display names, matching the web workbench's SEAT_LABELS.
SEAT_LABELS: dict[str, str] = {
    "theory_builder": "理论建构者",
    "causal_scientist": "因果推断专家",
    "measurement_scientist": "测量与构念专家",
    "replication_scientist": "统计与复现专家",
    "boundary_scientist": "边界与情境专家",
    "adversarial_falsifier": "对抗性证伪者",
    "evidence_auditor": "证据与溯源审计员",
}

NODE_LABELS: dict[str, str] = {
    "ResearchQuestion": "研究问题",
    "Claim": "主张",
    "Source": "来源",
    "StudyFinding": "研究发现",
    "Construct": "构念",
    "Context": "情境",
    "Blindspot": "盲点",
    "DebateCapsule": "争议胶囊",
    "DiscriminatingStudy": "可区分性研究",
    "DissentCertificate": "异议证书",
}

EDGE_LABELS: dict[str, str] = {
    "SUPPORTS": "支持",
    "REFUTES": "反驳",
    "QUALIFIES": "限定",
    "CONTRADICTS": "冲突",
    "CONFOUNDS": "混杂",
    "MEDIATES": "中介",
    "MODERATES": "调节",
    "OPERATIONALIZES": "操作化",
    "DERIVED_FROM": "源自",
    "APPLIES_IN": "适用于",
    "EXPOSES": "揭示",
    "TESTS": "检验",
}

# Evolution View event labels and internal gap strings, mirroring the web
# workbench's EvolutionView constants. The CLI must not import packages, so
# these live here (same precedent as SEAT_LABELS). Unknown strings pass
# through unchanged -- a label map must never guess.
EVENT_LABELS: dict[str, str] = {
    "Claim": "主张（含分叉）",
    "CHALLENGE_RAISED": "提出质询",
    "DissentCertificate": "异议证书",
    "CONFIDENCE_UPDATED": "置信度轨迹点",
    "ACQUISITION:no_tool_provider": "未配置工具网关，无法获取证据",
    "JOINT_MODELING:no_capsule_fold": "联合建模未形成可折叠的争论胶囊",
    "FINAL_REJUDGMENT:no_dissent_target": "最终复判未指向异议目标",
    "resurrection_condition_not_met": "复活条件未满足",
    "no model provider is connected to the Model Gateway": "模型网关未连接",
    "acquisition timed out": "证据获取超时",
    "source budget exhausted": "来源预算已耗尽",
    "source is retracted": "来源已撤回",
}


def _humanize_event(value: object) -> str:
    text = str(value)
    return EVENT_LABELS.get(text, text)


def _task_slug(snapshot: dict[str, Any]) -> str:
    """Stable directory name for one task: first 8 chars of the id plus a
    short, filesystem-safe question fragment so two tasks are easy to tell
    apart in the docs tree."""
    task = snapshot.get("task") or {}
    task_id = str(task.get("task_id") or "task")
    question = str(task.get("question") or "")
    words = re.findall(r"[A-Za-z0-9一-鿿]+", question)[:3]
    fragment = "-".join(words).lower()[:40] or "research"
    return f"{task_id[:8]}-{fragment}"


def _node_text(node: dict[str, Any]) -> str:
    payload = node.get("payload") or {}
    for key in ("question", "statement", "title", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return str(payload.get("id") or node.get("id") or "（无文本）")


def _render_evidence_md(snapshot: dict[str, Any]) -> str:
    graph = snapshot.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    lines = ["# 证据图（Evidence Map）", ""]
    lines.append(
        f"- 论文数量（paper count）：{snapshot.get('paper_count', 0)}"
    )
    lines.append(
        f"- 独立证据簇数量（independent clusters）："
        f"{snapshot.get('independent_cluster_count', 0)}"
    )
    lines.append("")
    lines.append("## 节点")
    lines.append("")
    if not nodes:
        lines.append("（本任务尚未产出被采纳的证据节点）")
    for node in nodes:
        node_type = str(node.get("node_type") or "")
        label = NODE_LABELS.get(node_type, node_type)
        status = str(node.get("status") or "")
        lines.append(f"### [{label}] {_node_text(node)}")
        lines.append("")
        lines.append(f"- 类型：`{node_type}` · 状态：`{status}`")
        lines.append("")
    lines.append("## 边（关系）")
    lines.append("")
    if not edges:
        lines.append("（本任务尚未登记正式证据关系）")
    for edge in edges:
        edge_type = str(edge.get("edge_type") or "")
        label = EDGE_LABELS.get(edge_type, edge_type)
        lines.append(
            f"- `{edge.get('source_id')}` —[{label}]→ `{edge.get('target_id')}`"
        )
    return "\n".join(lines)


def _render_council_md(snapshot: dict[str, Any]) -> str:
    seats = snapshot.get("seats") or []
    lines = ["# 议会记录（Council Record）", ""]
    lines.append("## 七位科学家的立场（并列呈现，非投票裁决）")
    lines.append("")
    lines.append(
        "> 议会不进行多数投票裁决科研真理（CLAUDE.md 4）；以下仅并列每位"
        "科学家的独立预承诺与最终复判，共识以条件化共识形式呈现。"
    )
    lines.append("")
    for seat in seats:
        seat_name = str(seat.get("seat") or "")
        display = SEAT_LABELS.get(seat_name, seat_name)
        lines.append(f"### {display}（`{seat_name}`）")
        lines.append("")
        precommitment = seat.get("precommitment")
        if precommitment:
            lines.append(
                f"- 预承诺置信度：{precommitment.get('confidence')}"
            )
            lines.append(
                f"- 更新条件：{precommitment.get('update_condition')}"
            )
        challenges = seat.get("challenges_raised") or ()
        if challenges:
            lines.append("- 提出的质询：")
            for challenge in challenges:
                fatal = "致命" if challenge.get("is_fatal") else "一般"
                lines.append(
                    f"  - [{fatal}] {challenge.get('statement')}"
                    f"（主张 {challenge.get('claim_id')}）"
                )
        judgment = seat.get("final_judgment")
        if judgment:
            lines.append(f"- 最终复判：{judgment.get('final_judgment')}")
            lines.append(f"- 终审置信度：{judgment.get('confidence')}")
            if judgment.get("has_dissent"):
                lines.append("- 保留异议：是")
        unavailable = seat.get("unavailable_phases") or ()
        if unavailable:
            lines.append(f"- 缺席阶段：{', '.join(map(str, unavailable))}")
        lines.append("")
    consensus = snapshot.get("consensus")
    lines.append("## 条件化共识")
    lines.append("")
    if not consensus:
        lines.append("（联合建模未形成条件化共识）")
    else:
        text = consensus.get("conditional_consensus")
        if isinstance(text, str) and text:
            lines.append(f"- 条件化共识：{text}")
        for key, label in (
            ("boundary_conditions", "边界条件"),
            ("unresolved_conflicts", "未解决冲突"),
            ("falsification_conditions", "可证伪条件"),
        ):
            values = consensus.get(key)
            if isinstance(values, list) and values:
                lines.append(f"- {label}：")
                for item in values:
                    lines.append(f"  - {item}")
    lines.append("")
    lines.append("## 争议演化时间线")
    lines.append("")
    evolution = snapshot.get("evolution") or ()
    if not evolution:
        lines.append("（账本中无争议演化事件）")
    for entry in evolution:
        event_type = _humanize_event(entry.get("event_type"))
        lines.append(
            f"- `{entry.get('sequence')}` {event_type}"
            f"（status={entry.get('status')}）"
        )
    return "\n".join(lines)


def _render_scientist_md(seat: dict[str, Any]) -> str:
    seat_name = str(seat.get("seat") or "")
    display = SEAT_LABELS.get(seat_name, seat_name)
    lines = [f"# {display}（`{seat_name}`）", ""]
    precommitment = seat.get("precommitment")
    if precommitment:
        lines.append("## 独立预承诺")
        lines.append("")
        lines.append(f"- 预承诺置信度：{precommitment.get('confidence')}")
        lines.append(f"- 更新条件：{precommitment.get('update_condition')}")
    else:
        lines.append("## 独立预承诺")
        lines.append("")
        lines.append("（本席位未记录预承诺）")
    lines.append("")
    challenges = seat.get("challenges_raised") or ()
    lines.append("## 提出的质询")
    lines.append("")
    if not challenges:
        lines.append("（未提出质询）")
    for challenge in challenges:
        fatal = "致命" if challenge.get("is_fatal") else "一般"
        lines.append(
            f"- [{fatal}] {challenge.get('statement')}"
            f"（主张 {challenge.get('claim_id')}）"
        )
    lines.append("")
    lines.append("## 最终独立复判")
    lines.append("")
    judgment = seat.get("final_judgment")
    if judgment:
        lines.append(f"- 判断：{judgment.get('final_judgment')}")
        lines.append(f"- 置信度：{judgment.get('confidence')}")
        lines.append(f"- 保留异议：{'是' if judgment.get('has_dissent') else '否'}")
    else:
        lines.append("（本席位未记录最终复判）")
    lines.append("")
    unavailable = seat.get("unavailable_phases") or ()
    if unavailable:
        lines.append(f"## 缺席阶段\n\n{', '.join(map(str, unavailable))}\n")
    return "\n".join(lines)


def _render_index_md(
    snapshot: dict[str, Any],
    task_dir: Path,
) -> str:
    task = snapshot.get("task") or {}
    lines = [
        "# Poliscope 研究结果",
        "",
        f"- 研究问题：{task.get('question')}",
        f"- 状态：{task.get('status')}",
        f"- 创建者：{task.get('created_by')}",
        f"- 论文数量：{snapshot.get('paper_count', 0)} · "
        f"独立证据簇：{snapshot.get('independent_cluster_count', 0)}",
        "",
        "## 文件索引",
        "",
        "- [最终论文（paper.md）](paper.md)",
        "- [研究简报（brief.md）](brief.md)",
        "- [证据图（evidence.md）](evidence.md)",
        "- [议会记录（council.md）](council.md)",
        "- [七位科学家（scientists/）](scientists/)",
        "",
        "## 科学家文件",
        "",
    ]
    seats = snapshot.get("seats") or ()
    for seat in seats:
        seat_name = str(seat.get("seat") or "")
        display = SEAT_LABELS.get(seat_name, seat_name)
        lines.append(
            f"- [{display}](scientists/{seat_name}.md)（`{seat_name}`）"
        )
    lines.extend(
        [
            "",
            "> 本目录由 `poliscope export-docs` 从任务工作区快照生成。"
            "所有内容均可在网页工作台（Research Brief / Controversy Map / "
            "Council / Audit Trail）中核验。",
            "",
        ]
    )
    return "\n".join(lines)


def _render_paper_stub(snapshot: dict[str, Any]) -> str:
    """Local honest stub for a task whose paper markdown could not be fetched.

    The CLI never re-serialises the paper itself (the server renders, the
    client writes -- same philosophy as the brief). When the server's paper
    endpoint is unreachable, the stub says exactly that and points at the
    files that do exist, rather than pretending a paper exists.
    """
    task = snapshot.get("task") or {}
    question = task.get("question") or "（未知问题）"
    return (
        "# 综合论文未生成\n\n"
        f"研究问题：{question}\n\n"
        "论文 Markdown 获取失败（API 不可达）。"
        "请查看同目录下的 brief.md 与 evidence.md。\n"
    )


def write_task_docs(
    snapshot: dict[str, Any],
    report_markdown: str,
    output_root: Path,
    paper_markdown: str | None = None,
) -> list[Path]:
    """Write a task's docs under ``output_root/{slug}/``; return the files.

    ``paper_markdown`` is the server-rendered final paper (``/api/reports/
    {id}/paper?format=markdown``). It is always written -- either the real
    paper or the server's honest stub; when the fetch itself failed,
    ``None`` renders a local stub so the file still exists and says so.
    """
    task_dir = output_root / _task_slug(snapshot)
    scientists_dir = task_dir / "scientists"
    scientists_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    index = task_dir / "README.md"
    index.write_text(_render_index_md(snapshot, task_dir), encoding="utf-8")
    written.append(index)

    paper = task_dir / "paper.md"
    paper.write_text(
        paper_markdown if paper_markdown is not None else _render_paper_stub(snapshot),
        encoding="utf-8",
    )
    written.append(paper)

    brief = task_dir / "brief.md"
    brief.write_text(report_markdown, encoding="utf-8")
    written.append(brief)

    evidence = task_dir / "evidence.md"
    evidence.write_text(_render_evidence_md(snapshot), encoding="utf-8")
    written.append(evidence)

    council = task_dir / "council.md"
    council.write_text(_render_council_md(snapshot), encoding="utf-8")
    written.append(council)

    for seat in snapshot.get("seats") or ():
        seat_name = str(seat.get("seat") or "")
        if not seat_name:
            continue
        path = scientists_dir / f"{seat_name}.md"
        path.write_text(_render_scientist_md(seat), encoding="utf-8")
        written.append(path)

    return written


__all__ = ["SEAT_LABELS", "write_task_docs"]
