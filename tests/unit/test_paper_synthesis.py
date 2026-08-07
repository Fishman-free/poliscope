"""Unit tests for the paper synthesis contracts and renderer.

The synthesis call itself (``synthesize_paper``) needs a database session and
is covered by tests/integration/test_paper_pipeline.py; these tests cover the
pure parts -- payload parsing and the markdown renderer, including the honest
stub a missing paper must render (CLAUDE.md 10).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.reports.contracts import FinalPaper, PaperReference, PaperSection
from packages.reports.paper_markdown import render_paper_markdown
from packages.reports.synthesis import paper_payload_to_dataclass


def _paper_payload() -> dict[str, object]:
    return {
        "title": "社交媒体使用与青少年抑郁：一项议会证据整合",
        "abstract": "七位科学家独立预承诺、交叉质询后形成的整合结论。",
        "sections": [
            {
                "heading": "发现",
                "paragraphs": [
                    "相关性研究一致，但因果方向未解决。",
                    "反向因果与混杂是主要威胁。",
                ],
            }
        ],
        "references": [
            {
                "id": str(uuid4()),
                "title": "A longitudinal cohort study",
                "doi": "10.1000/example",
            }
        ],
        "limitations": ["仅一项纵向研究；测量偏差未消除。"],
        "investigation_process": ["7 席全部参与", "证据门拒绝了 3 项提交"],
    }


def test_parse_valid_paper_payload() -> None:
    paper = paper_payload_to_dataclass(_paper_payload())
    assert isinstance(paper, FinalPaper)
    assert paper.title == "社交媒体使用与青少年抑郁：一项议会证据整合"
    assert paper.sections[0].paragraphs[1].startswith("反向因果")
    assert paper.references[0].doi == "10.1000/example"


def test_parse_rejects_payload_without_title_or_abstract() -> None:
    payload = _paper_payload()
    del payload["title"]
    with pytest.raises(ValueError, match="title or abstract"):
        paper_payload_to_dataclass(payload)


def test_parse_rejects_payload_without_sections() -> None:
    payload = _paper_payload()
    payload["sections"] = []
    with pytest.raises(ValueError, match="no sections"):
        paper_payload_to_dataclass(payload)


def test_parse_drops_references_missing_id_or_title() -> None:
    payload = _paper_payload()
    payload["references"] = [
        {"id": str(uuid4()), "title": "kept", "doi": None},
        {"id": str(uuid4()), "title": ""},
        {"title": "no id"},
        "not a dict",
    ]
    paper = paper_payload_to_dataclass(payload)
    assert len(paper.references) == 1
    assert paper.references[0].title == "kept"


def test_parse_tolerates_broken_section_entries() -> None:
    payload = _paper_payload()
    payload["sections"] = [
        {"heading": "ok", "paragraphs": ["p1"]},
        {"heading": "", "paragraphs": ["p2"]},
        {"paragraphs": []},
        "not a dict",
    ]
    paper = paper_payload_to_dataclass(payload)
    assert len(paper.sections) == 1
    assert paper.sections[0].heading == "ok"


def _paper() -> FinalPaper:
    return FinalPaper(
        title="T",
        abstract="A",
        sections=(PaperSection(heading="S", paragraphs=("P",)),),
        references=(
            PaperReference(id="1", title="R", doi="10.1/x"),
            PaperReference(id="2", title="NoDoi", doi=None),
        ),
        limitations=("L1",),
        investigation_process=("I1",),
    )


def test_render_paper_markdown_includes_sections_references_limitations() -> None:
    task_id = uuid4()
    text = render_paper_markdown(_paper(), task_id, "Q?")
    assert "# T" in text
    assert "## 摘要" in text
    assert "### S" in text
    assert "P" in text
    assert "https://doi.org/10.1/x" in text
    assert "NoDoi" in text
    assert "L1" in text
    assert "## 结论与局限" in text
    assert "## 参考文献" in text
    assert "## 调查过程" in text


def test_render_paper_stub_is_honest_when_no_paper() -> None:
    task_id = uuid4()
    text = render_paper_markdown(None, task_id, "Q?", reason="synthesis pending")
    assert "综合论文未生成" in text
    assert "synthesis pending" in text
    assert "Research Brief" in text


def test_render_paper_does_not_leak_signed_urls() -> None:
    paper = FinalPaper(
        title="T",
        abstract="A",
        sections=(
            PaperSection(
                heading="S",
                paragraphs=(
                    "See https://s3.example.com/doc.pdf?X-Amz-Signature=abc",
                ),
            ),
        ),
        references=(),
        limitations=(),
        investigation_process=(),
    )
    text = render_paper_markdown(paper, uuid4(), "Q?")
    assert "X-Amz-Signature" not in text
    assert "REDACTED" in text
