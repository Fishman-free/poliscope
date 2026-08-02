"""Inter-annotator agreement statistics -- an explicitly incomplete skeleton.

Design spec 11 calls for human-graded Blindspot Recall/Precision, validated
against inter-annotator agreement (Cohen's Kappa for two raters, Krippendorff's
Alpha for more). CLAUDE.md 7 forbids fabricating a measured quantity, and no
annotation pipeline exists yet to collect real human judgments of a
baseline's blindspot statements -- that is separate product work (an
annotation UI, a recruiting/rater-training process, none of which is in this
module's scope), not a missing formula.

So this module draws a hard line: the two statistics below are fully
implemented and unit-tested against synthetic rater data, because the *math*
is correct and reusable the moment real annotations exist. What this module
does NOT do is invent, load, or simulate human annotation data -- that is
what :func:`load_human_annotations` refuses to do, loudly, rather than
returning an empty or fabricated result that :mod:`packages.evaluation.scoring`
or a report could mistake for "zero agreement" instead of "not collected".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence


class HumanAnnotationsNotCollected(Exception):
    """Raised by :func:`load_human_annotations` -- there is nothing to load.

    Distinct from returning ``None``/``[]``: a caller that forgot to check for
    an empty result could silently compute an agreement score of "1.0" (or
    ZeroDivisionError) over zero raters, either of which would misrepresent an
    uncollected measurement as data (CLAUDE.md 7).
    """


def load_human_annotations(case_id: str) -> Sequence[Sequence[str]]:
    """Not implemented on purpose -- see the module docstring.

    Once a real annotation pipeline exists, this should return one sequence of
    labels per rater, aligned by item, for the given eval case.
    """
    raise HumanAnnotationsNotCollected(
        f"no human annotation pipeline exists yet for case {case_id!r}; "
        "see packages/evaluation/agreement.py's module docstring"
    )


def cohen_kappa(rater_a: Sequence[str], rater_b: Sequence[str]) -> float:
    """Cohen's Kappa for two raters labelling the same ordered items.

    Standard unweighted form: ``(observed_agreement - expected_agreement) /
    (1 - expected_agreement)``, with expected agreement computed from each
    rater's marginal label frequencies. Returns ``1.0`` when both raters agree
    on every item and neither marginal has any spread (expected agreement
    would otherwise be undefined at ``1.0``, and perfect observed agreement
    should not read as an undefined score).
    """
    if len(rater_a) != len(rater_b):
        raise ValueError("raters must label the same number of items")
    n = len(rater_a)
    if n == 0:
        raise ValueError("no items to score")
    observed = sum(1 for a, b in zip(rater_a, rater_b, strict=True) if a == b) / n
    counts_a = Counter(rater_a)
    counts_b = Counter(rater_b)
    labels = set(counts_a) | set(counts_b)
    expected = sum((counts_a[label] / n) * (counts_b[label] / n) for label in labels)
    if expected >= 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


def krippendorff_alpha_nominal(coders: Sequence[Sequence[str | None]]) -> float:
    """Krippendorff's Alpha for nominal labels, any number of coders, missing allowed.

    ``coders[i][j]`` is coder ``i``'s label for item ``j``, or ``None`` when
    that coder did not label that item. Uses the standard nominal-metric
    definition: ``1 - observed_disagreement / expected_disagreement``, both
    computed over all pairable (coder, coder) values within each item.
    """
    if not coders:
        raise ValueError("no coders to score")
    item_count = len(coders[0])
    if any(len(row) != item_count for row in coders):
        raise ValueError("every coder must report the same number of items")

    # Each item's non-missing labels form one unit; a unit with fewer than two
    # labels contributes nothing to either sum (no pair to compare).
    per_item_labels: list[list[str]] = [
        [label for row in coders if (label := row[j]) is not None]
        for j in range(item_count)
    ]

    all_labels = [label for labels in per_item_labels for label in labels]
    total_pairable = sum(len(labels) for labels in per_item_labels)
    if total_pairable < 2:
        raise ValueError("fewer than two labelled values; alpha is undefined")

    observed_disagreement = 0.0
    for labels in per_item_labels:
        m = len(labels)
        if m < 2:
            continue
        mismatches = sum(1 for a in labels for b in labels if a != b)
        observed_disagreement += mismatches / (m - 1)
    observed_disagreement /= total_pairable

    label_counts = Counter(all_labels)
    n = len(all_labels)
    expected_disagreement = sum(
        label_counts[c] * label_counts[k]
        for c in label_counts
        for k in label_counts
        if c != k
    ) / (n * (n - 1))

    if expected_disagreement == 0:
        # Every rated value is identical: no disagreement is possible, so
        # agreement is perfect rather than undefined.
        return 1.0
    return 1 - observed_disagreement / expected_disagreement


__all__ = [
    "HumanAnnotationsNotCollected",
    "cohen_kappa",
    "krippendorff_alpha_nominal",
    "load_human_annotations",
]
