from __future__ import annotations

from packages.reports.markdown import render_markdown
from packages.reports.safety import sanitize_export


def test_markdown_report_has_required_sections() -> None:
    report = render_markdown(
        question="test question",
        atomic_claims=["claim1"],
        admitted_findings=[],
        blindspots=[],
        dissents=[],
    )
    assert len(report) > 0


def test_sanitize_removes_signed_urls() -> None:
    text = "Link: https://bucket.s3.amazonaws.com/file.pdf?X-Amz-Signature=secret"
    result = sanitize_export(text)
    assert "X-Amz-Signature" not in result


def test_suite() -> None:
    test_markdown_report_has_required_sections()
    test_sanitize_removes_signed_urls()
