from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.council.contracts import (
    ALL_SEATS,
    ChallengeResponseType,
    ScientificActionType,
    Seat,
)
from packages.council.rounds.base import (
    CompletionDecision,
    RoundContext,
    RoundEntry,
    RoundResult,
    TimeoutDecision,
)
from packages.council.rounds.precommitment import (
    PrecommitmentHandler,
    PrecommitmentNotSealed,
    PrecommitmentOutput,
)


@dataclass
class CouncilRuntime:
    task_id: UUID = field(default_factory=uuid4)
    precommitment_handler: PrecommitmentHandler = field(default_factory=PrecommitmentHandler)
    _action_log: list[dict[str, object]] = field(default_factory=list)

    async def submit_precommitment(
        self, seat: Seat, output: PrecommitmentOutput
    ) -> None:
        await self.precommitment_handler.seal()
        await self.precommitment_handler.submit(seat, output)

    async def read_all_precommitments(self) -> dict[Seat, PrecommitmentOutput]:
        return await self.precommitment_handler.read_all()

    def log_action(
        self,
        seat: Seat,
        action_type: ScientificActionType,
        target_id: UUID | None,
        statement: str,
    ) -> dict[str, object]:
        entry = {
            "seat": seat.value,
            "action": action_type.value,
            "target": str(target_id) if target_id else None,
            "statement": statement,
        }
        self._action_log.append(entry)
        return entry

    def respond_to_challenge(
        self,
        seat: Seat,
        response_type: ChallengeResponseType,
        target_id: UUID,
        statement: str,
    ) -> dict[str, object]:
        entry = {
            "seat": seat.value,
            "response": response_type.value,
            "target": str(target_id),
            "statement": statement,
        }
        self._action_log.append(entry)
        return entry

    def get_history(self) -> list[dict[str, object]]:
        return list(self._action_log)
