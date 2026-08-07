"""Skill-mode output: ``poliscope export-docs`` rendering (round 4).

The CLI writes a finished task's workspace snapshot into the project's
docs/poliscope/ -- scientists/ (one file per seat), evidence.md (evidence
map), council.md (positions + evolution), brief.md (server-rendered report),
README.md (index). These tests pin the rendered shape with a hand-built
snapshot; nothing here talks to the network.
"""

from __future__ import annotations

from pathlib import Path

from apps.cli.docs_writer import write_task_docs

SNAPSHOT: dict[str, object] = {
    "task": {
        "task_id": "e13b4d00-fa78-4609-852a-b44079c3d6bd",
        "question": "中国大陆地区青少年自杀率和学习成绩是否具有显著关系？",
        "status": "COMPLETED",
        "created_by": "researcher",
    },
    "paper_count": 12,
    "independent_cluster_count": 5,
    "graph": {
        "nodes": [
            {
                "id": "n1",
                "node_type": "ResearchQuestion",
                "status": "active",
                "payload": {
                    "question": "中国大陆地区青少年自杀率和学习成绩是否具有显著关系？"
                },
            },
            {
                "id": "n2",
                "node_type": "DebateCapsule",
                "status": "active",
                "payload": {"statement": "学业压力与自杀风险的因果方向存在争议"},
            },
        ],
        "edges": [
            {"source_id": "n1", "target_id": "n2", "edge_type": "TESTS"},
            {"source_id": "n1", "target_id": "n2", "edge_type": "REFUTES"},
        ],
    },
    "seats": [
        {
            "seat": "theory_builder",
            "precommitment": {"confidence": "high", "update_condition": "cohort null"},
            "challenges_raised": (
                {
                    "claim_id": "c1",
                    "statement": "反向因果未被排除",
                    "is_fatal": True,
                },
            ),
            "final_judgment": {
                "final_judgment": "关系存在但方向存疑",
                "confidence": "medium",
                "has_dissent": True,
            },
            "unavailable_phases": ("BLINDSPOT_BOUNTY",),
        },
        {
            "seat": "evidence_auditor",
            "precommitment": None,
            "challenges_raised": (),
            "final_judgment": None,
            "unavailable_phases": (),
        },
    ],
    "evolution": (
        {"sequence": 1, "event_type": "CHALLENGE_RAISED", "status": "admitted"},
    ),
    "consensus": {
        "conditional_consensus": "关系存在，但需纵向设计确认方向",
        "boundary_conditions": ["仅适用于中国大陆学龄群体"],
        "unresolved_conflicts": ["测量偏差是否被低估"],
        "falsification_conditions": ["一项注册纵向队列发现零效应"],
    },
}

REPORT_MD = "# Research Brief\n\n结论与局限并排显示。\n"
PAPER_MD = "# 综合论文\n\n整合七位科学家立场。\n"


def test_write_task_docs_creates_expected_layout(tmp_path: Path) -> None:
    written = write_task_docs(SNAPSHOT, REPORT_MD, tmp_path)

    # README + paper + brief + evidence + council + 2 scientists
    assert len(written) == 7
    task_dir = tmp_path / "e13b4d00-中国大陆地区青少年自杀率和学习成绩是否具有显著关系"
    assert (task_dir / "README.md").is_file()
    assert (task_dir / "paper.md").is_file()
    assert (task_dir / "brief.md").is_file()
    assert (task_dir / "evidence.md").is_file()
    assert (task_dir / "council.md").is_file()
    assert (task_dir / "scientists" / "theory_builder.md").is_file()
    assert (task_dir / "scientists" / "evidence_auditor.md").is_file()


def test_paper_md_is_written_verbatim_when_fetched(tmp_path: Path) -> None:
    written = write_task_docs(SNAPSHOT, REPORT_MD, tmp_path, paper_markdown=PAPER_MD)
    paper = next(p for p in written if p.name == "paper.md")
    assert paper.read_text(encoding="utf-8") == PAPER_MD


def test_paper_md_falls_back_to_a_honest_stub(tmp_path: Path) -> None:
    """A paper fetch failure must still produce a file that says so."""
    written = write_task_docs(SNAPSHOT, REPORT_MD, tmp_path)
    paper = next(p for p in written if p.name == "paper.md")
    text = paper.read_text(encoding="utf-8")
    assert "综合论文未生成" in text
    assert "API 不可达" in text


def test_readme_index_lists_the_paper(tmp_path: Path) -> None:
    written = write_task_docs(SNAPSHOT, REPORT_MD, tmp_path)
    readme = next(p for p in written if p.name == "README.md")
    text = readme.read_text(encoding="utf-8")
    assert "[最终论文（paper.md）](paper.md)" in text


def test_evidence_md_lists_nodes_and_edges(tmp_path: Path) -> None:
    written = write_task_docs(SNAPSHOT, REPORT_MD, tmp_path)
    evidence = next(p for p in written if p.name == "evidence.md")
    text = evidence.read_text(encoding="utf-8")
    assert "研究问题" in text
    assert "争议胶囊" in text
    assert "—[反驳]→" in text
    assert "独立证据簇" in text
    assert "12" in text


def test_council_md_lists_positions_and_evolution(tmp_path: Path) -> None:
    written = write_task_docs(SNAPSHOT, REPORT_MD, tmp_path)
    council = next(p for p in written if p.name == "council.md")
    text = council.read_text(encoding="utf-8")
    assert "理论建构者" in text
    assert "反向因果未被排除" in text
    assert "最终复判：关系存在但方向存疑" in text
    assert "缺席阶段：BLINDSPOT_BOUNTY" in text
    # 事件类型用中文标签呈现，而不是原样英文。
    assert "提出质询" in text
    assert "CHALLENGE_RAISED" not in text
    # 条件化共识四字段。
    assert "条件化共识：关系存在，但需纵向设计确认方向" in text
    assert "边界条件" in text
    assert "未解决冲突" in text
    assert "可证伪条件" in text
    # 立场并列呈现的声明（CLAUDE.md 4）。
    assert "非投票裁决" in text


def test_scientist_files_render_each_position(tmp_path: Path) -> None:
    written = write_task_docs(SNAPSHOT, REPORT_MD, tmp_path)
    theory = next(p for p in written if p.name == "theory_builder.md")
    text = theory.read_text(encoding="utf-8")
    assert "理论建构者" in text
    assert "预承诺置信度：high" in text
    assert "[致命] 反向因果未被排除" in text
    assert "保留异议：是" in text

    auditor = next(p for p in written if p.name == "evidence_auditor.md")
    text = auditor.read_text(encoding="utf-8")
    assert "证据与溯源审计员" in text
    assert "（本席位未记录预承诺）" in text


def test_brief_is_written_verbatim(tmp_path: Path) -> None:
    written = write_task_docs(SNAPSHOT, REPORT_MD, tmp_path)
    brief = next(p for p in written if p.name == "brief.md")
    assert brief.read_text(encoding="utf-8") == REPORT_MD
