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

**Scope, stated plainly.** This module only generates the six query strings.
Whether any of them actually resolves to a real source depends on a
search-capable adapter existing --
``packages.papers.candidate_pool.CandidatePool.add`` currently treats
anything without a DOI-shaped substring as unresolvable and records it as
such. A query produced here can therefore land in
``AcquisitionResult.unresolvable`` today, exactly like any other free-text
request; that is an honest gap (CLAUDE.md 7), not a failure of this module.
Intent generation is the whole of this mechanism's first version.

Because that non-resolution is constant and system-wide -- every one of
these six-per-claim queries, on every task, until a search-capable adapter
exists -- ``packages.council.rounds.registry.run_acquisition`` deliberately
does *not* fold it into that round's ``unfilled_slots``: a task-specific gap
and a permanent adapter-capability gap are different kinds of fact, and
conflating them would make ``TaskStatus.COMPLETED_WITH_GAPS`` mean nothing
for any task with confirmed claims. The attempt (and its resolved/unresolved
counts) instead stays visible on the audit trail through a dedicated
``ADVERSARIAL_RETRIEVAL_ATTEMPTED`` event -- CLAUDE.md 7's "admit unknown"
honored through visibility, not through gap-counting.
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
    claim's actual statement text, and to a real search hit, is a future
    search-capable adapter's job, not this pure function's.
    """
    return tuple(f"claim {claim_id}: {intent}" for intent in _INTENTS)


__all__ = ["adversarial_retrieval_queries"]
