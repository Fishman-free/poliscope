from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from packages.council.contracts import Seat


@dataclass(frozen=True, slots=True)
class ChallengeEntry:
    claim_id: UUID
    challenger: Seat
    target_seat: Seat
    challenge_statement: str
    is_fatal: bool = False


@dataclass(frozen=True, slots=True)
class CrossExaminationResult:
    blocked_claim_ids: tuple[UUID, ...] = ()
    unresolved_challenge_ids: tuple[UUID, ...] = ()


@dataclass
class CrossExaminationHandler:
    _blocked: list[UUID] = field(default_factory=list)
    _unresolved: list[UUID] = field(default_factory=list)

    async def on_timeout(self, entry: ChallengeEntry) -> CrossExaminationResult:
        if entry.is_fatal:
            self._blocked.append(entry.claim_id)
            self._unresolved.append(entry.claim_id)
        return CrossExaminationResult(
            blocked_claim_ids=tuple(self._blocked),
            unresolved_challenge_ids=tuple(self._unresolved),
        )

    async def submit_challenge(self, entry: ChallengeEntry) -> None:
        if entry.is_fatal:
            self._blocked.append(entry.claim_id)
