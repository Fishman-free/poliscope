"""Unit tests for the deterministic post-retrieval relevance filter (B5)."""

from __future__ import annotations

from packages.papers.relevance import (
    DEFAULT_RELEVANCE_THRESHOLD,
    is_topically_relevant,
    tokenize,
    within_cutoff,
)


def test_cjk_bigrams_make_overlap_detectable() -> None:
    context = ["社交媒体使用与青少年抑郁症状的纵向关系研究"]
    # Shares the bigram 抑郁 / 社交 with the question.
    title = "社交媒体使用对青少年抑郁的影响"
    admitted, score = is_topically_relevant(context, title)
    assert admitted
    assert score > 0


def test_off_topic_title_is_rejected() -> None:
    context = ["social media use and adolescent depression longitudinal"]
    title = "Stock market volatility and macroeconomic monetary policy"
    admitted, score = is_topically_relevant(context, title)
    assert not admitted
    assert score < DEFAULT_RELEVANCE_THRESHOLD


def test_empty_context_fails_open() -> None:
    # No basis to judge -> admit, never silently censor.
    admitted, score = is_topically_relevant(["", "  "], "anything at all")
    assert admitted
    assert score == 0.0


def test_stopwords_are_not_topic_signal() -> None:
    context = ["a study of the association between sleep and anxiety"]
    # Only stopwords overlap ("study", "association", "the", "of", "and").
    title = "the study and review of an association"
    tokens = tokenize(title)
    assert "study" not in tokens
    assert "association" not in tokens
    # Stopwords carry no topic signal, so the shared stopwords must not be
    # enough to admit the title against a substantive context.
    admitted, score = is_topically_relevant(context, title)
    assert not admitted
    assert score == 0.0


def test_abstract_can_rescue_a_terse_title() -> None:
    context = ["smartphone screen time anxiety adolescents"]
    terse = "A cohort report"
    with_abstract = is_topically_relevant(
        context, terse, "screen time and anxiety in adolescent cohorts"
    )
    without_abstract = is_topically_relevant(context, terse)
    assert with_abstract[1] > without_abstract[1]


def test_within_cutoff_fails_open_on_unknowns() -> None:
    assert within_cutoff(None, 2020) is True
    assert within_cutoff(2019, None) is True
    assert within_cutoff(2021, 2020) is False
    assert within_cutoff(2020, 2020) is True
