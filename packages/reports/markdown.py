from __future__ import annotations


def render_markdown(
    question: str,
    atomic_claims: list[str],
    admitted_findings: list[dict],
    blindspots: list[dict],
    dissents: list[dict],
) -> str:
    lines = [
        f"# Research Brief: {question}",
        "",
        "## 执行摘要",
        "",
        f"原子主张数量: {len(atomic_claims)}",
        f"已采纳发现数量: {len(admitted_findings)}",
        f"盲点数量: {len(blindspots)}",
        f"异议数量: {len(dissents)}",
        "",
        "## 参考文献与事件附录",
        "",
    ]
    return "\n".join(lines)
