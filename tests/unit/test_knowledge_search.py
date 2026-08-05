"""Pure functions behind knowledge-base keyword search."""

from __future__ import annotations

from packages.knowledge.search import _ilike_count, _snippet

TEXT = (
    "Adolescent social media use and mental health: a longitudinal cohort "
    "study. Self-reported screen time was measured weekly. The association "
    "with depressive symptoms was small but consistent."
)


def test_ilike_count_matches_substring_case_insensitively() -> None:
    assert _ilike_count(TEXT, "social media") == 1
    assert _ilike_count(TEXT, "was") == 2
    assert _ilike_count("中文知识库文档", "知识库") == 1
    assert _ilike_count("abc", "abc") == 1
    # Overlapping matches count once each, like the SQL length arithmetic.
    assert _ilike_count("aaaa", "aa") == 2


def test_ilike_count_empty_query_matches_nothing() -> None:
    assert _ilike_count(TEXT, "") == 0
    assert _ilike_count("", "x") == 0


def test_snippet_centers_on_first_hit() -> None:
    snippet = _snippet(TEXT, "screen time")
    assert "screen time" in snippet
    # A 150-char window around the hit should not start at the document's
    # first character when the hit is mid-text.
    assert not snippet.startswith("Adolescent")


def test_snippet_without_hit_returns_prefix() -> None:
    assert _snippet(TEXT, "nonexistent") == TEXT[:150]


def test_snippet_ellipsizes_when_window_is_internal() -> None:
    snippet = _snippet(TEXT, "screen time", width=40)
    assert snippet.startswith("…")
    assert snippet.endswith("…")
    assert "screen time" in snippet


def test_snippet_short_text_is_unchanged() -> None:
    assert _snippet("tiny", "tiny") == "tiny"
