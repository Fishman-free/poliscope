"""Unit tests for the round-9 fallback integrated paper (最终论文总是整合结论).

When the synthesis model call fails, is quarantined, or no gateway is
connected, the worker must still end the task with an integrated conclusion
assembled from the Research Brief alone -- never the "综合论文尚未生成" stub.
These tests cover the pure assembler; the ledger write path lives in the
integration tests (test_worker_pipeline.py).
"""

from __future__ import annotations

from typing import cast
from uuid import uuid4

from packages.reports.contracts import FinalPaper
from packages.reports.service import BriefNode, ResearchBrief
from packages.reports.synthesis import (
    _fallback_integrated_paper,
    _phase_coverage,
)
from packages.research.repository import StoredClaim


def _brief() -> ResearchBrief:
    return ResearchBrief(
        task_id=uuid4(),
        question="社交媒体使用是否会降低青少年心理健康水平？",
        status="COMPLETED_WITH_GAPS",
        confirmed_claims=(
            _claim("社交媒体使用与抑郁呈正相关", "CORRELATIONAL"),
            _claim("因果方向未解决，存在反向因果", "CAUSAL"),
        ),
        findings=(
            _node("STUDY_FINDING", {"statement": "一项纵向队列研究显示相关"}),
            _node("STUDY_FINDING", {"statement": "测量偏差影响效应方向"}),
        ),
        blindspots=(_node("BLINDSPOT", {"statement": "未调查年龄组差异"}),),
        dissents=(_node("DISSENT_CERTIFICATE", {"statement": "证据不足，拒绝下结论"}),),
        paper_count=3,
        independent_cluster_count=2,
        limitations=("样本以欧美为主，通用性有限。",),
        absent_seats=("adversarial_falsifier",),
    )


def _claim(statement: str, claim_type: str) -> StoredClaim:
    return cast(
        StoredClaim,
        type("_C", (), {
            "statement": statement,
            "claim_type": claim_type,
            "falsification_condition": "不存在显著相关",
        })(),
    )


def _node(node_type: str, payload: dict[str, object]) -> BriefNode:
    return BriefNode(
        node_id=uuid4(), node_type=node_type, status="active", payload=payload
    )


def test_fallback_paper_always_produces_sections() -> None:
    paper = _fallback_integrated_paper(
        _brief(), {}, "社交媒体使用是否会降低青少年心理健康水平？"
    )
    assert isinstance(paper, FinalPaper)
    assert paper.title
    assert paper.abstract
    assert len(paper.sections) >= 4
    # References are empty (no model, no source registry in the assembler) --
    # an honest absence, not a fabricated citation.
    assert paper.references == ()


def test_fallback_paper_embeds_findings_and_claims() -> None:
    paper = _fallback_integrated_paper(_brief(), {}, "Q")
    all_text = "\n".join(
        "\n".join(section.paragraphs) for section in paper.sections
    )
    assert "纵向队列" in all_text
    assert "测量偏差" in all_text
    assert "社交媒体使用与抑郁呈正相关" in all_text
    assert "未调查年龄组差异" in all_text
    assert "证据不足，拒绝下结论" in all_text


def test_fallback_paper_reports_gaps_honestly() -> None:
    paper = _fallback_integrated_paper(_brief(), {}, "Q")
    process = "\n".join(paper.investigation_process)
    assert "3 篇论文" in process
    assert "缺席席位：adversarial_falsifier" in process
    # 缺席席位的科学家缺席记录在 process 里，但不伪装成无缺席的完整运行。
    limitations = "\n".join(paper.limitations)
    assert "adversarial_falsifier" in limitations or "缺席" in limitations


def test_fallback_paper_without_gaps_is_clean() -> None:
    clean = _brief()
    clean.status = "COMPLETED"
    clean.absent_seats = ()
    paper = _fallback_integrated_paper(clean, {}, "Q")
    process = "\n".join(paper.investigation_process)
    assert "全部阶段完成" in process
    assert "缺席" not in process


def test_fallback_paper_consensus_lines_are_included() -> None:
    consensus: dict[str, object] = {
        "conditional_consensus": "证据指向弱正相关，但因果方向未决。",
        "boundary_conditions": ["仅适用于社交媒体重度用户"],
    }
    paper = _fallback_integrated_paper(_brief(), consensus, "Q")
    all_text = "\n".join(
        "\n".join(section.paragraphs) for section in paper.sections
    )
    assert "证据指向弱正相关" in all_text


def test_phase_coverage_summary() -> None:
    gapped = _brief()
    gapped.failed_phases = ("JOINT_MODELING",)
    gapped.skipped_phases = ("REPORTING",)
    assert "阶段失败" in _phase_coverage(gapped)
    assert "未执行" in _phase_coverage(gapped)

    clean = _brief()
    clean.failed_phases = ()
    clean.skipped_phases = ()
    assert _phase_coverage(clean) == "全部阶段完成"
