"""Unit tests for the paper-review task's pure parts (round-7).

The full ``understand_paper`` call needs a database session (it appends
ledger events through SqlEventLedger) and is covered by the integration
tests; here we cover what is pure: text-block rendering, the prompt builder,
the uploaded-text loader (with a fake session and a real object store), the
paper-review claims suggestion, and the review report parsing/rendering.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from packages.papers.object_store import PrivateObjectStore
from packages.papers.parser import PageText
from packages.papers.understanding import (
    MAX_PAPER_TEXT_CHARS,
    _build_user_prompt,
    _load_paper_text,
    _render_text_blocks,
)
from packages.reports.contracts import PaperReviewReport
from packages.reports.paper_markdown import render_paper_markdown
from packages.reports.synthesis import (
    _parse_review_paper,
    _review_payload_dict,
    paper_payload_to_dataclass,
)
from packages.research.atomization import suggest_atomic_claims
from packages.research.contracts import ResearchContract
from tests.factories import make_research_contract

# --- text-block rendering -------------------------------------------------


def test_render_text_blocks_labels_each_block_with_its_locator() -> None:
    blocks = [
        PageText(page_number=1, text="第一页"),
        PageText(page_number=2, text="第二页"),
    ]
    rendered = _render_text_blocks(blocks, "页")
    assert rendered == "(页 1): 第一页\n(页 2): 第二页"


def test_render_text_blocks_skips_empty_blocks() -> None:
    blocks = [PageText(page_number=1, text=""), PageText(page_number=2, text="内容")]
    rendered = _render_text_blocks(blocks, "段")
    assert "(段 1)" not in rendered
    assert "(段 2): 内容" in rendered


# --- prompt builder -------------------------------------------------------


def test_build_user_prompt_admits_truncation() -> None:
    prompt = _build_user_prompt("论文文本", truncated=True, output_language="en")
    assert "truncated" in prompt
    assert "论文文本" in prompt


def test_build_user_prompt_never_mentions_truncation_when_complete() -> None:
    prompt = _build_user_prompt("论文文本", truncated=False, output_language="en")
    assert "truncated" not in prompt


# --- uploaded-text loader -------------------------------------------------


class _FakeScalarSession:
    """Scripts one row of (object_key, file_name) for the loader's select."""

    def __init__(self, rows: list[tuple[str, str | None]]) -> None:
        self._rows = rows

    async def execute(self, statement: Any) -> Any:
        class _Result:
            def all(self) -> list[tuple[str, str | None]]:
                return self._rows  # type: ignore[no-any-return, attr-defined]

        result = _Result()
        result._rows = self._rows  # type: ignore[attr-defined]
        return result


def _pdf_bytes(text: str) -> bytes:
    # fitz's built-in fonts cover Latin only -- Chinese test text comes back
    # mojibake after extraction, so the loader tests use English.
    import fitz  # type: ignore[import-untyped]

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    return bytes(document.tobytes())


async def test_load_paper_text_extracts_uploaded_pdf(tmp_path: Path) -> None:
    store = PrivateObjectStore(root=str(tmp_path))
    task_id = uuid4()
    stored = store.store(
        task_id=task_id, content=_pdf_bytes("Screen time and depression")
    )
    session = _FakeScalarSession([(stored.object_key, "paper.pdf")])

    text, truncated, error = await _load_paper_text(
        session,  # type: ignore[arg-type]
        store,
        [uuid4()],
    )

    assert error is None
    assert not truncated
    assert "Screen time and depression" in text
    assert "(paper.pdf, 页 1)" in text


async def test_load_paper_text_missing_object_records_gap(tmp_path: Path) -> None:
    store = PrivateObjectStore(root=str(tmp_path))
    session = _FakeScalarSession([("tasks/never-written/abc.pdf", "paper.pdf")])

    text, truncated, error = await _load_paper_text(
        session,  # type: ignore[arg-type]
        store,
        [uuid4()],
    )

    assert error is not None
    assert "missing from store" in error
    assert text == ""
    assert truncated is False


async def test_load_paper_text_truncates_overlong_text(tmp_path: Path) -> None:
    # A .txt upload (one block, whole file) is the honest way to exceed the
    # cap in a unit test -- a single fitz page cannot hold 60k+ characters.
    store = PrivateObjectStore(root=str(tmp_path))
    task_id = uuid4()
    huge = "long text " * (MAX_PAPER_TEXT_CHARS + 1000)
    stored = store.store_named(
        f"tasks/{task_id}", huge.encode(), suffix=".txt", content_type="text/plain"
    )
    session = _FakeScalarSession([(stored.object_key, "paper.txt")])

    text, truncated, error = await _load_paper_text(
        session,  # type: ignore[arg-type]
        store,
        [uuid4()],
    )

    assert error is None
    assert truncated is True
    assert len(text) <= MAX_PAPER_TEXT_CHARS + 200


