"""Vendor-safe search-query sanitizer (off-topic retrieval regression)."""

from __future__ import annotations

from uuid import uuid4

from packages.papers.query_sanitize import sanitize_search_query


def test_strips_cjk_and_keeps_english_keywords() -> None:
    cleaned = sanitize_search_query("爱情是否是人类的必需品 love necessity")
    assert cleaned is not None
    assert "love" in cleaned.lower()
    assert "爱情" not in cleaned


def test_strips_claim_uuid_prefix() -> None:
    claim_id = uuid4()
    cleaned = sanitize_search_query(f"claim {claim_id}: contradictory evidence")
    assert cleaned == "contradictory evidence"


def test_preserves_a_doi() -> None:
    raw = "doi:10.1038/s41562-020-00965-x 核对其中是否包含反向因果"
    cleaned = sanitize_search_query(raw)
    assert cleaned == raw


def test_pure_cjk_is_an_honest_miss() -> None:
    assert sanitize_search_query("反驳该主张的证据") is None


def test_empty_is_an_honest_miss() -> None:
    assert sanitize_search_query("   ") is None
