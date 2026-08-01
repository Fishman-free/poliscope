from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.base import (
    CompletionDecision,
    RoundContext,
    RoundEntry,
    RoundResult,
    TimeoutDecision,
)


class PrecommitmentNotSealed(Exception):
    """Raised when reading precommitments before seal."""


@dataclass(frozen=True, slots=True)
class PrecommitmentInput:
    confirmed_claims: tuple[UUID, ...]
    task_recall: str


@dataclass(frozen=True, slots=True)
class PrecommitmentOutput:
    initial_judgment: str
    confidence: float
    blindspots: tuple[str, ...] = ()
    update_condition: str = ""


@dataclass
class PrecommitmentHandler:
    _submissions: dict[Seat, PrecommitmentOutput] = field(default_factory=dict)
    _sealed: bool = False

    async def submit(self, seat: Seat, output: PrecommitmentOutput) -> None:
        if self._sealed:
            raise PrecommitmentNotSealed("already sealed")
        self._submissions[seat] = output

    async def seal(self) -> None:
        self._sealed = True

    async def read_all(self) -> dict[Seat, PrecommitmentOutput]:
        if not self._sealed:
            raise PrecommitmentNotSealed("precommitments not yet sealed")
        return dict(self._submissions)

    def task_status(self) -> str:
        if len(self._submissions) == 7:
            return "ready_to_run"
        if len(self._submissions) > 0:
            return "degraded_running"
        return "pending"
