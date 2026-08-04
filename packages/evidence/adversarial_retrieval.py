"""Adversarial retrieval (design spec 7.9, CLAUDE.md 7.4, mechanism 4 of 4).

The other three anti-shared-error mechanisms constrain how already-acquired
evidence is judged (``source_diversity.py`` in this package), extracted
(dual extraction in ``packages.papers.finding_extraction``), or displayed (the
blind-review denylist in ``packages.council.deliberation``). This one instead
widens what gets asked for in the first place: alongside whatever a seat
actually requested, the ACQUISITION round appends six reverse-search-intent
queries per confirmed claim, attributed to the adversarial falsifier seat, so
acquisition itself is not structurally biased toward finding only what
supports the judgment already on the table.

**Scope, stated plainly.** This module generates the six query strings; it
does not itself search anything. Resolving a string to a real source is
``packages.papers.acquisition.SourceAcquisition``'s job:
``packages.papers.candidate_pool.CandidatePool.add`` finds no DOI-shaped
substring in these (they name a claim id and an intent, not a paper), so each
one is tried as free text against every free, keyless search adapter --
OpenAlex, Crossref, Semantic Scholar, in that order (see
``packages.tools.adapters.SEARCH_ADAPTER_NAMES``) -- and the first hit wins.
A query genuinely without a matching paper in any of those three indexes, or
one only a paid/keyed provider would cover, still lands in
``AcquisitionResult.unresolvable``; that remainder is an honest gap
(CLAUDE.md 7), not a failure of this module, and not every one of the six
strings, every time, as in this mechanism's first version.

Because some non-resolution is still expected system-wide -- the free
providers do not index everything a paid one would --
``packages.council.rounds.registry.run_acquisition`` deliberately does *not*
fold these queries' outcome into that round's ``unfilled_slots``: a
task-specific gap and an adapter-coverage gap are different kinds of fact,
and conflating them would make ``TaskStatus.COMPLETED_WITH_GAPS`` mean
nothing for any task with confirmed claims. The attempt (and its real
resolved/unresolved/refused counts) instead stays visible on the audit trail
through a dedicated ``ADVERSARIAL_RETRIEVAL_ATTEMPTED`` event -- CLAUDE.md 7's
"admit unknown" honored through visibility, not through gap-counting.
"""

from __future__ import annotations

from uuid import UUID

# The six reverse-search intents design spec 7.9 names, in the order it lists
# them. Kept as a plain tuple rather than a StrEnum: nothing outside this
# module branches on which intent is which -- the point is coverage of all
# six, not dispatch on any one of them.
_INTENTS: tuple[str, ...] = (
    "反驳该主张的证据",
    "零结果或不显著效应",
    "替代理论或替代机制",
    "测量批评（构念效度质疑）",
    "复现失败的报告",
    "边界反转情形",
)


def adversarial_retrieval_queries(claim_id: UUID) -> tuple[str, ...]:
    """Six reverse-search-intent query strings for one confirmed claim.

    Each string names the claim by id -- the only handle available at query-
    planning time -- and one adversarial intent. Resolving the id to the
    claim's actual statement text is out of scope here; the string is instead
    tried as-is against real search adapters downstream in
    ``SourceAcquisition.acquire`` -- see the module docstring above.
    """
    return tuple(f"claim {claim_id}: {intent}" for intent in _INTENTS)


__all__ = ["adversarial_retrieval_queries"]
