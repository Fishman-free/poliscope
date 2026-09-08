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
* **Cross-script fail-open.** Academic indexes return English titles for
  Chinese questions. A purely lexical filter has *no* common tokens to compare
  across scripts, so a CJK-only context versus a latin-only candidate is
  unjudgeable, not irrelevant: such a candidate is admitted (the same
  fail-open principle as an empty context) instead of being faked as a miss.
* **Fail-open, not fail-closed.** An empty context, an empty candidate, a
  cross-script pair, or a disabled threshold never drops anything -- a filter
  that cannot be computed must not quietly censor the evidence pool.
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
# A token produced by ``tokenize`` is CJK-derived (a character bigram or single
# character) when it contains at least one CJK character; everything else is a
# latin word. Used to detect the cross-script case lexical overlap cannot judge.
_CJK_TOKEN = re.compile(r"[一-鿿぀-ヿ가-힯]")

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


def _scripts_of(tokens: Collection[str]) -> frozenset[str]:
    """Which writing systems a token set draws on: ``"cjk"`` and/or ``"latin"``."""
    scripts: set[str] = set()
    for token in tokens:
        scripts.add("cjk" if _CJK_TOKEN.search(token) else "latin")
    return frozenset(scripts)


def shares_a_script(context_tokens: Collection[str], candidate_tokens: Collection[str]) -> bool:
    """Whether the two token sets have at least one writing system in common.

    When they do not (e.g. a Chinese question and an English-only title), no
    lexical overlap can ever exist -- however on-topic the candidate is -- so
    coverage math is meaningless and the caller must fail open.
    """
    return bool(_scripts_of(context_tokens) & _scripts_of(candidate_tokens))


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


# Sentinel returned when context and candidate use disjoint writing systems:
# lexical screening has no basis to decide, so the candidate is admitted. A
# negative score can never be confused with a real coverage in [0, 1].
CROSS_SCRIPT_SCORE = -1.0


def is_topically_relevant(
    context: Collection[str],
    title: str,
    abstract: str | None = None,
    threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
) -> tuple[bool, float]:
    """Admit decision plus the exact score (for the audit record).

    Fail-open: an empty context admits everything, because there is no
    defensible basis to exclude on. A context and a candidate that share no
    writing system (a Chinese question and an English-only title) likewise
    admit with :data:`CROSS_SCRIPT_SCORE`: token overlap is structurally
    impossible across scripts, so a zero there means "cannot judge", never
    "off-topic". This is the production fix for Chinese questions whose
    perfectly relevant English hits were all recorded as misses.
    """
    context_tokens: set[str] = set()
    for part in context:
        context_tokens.update(tokenize(part))
    if not context_tokens:
        return True, 0.0
    candidate_tokens = set(tokenize(title))
    if abstract:
        candidate_tokens.update(tokenize(abstract))
    if not candidate_tokens:
        raw_text = f"{title} {abstract or ''}".strip()
        return (True, 0.0) if not raw_text else (False, 0.0)
    if not shares_a_script(context_tokens, candidate_tokens):
        return True, CROSS_SCRIPT_SCORE
    score = len(candidate_tokens & context_tokens) / len(candidate_tokens)
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
    "CROSS_SCRIPT_SCORE",
    "DEFAULT_RELEVANCE_THRESHOLD",
    "is_topically_relevant",
    "relevance_score",
    "shares_a_script",
    "tokenize",
    "within_cutoff",
]
