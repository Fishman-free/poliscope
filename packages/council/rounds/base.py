from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from packages.council.contracts import Seat


@dataclass(frozen=True, slots=True)
class RoundContext:
    task_id: UUID
    phase: str
    seats: tuple[Seat, ...]


@dataclass(frozen=True, slots=True)
class RoundEntry:
    seat: Seat
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class RoundResult:
    seat: Seat
    output: dict[str, object]
    completed: bool = True


@dataclass(frozen=True, slots=True)
class CompletionDecision:
    task_status: str
    next_phase: str | None = None
    missing_output: str | None = None


@dataclass(frozen=True, slots=True)
class TimeoutDecision:
    task_status: str
    missing_output: str | None = None


class RoundHandler(Protocol):
    async def enter(self, context: RoundContext) -> RoundEntry | None: ...
    async def run(self, entry: RoundEntry) -> RoundResult: ...
    async def on_timeout(self, entry: RoundEntry | None) -> TimeoutDecision: ...
    async def complete(self, results: tuple[RoundResult, ...]) -> CompletionDecision: ...
