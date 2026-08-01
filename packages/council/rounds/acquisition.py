from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.base import RoundResult


@dataclass(frozen=True, slots=True)
class AcquisitionInput:
    seat: Seat
    requests: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcquisitionOutput:
    source_requests: tuple[UUID, ...]


@dataclass
class AcquisitionRound:
    _results: list[RoundResult] = field(default_factory=list)

    async def run(self, handler_seat: Seat, requests: tuple[str, ...]) -> AcquisitionOutput:
        refs = tuple(uuid4() for _ in requests)
        return AcquisitionOutput(source_requests=refs)
