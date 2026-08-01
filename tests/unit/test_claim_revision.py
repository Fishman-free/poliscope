from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from packages.council.claim_revision import ClaimRepository, revise_claim
from packages.evidence.contracts import ClaimRevision, ClaimStatus, ClaimType


def _broad_claim() -> ClaimRevision:
    return ClaimRevision(
        claim_id=uuid4(),
        revision=1,
        statement="screen time affects wellbeing",
        claim_type=ClaimType.CORRELATIONAL,
        scope={"population": "adolescents"},
        confidence=Decimal("0.5"),
        falsification_condition="null result in replication",
    )


def test_narrow_appends_revision_and_keeps_original() -> None:
    broad = _broad_claim()
    narrowed = revise_claim(
        broad,
        "NARROW",
        new_scope={"population": "adolescents", "region": "US"},
        new_confidence=Decimal("0.7"),
    )
    assert narrowed.revision == broad.revision + 1
    assert narrowed.supersedes_revision == broad.revision
    assert narrowed.status == ClaimStatus.NARROWED


def test_original_claim_preserved_after_revision() -> None:
    repo = ClaimRepository()
    broad = _broad_claim()
    repo.add(broad)
    narrowed = revise_claim(broad, "NARROW", new_scope={"x": 1})
    repo.add(narrowed)
    assert repo.get(broad.claim_id, broad.revision) == broad


def test_withdraw_marks_status_withdrawn() -> None:
    broad = _broad_claim()
    withdrawn = revise_claim(broad, "WITHDRAW")
    assert withdrawn.status == ClaimStatus.WITHDRAWN


def test_unanswered_fatal_challenge_blocks_claim() -> None:
    import asyncio

    from packages.council.contracts import Seat
    from packages.council.rounds.cross_examination import (
        ChallengeEntry,
        CrossExaminationHandler,
    )

    handler = CrossExaminationHandler()
    entry = ChallengeEntry(
        claim_id=uuid4(),
        challenger=Seat.ADVERSARY_FALSIFIER,
        target_seat=Seat.THEORY_BUILDER,
        challenge_statement="unreplicated",
        is_fatal=True,
    )
    result = asyncio.run(handler.on_timeout(entry))
    assert entry.claim_id in result.blocked_claim_ids
    assert entry.claim_id in result.unresolved_challenge_ids


def test_suite() -> None:
    test_narrow_appends_revision_and_keeps_original()
    test_original_claim_preserved_after_revision()
    test_withdraw_marks_status_withdrawn()
    test_unanswered_fatal_challenge_blocks_claim()
