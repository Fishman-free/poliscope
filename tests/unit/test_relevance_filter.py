"""Unit tests for the deterministic post-retrieval relevance filter (B5)."""

from __future__ import annotations

from packages.papers.relevance import (
    CROSS_SCRIPT_SCORE,
    DEFAULT_RELEVANCE_THRESHOLD,
    is_topically_relevant,
    shares_a_script,
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


def test_chinese_context_admits_english_title_cross_script() -> None:
    """Production bug: Chinese questions had every relevant English hit
    recorded as a miss because lexical overlap across scripts is impossible."""
    context = ["中国青少年频繁的抑郁症来源于什么，社交媒体还是学业压力"]
    title = (
        "Problematic mobile phone use and depressive symptoms in "
        "adolescents: a longitudinal cohort study"
    )
    admitted, score = is_topically_relevant(context, title)
    assert admitted
    assert score == CROSS_SCRIPT_SCORE


def test_cross_script_detection_helper() -> None:
    cjk = tokenize("青少年抑郁")
    latin = tokenize("adolescent depression")
    assert not shares_a_script(cjk, latin)
    assert shares_a_script(tokenize("depression 抑郁"), latin)


def test_mixed_context_still_screens_english_titles() -> None:
    # A Chinese question plus an English atomic claim has latin tokens to
    # compare against an English title, so normal screening applies: an
    # off-topic title is still rejected.
    context = [
        "中国青少年抑郁来源",
        "problematic mobile phone use predicts adolescent depression",
    ]
    off_topic = "Stock market volatility and monetary policy transmission"
    admitted, _ = is_topically_relevant(context, off_topic)
    assert not admitted
    on_topic = (
        "Mobile phone addiction and depressive symptoms among adolescents"
    )
    admitted_on, score = is_topically_relevant(context, on_topic)
    assert admitted_on
    assert score >= 0
