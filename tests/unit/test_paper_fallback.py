"""Unit tests for the round-9 fallback integrated paper (最终论文总是整合结论).

When the synthesis model call fails, is quarantined, or no gateway is
connected, the worker must still end the task with an integrated conclusion
assembled from the Research Brief alone -- never the "综合论文尚未生成" stub.
Since the reasoning-map rework the fallback is a deterministic reasoning map
(packages/reports/reasoning_map.py) with the prose fields derived from it.
These tests cover the pure assembler; the ledger write path lives in the
integration tests (test_worker_pipeline.py).
"""

from __future__ import annotations

from typing import cast
from uuid import uuid4

from packages.reports.contracts import FinalPaper
from packages.reports.reasoning_map import MODE_DETERMINISTIC, JudgmentRow
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


def _all_text(paper: FinalPaper) -> str:
    return paper.abstract + "\n" + "\n".join(
        "\n".join((section.heading, *section.paragraphs))
        for section in paper.sections
    )


def test_fallback_paper_always_produces_sections() -> None:
    paper = _fallback_integrated_paper(
        _brief(), {}, "社交媒体使用是否会降低青少年心理健康水平？"
    )
    assert isinstance(paper, FinalPaper)
    assert paper.title
    assert paper.abstract
    assert len(paper.sections) >= 4
    assert paper.reasoning_map is not None
    assert paper.reasoning_map.mode == MODE_DETERMINISTIC
    # No finding carries a DOI, so there is nothing honest to cite -- an
    # absence, not a fabricated citation.
    assert paper.references == ()


def test_fallback_paper_embeds_findings_and_claims() -> None:
    all_text = _all_text(_fallback_integrated_paper(_brief(), {}, "Q"))
    assert "纵向队列" in all_text
    assert "测量偏差" in all_text
    assert "社交媒体使用与抑郁呈正相关" in all_text
    assert "未调查年龄组差异" in all_text
    assert "证据不足，拒绝下结论" in all_text


def test_fallback_files_each_doubt_under_a_reasoning_step() -> None:
    """The blindspot is not a free-floating list item: it hangs under the step
    whose conclusion it threatens (age -> the boundary step)."""
    paper = _fallback_integrated_paper(_brief(), {}, "Q")
    assert paper.reasoning_map is not None
    boundary = next(
        step for step in paper.reasoning_map.steps if step.dimension == "boundary"
    )
    assert "未调查年龄组差异" in boundary.unknowns


def test_fallback_paper_reports_gaps_honestly() -> None:
    paper = _fallback_integrated_paper(_brief(), {}, "Q")
    process = "\n".join(paper.investigation_process)
    assert "3 篇论文" in process
    assert "对抗性证伪者" in process
    # The absent expert is a stated limitation, not a silently complete run.
    limitations = "\n".join(paper.limitations)
    assert "缺席" in limitations


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
    all_text = _all_text(paper)
    assert "证据指向弱正相关" in all_text
    assert "仅适用于社交媒体重度用户" in all_text


def test_fallback_without_consensus_lists_each_position() -> None:
    """未达成共识时，每位专家的终审观点都署名出现在它负责的推理环节里，
    而不是一句「未形成统一结论」了事。"""
    judgments = (
        JudgmentRow("causal_scientist", "相关可解释为反向因果", 0.6),
        JudgmentRow("theory_builder", "机制上可能存在正向效应", 0.5),
    )
    paper = _fallback_integrated_paper(_brief(), {}, "Q", judgments=judgments)
    assert paper.reasoning_map is not None
    answers = {step.dimension: step.answer for step in paper.reasoning_map.steps}
    assert answers["causality"].startswith("因果推断专家：相关可解释为反向因果")
    assert answers["mechanism"].startswith("理论建构者：机制上可能存在正向效应")
    # Without a real consensus the map invents no bottom line.
    assert paper.reasoning_map.bottom_line == ""
    assert "未经模型整合" in paper.abstract


def test_fallback_with_consensus_keeps_consensus_wording() -> None:
    """达成共识时，共识原文就是一句话回答。"""
    consensus: dict[str, object] = {
        "conditional_consensus": "证据指向弱正相关，但因果方向未决。",
        "boundary_conditions": ["仅适用于社交媒体重度用户"],
    }
    paper = _fallback_integrated_paper(_brief(), consensus, "Q")
    assert paper.overall_conclusion == "证据指向弱正相关，但因果方向未决。"
    assert paper.abstract.startswith("证据指向弱正相关")


def test_fallback_body_avoids_internal_jargon() -> None:
    """Bug 8 tracer: outside investigation_process the reader-facing paper
    must not use the system's internal process vocabulary."""
    consensus: dict[str, object] = {
        "conditional_consensus": "证据指向弱正相关，但因果方向未决。",
    }
    judgments = (
        JudgmentRow("causal_scientist", "相关可解释为反向因果", 0.6, True),
    )
    paper = _fallback_integrated_paper(
        _brief(), consensus, "Q", judgments=judgments
    )
    body = _all_text(paper)
    banned = (
        "原子主张",
        "条件化共识",
        "交叉核验",
        "交叉质询",
        "联合建模",
        "审计轨迹",
        "席位",
        "议会",
        "最终复判",
        "预承诺",
        "causal_scientist",
    )
    for term in banned:
        assert term not in body, f"internal jargon leaked into paper: {term}"


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
