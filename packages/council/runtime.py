from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.council.contracts import (
    ChallengeResponseType,
    ScientificActionType,
    Seat,
)
from packages.council.rounds.precommitment import (
    PrecommitmentHandler,
    PrecommitmentOutput,
)


@dataclass
class CouncilRuntime:
    """In-memory façade over one task's council interactions.

    This is the deterministic surface the evaluator and the unit tests drive; the
    durable path is the Scientific Event Ledger, written by
    :class:`packages.epistemo.orchestrator.CouncilOrchestrator`. The action log
    here is a convenience view, not a second source of truth, and nothing in it
    is evidence.
    """

    task_id: UUID = field(default_factory=uuid4)
    precommitment_handler: PrecommitmentHandler = field(
        default_factory=PrecommitmentHandler
    )
    _action_log: list[dict[str, object]] = field(default_factory=list)

    async def submit_precommitment(
        self, seat: Seat, output: PrecommitmentOutput
    ) -> None:
        """Record one seat's precommitment. Sealing is a separate, later step.

        This used to seal before submitting, which made the first call succeed,
        every later call raise, and the round unusable. Sealing is what makes the
        independence in CLAUDE.md 4 real -- it is the moment after which no seat
        can revise its judgment having seen another's -- so it belongs to the
        round, not to each submission.
        """
        await self.precommitment_handler.submit(seat, output)

    async def seal_precommitments(self) -> None:
        """Close submissions and make every seat's judgment readable."""
        await self.precommitment_handler.seal()

    async def read_all_precommitments(self) -> dict[Seat, PrecommitmentOutput]:
        return await self.precommitment_handler.read_all()

    def log_action(
        self,
        seat: Seat,
        action_type: ScientificActionType,
        target_id: UUID | None,
        statement: str,
    ) -> dict[str, object]:
        entry: dict[str, object] = {
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
        entry: dict[str, object] = {
            "seat": seat.value,
            "response": response_type.value,
            "target": str(target_id),
            "statement": statement,
        }
        self._action_log.append(entry)
        return entry

    def get_history(self) -> list[dict[str, object]]:
        return list(self._action_log)
