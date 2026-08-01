from __future__ import annotations

from packages.reports.safety import apply_safety_notice


def test_mental_health_report_contains_required_safety_statement() -> None:
    text = apply_safety_notice(
        "Original report content",
        is_mental_health=True,
    )
    assert len(text) > 0
    assert "AI" in text or "科研" in text
    assert "局限" in text


def test_non_mental_health_report_unchanged() -> None:
    original = "Some economics report"
    result = apply_safety_notice(original, is_mental_health=False)
    assert result == original


def test_report_export_does_not_leak_pdf_or_signed_url() -> None:
    from packages.reports.safety import sanitize_export
    dirty = "See file:///path/to/doc.pdf and https://s3.example.com/doc.pdf?X-Amz-Signature=abc"
    clean = sanitize_export(dirty)
    assert "file://" not in clean
    assert "X-Amz-Signature" not in clean
