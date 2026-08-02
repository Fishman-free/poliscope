"""Unit tests for the inter-annotator agreement math.

``packages.evaluation.agreement`` draws a hard line: the statistics
(``cohen_kappa``, ``krippendorff_alpha_nominal``) are fully implemented and
tested against synthetic rater data here, while ``load_human_annotations``
refuses -- loudly, via an exception -- to fabricate the human judgments no
annotation pipeline has collected yet (CLAUDE.md 7). These tests hold that
line: they exercise the math, and separately assert the loader still refuses.
"""

from __future__ import annotations

import pytest

from packages.evaluation.agreement import (
    HumanAnnotationsNotCollected,
    cohen_kappa,
    krippendorff_alpha_nominal,
    load_human_annotations,
)

# --- cohen_kappa -------------------------------------------------------


def test_cohen_kappa_perfect_agreement_is_one() -> None:
    rater_a = ["confounding", "measurement_bias", "confounding", "reverse_causation"]
    rater_b = list(rater_a)
    assert cohen_kappa(rater_a, rater_b) == 1.0


def test_cohen_kappa_chance_level_agreement_is_near_zero() -> None:
    # Two raters each split 50/50 between two labels, agreeing only as often
    # as chance predicts (half the time).
    rater_a = ["x", "x", "y", "y"]
    rater_b = ["x", "y", "x", "y"]
    assert cohen_kappa(rater_a, rater_b) == pytest.approx(0.0, abs=1e-9)


def test_cohen_kappa_hand_computed_partial_agreement() -> None:
    # observed agreement = 3/4; marginals: A has 3 "x" 1 "y", B has 2 "x" 2 "y".
    # expected = (3/4)(2/4) + (1/4)(2/4) = 6/16 + 2/16 = 0.5
    # kappa = (0.75 - 0.5) / (1 - 0.5) = 0.5
    rater_a = ["x", "x", "x", "y"]
    rater_b = ["x", "x", "y", "y"]
    assert cohen_kappa(rater_a, rater_b) == pytest.approx(0.5, abs=1e-9)


def test_cohen_kappa_mismatched_lengths_raises() -> None:
    with pytest.raises(ValueError):
        cohen_kappa(["x"], ["x", "y"])


def test_cohen_kappa_empty_raises() -> None:
    with pytest.raises(ValueError):
        cohen_kappa([], [])


# --- krippendorff_alpha_nominal -----------------------------------------


def test_krippendorff_alpha_perfect_agreement_is_one() -> None:
    coders = [
        ["a", "b", "a", "b"],
        ["a", "b", "a", "b"],
        ["a", "b", "a", "b"],
    ]
    assert krippendorff_alpha_nominal(coders) == 1.0


def test_krippendorff_alpha_all_identical_labels_is_one() -> None:
    coders = [["a", "a", "a"], ["a", "a", "a"]]
    assert krippendorff_alpha_nominal(coders) == 1.0


def test_krippendorff_alpha_handles_missing_values() -> None:
    coders = [
        ["a", "b", None, "a"],
        ["a", "b", "a", None],
        [None, "b", "a", "a"],
    ]
    # Every non-missing value agrees within its item, so this should still be
    # perfect agreement even with gaps scattered across coders.
    assert krippendorff_alpha_nominal(coders) == 1.0


def test_krippendorff_alpha_mismatched_item_counts_raises() -> None:
    with pytest.raises(ValueError):
        krippendorff_alpha_nominal([["a", "b"], ["a"]])


def test_krippendorff_alpha_no_coders_raises() -> None:
    with pytest.raises(ValueError):
        krippendorff_alpha_nominal([])


def test_krippendorff_alpha_fewer_than_two_labelled_values_raises() -> None:
    with pytest.raises(ValueError):
        krippendorff_alpha_nominal([["a", None], [None, None]])


# --- load_human_annotations ----------------------------------------------


def test_load_human_annotations_refuses_to_fabricate_data() -> None:
    with pytest.raises(HumanAnnotationsNotCollected):
        load_human_annotations("screen_time_mh_2024")
