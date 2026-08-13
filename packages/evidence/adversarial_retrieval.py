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

# The six reverse-search intents design spec 7.9 names. English academic
# phrases -- OpenAlex / Crossref / Semantic Scholar index English metadata;
# a Chinese intent glued to a claim UUID (the previous form) is a random
# keyword hit and is how a question about love retrieved nuclear-plant papers.
_INTENTS: tuple[str, ...] = (
    "contradictory evidence refutation",
    "null result non-significant effect",
    "alternative theory mechanism",
    "construct validity measurement critique",
    "failed replication",
    "boundary condition reversal moderator",
)

# Keep a search phrase short enough that vendors treat it as a topic, not an
# essay. The claim id is deliberately *not* interpolated: UUIDs have no
# bibliographic meaning and pollute ranking.
_MAX_TOPIC_CHARS = 160


def adversarial_retrieval_queries(
    claim_id: UUID,
    statement: str = "",
    question: str = "",
) -> tuple[str, ...]:
    """Six reverse-search-intent query strings for one confirmed claim.

    ``claim_id`` is kept in the signature so callers and the audit trail can
    still attribute the six queries to a claim; it is not part of the search
    string. The topic is the claim statement (falling back to the research
    question) so the vendor is asked about the *science*, not an opaque id.
    """
    topic = (statement or "").strip() or (question or "").strip()
    topic = " ".join(topic.split())[:_MAX_TOPIC_CHARS]
    if not topic:
        return tuple(_INTENTS)
    return tuple(f"{topic} {intent}" for intent in _INTENTS)


__all__ = ["adversarial_retrieval_queries"]