# --- claims suggestion ----------------------------------------------------


def test_paper_review_suggests_rigor_and_sufficiency_claims() -> None:
    contract = make_research_contract()
    contract = ResearchContract.model_validate(
        {**contract.model_dump(mode="json"), "task_type": "paper_review"}
    )
    claims = suggest_atomic_claims(contract)
    assert len(claims) == 2
    statements = {claim.statement for claim in claims}
    assert any("论证严谨性" in s for s in statements)
    assert any("证据充分性" in s for s in statements)
    for claim in claims:
        assert claim.falsification_condition


def test_deep_research_keeps_original_claims() -> None:
    contract = make_research_contract()
    claims = suggest_atomic_claims(contract)
    assert len(claims) == 2
    assert all("关联主张" in c.statement or "因果主张" in c.statement for c in claims)


# --- review report parsing and rendering ----------------------------------


def _review_payload() -> dict[str, object]:
    return {
        "title": "对论文《屏幕时间与青少年抑郁》的审查报告",
        "paper_overview": {
            "title": "屏幕时间与青少年抑郁",
            "research_question": "屏幕时间是否导致青少年抑郁？",
            "main_claims": [
                {
                    "statement": "屏幕时间与抑郁相关",
                    "supporting_evidence": ["横断面调查显示 r=0.2"],
                }
            ],
        },
        "rigor_issues": [
            {
                "claim_ref": "屏幕时间与抑郁相关",
                "issue": "未控制混杂",
                "severity": "high",
            }
        ],
        "evidence_insufficiency": [
            {
                "claim_ref": "屏幕时间与抑郁相关",
                "missing_evidence": "缺少纵向证据",
                "suggested_evidence": "队列研究",
            }
        ],
        "improvement_suggestions": [
            {"claim_ref": "", "issue": "补充测量偏差分析"}
        ],
        "conclusion": "论文证据不足以支持因果结论。",
        "limitations": ["本次审查基于单一上传版本。"],
        "investigation_process": ["7 席全部参与"],
    }


def test_parse_valid_review_payload() -> None:
    report = paper_payload_to_dataclass(_review_payload())
    assert isinstance(report, PaperReviewReport)
    assert report.paper_overview.research_question == "屏幕时间是否导致青少年抑郁？"
    assert report.rigor_issues[0].severity == "high"
    assert report.evidence_insufficiency[0].suggested_evidence == "队列研究"
    assert report.conclusion.startswith("论文证据不足以支持")


def test_review_payload_dispatch_keeps_final_paper_parsing() -> None:
    from tests.unit.test_paper_synthesis import _paper_payload

    paper = paper_payload_to_dataclass(_paper_payload())
    assert isinstance(paper, object)  # FinalPaper shape: no paper_overview key
    assert "paper_overview" not in _paper_payload()


def test_parse_review_rejects_payload_without_conclusion() -> None:
    payload = _review_payload()
    del payload["conclusion"]
    with pytest.raises(ValueError, match="conclusion"):
        _parse_review_paper(payload)


def test_parse_review_tolerates_broken_issue_entries() -> None:
    payload = _review_payload()
    payload["rigor_issues"] = [
        {"issue": "ok"},
        {"issue": ""},
        "not a dict",
        {"claim_ref": "x"},
    ]
    report = _parse_review_paper(payload)
    assert len(report.rigor_issues) == 1
    assert report.rigor_issues[0].issue == "ok"
    assert report.rigor_issues[0].claim_ref is None


def test_review_payload_round_trip_through_ledger_shape() -> None:
    report = _parse_review_paper(_review_payload())
    stored = _review_payload_dict(report)
    # FrozenDict-free, JSON-serialisable plain dict for the ledger payload.
    parsed_back = _parse_review_paper(stored)
    assert parsed_back.conclusion == report.conclusion
    assert parsed_back.paper_overview.main_claims[0].statement == (
        report.paper_overview.main_claims[0].statement
    )


def test_render_review_markdown_covers_all_sections() -> None:
    report = _parse_review_paper(_review_payload())
    text = render_paper_markdown(report, uuid4(), "审查问题")
    assert "## 论文概况" in text
    assert "## 不严谨之处" in text
    assert "未控制混杂" in text
    assert "## 证据不充分之处" in text
    assert "缺少纵向证据" in text
    assert "## 改进建议" in text
    assert "补充测量偏差分析" in text
    assert "## 结论与局限" in text
    assert "论文证据不足以支持因果结论" in text
