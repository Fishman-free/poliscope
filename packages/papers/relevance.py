"""Deterministic post-retrieval relevance filter (B5).

The seven seats generate free-text search intents, and a keyless provider
occasionally returns a hit that shares a stopword with the query but is about a
different subject. A model call to re-rank every hit would be another black box
and another cost, so this filter is deliberately *deterministic and auditable*:
it scores lexical overlap between the research context (the question plus the
confirmed atomic claims) and a candidate's title, and acquisition records every
candidate it drops -- a filtered paper is a ``RefusedCandidate`` with its exact
score, never a silent disappearance (CLAUDE.md 7: the unknown must stay visible).

Design constraints:

* **No model, no network.** Pure token-set math so the same inputs always give
  the same score and a unit test can pin the boundary.
* **CJK-aware.** Latin text tokenises on words; Chinese/Japanese/Korean text
  has no spaces, so it tokenises into character bigrams, which is what makes
  overlap non-trivial for the project's primary (Chinese) questions.
* **Fail-open, not fail-closed.** An empty context, an empty candidate, or a
  disabled threshold never drops anything -- a filter that cannot be computed
  must not quietly censor the evidence pool.
* **Corpus cutoff is separate.** :func:`within_cutoff` handles the A3
  publication-year constraint so acquisition can report *why* a candidate was
  excluded independently of topical relevance.
"""

from __future__ import annotations

import re
from collections.abc import Collection

# A candidate whose coverage score is at or above this is admitted. Chosen so a
# title that shares one meaningful content token with a focused question passes
# while a title sharing only stopwords does not; pinned by unit tests.
DEFAULT_RELEVANCE_THRESHOLD = 0.12

_LATIN_WORD = re.compile(r"[a-z0-9]+")
_CJK_CHAR = re.compile(r"[一-鿿぀-ヿ가-힯]")

# Minimal English stopword set: high-frequency tokens that carry no topic
# signal. Deliberately short and English-only -- CJK bigrams already make a
# single generic character pair unlikely to dominate.
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "was", "were", "be", "been", "by", "as", "at", "it",
        "this", "that", "these", "those", "from", "into", "about", "between",
        "study", "studies", "effect", "effects", "association", "associated",
        "relation", "relationship", "research", "analysis", "review",
        "we", "our", "their", "its", "not", "no", "do", "does", "did",
    }
)


def tokenize(text: str) -> frozenset[str]:
    """Lowercase a text into a set of latin words and CJK bigrams.

    A set (not a list) because relevance is about which concepts overlap, not
    how often a token repeats -- term frequency would let a long, repetitive
    off-topic title score higher than a short on-topic one.
    """
    if not text:
        return frozenset()
    lowered = text.lower()
    tokens: set[str] = set()
    for word in _LATIN_WORD.findall(lowered):
        if len(word) > 1 and word not in _STOPWORDS and not word.isdigit():
            tokens.add(word)
        elif word.isdigit() and len(word) == 4:
            # A bare four-digit number is usually a year, not a topic token.
            continue
    cjk = _CJK_CHAR.findall(lowered)
    for index in range(len(cjk) - 1):
        tokens.add(cjk[index] + cjk[index + 1])
    if len(cjk) == 1:
        # A single CJK character is still a usable signal.
        tokens.add(cjk[0])
    return frozenset(tokens)


def relevance_score(
    context: Collection[str],
    title: str,
    abstract: str | None = None,
) -> float:
    """Asymmetric coverage of a candidate's content tokens by the context.

    Returns the share of the candidate's (title + abstract) content tokens that
    also appear in the research context. Coverage -- rather than symmetric
    Jaccard -- is used because a question is long and a title is short; Jaccard
    would punish a perfectly on-topic title merely for being terse. Returns
    ``1.0`` only for a truly blank candidate (nothing to reject on); a
    stopwords-only candidate has zero topical overlap and scores ``0.0``, as
    does an empty context (no basis to judge, so the caller treats "no basis"
    as "do not filter" -- see :func:`is_topically_relevant`).
    """
    context_tokens: set[str] = set()
    for part in context:
        context_tokens.update(tokenize(part))
    candidate_tokens = set(tokenize(title))
    if abstract:
        candidate_tokens.update(tokenize(abstract))
    if not candidate_tokens:
        # A truly blank candidate carries nothing to judge (fail-open), but a
        # title whose words are ALL stopwords ("a study of the review") is not
        # blank -- it simply has zero topical overlap and must score 0, or
        # stopword-only hits would be admitted with a perfect 1.0 score.
        raw_text = f"{title} {abstract or ''}".strip()
        return 1.0 if not raw_text else 0.0
    if not context_tokens:
        return 0.0
    overlap = candidate_tokens & context_tokens
    return len(overlap) / len(candidate_tokens)


def is_topically_relevant(
    context: Collection[str],
    title: str,
    abstract: str | None = None,
    threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
) -> tuple[bool, float]:
    """Admit decision plus the exact score (for the audit record).

    Fail-open: an empty context admits everything, because there is no
    defensible basis to exclude on.
    """
    has_context = any(tokenize(part) for part in context)
    if not has_context:
        return True, 0.0
    score = relevance_score(context, title, abstract)
    return score >= threshold, score


def within_cutoff(publication_year: int | None, cutoff_year: int | None) -> bool:
    """Whether a source published in ``publication_year`` is inside the cutoff.

    Fail-open on both unknowns: no cutoff set, or a provider that did not
    report a year, means the candidate cannot be excluded on date grounds. An
    unknown publication date is admitted and remains visibly unknown rather
    than being guessed (CLAUDE.md 7).
    """
    if cutoff_year is None:
        return True
    if publication_year is None:
        return True
    return publication_year <= cutoff_year


__all__ = [
    "DEFAULT_RELEVANCE_THRESHOLD",
    "is_topically_relevant",
    "relevance_score",
    "tokenize",
    "within_cutoff",
]
