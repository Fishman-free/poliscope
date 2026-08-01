from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from packages.research.contracts import EvidenceDemandType


@dataclass(frozen=True, slots=True)
class EvidenceDemandSlot:
    demand_type: EvidenceDemandType
    priority: int
    requested_by: tuple[str, ...]
    satisfied_by: tuple[UUID, ...]
    status: str = "open"
    gap_reason: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceDemandMatrix:
    slots: tuple[EvidenceDemandSlot, ...]

    @property
    def demand_types(self) -> frozenset[EvidenceDemandType]:
        return frozenset(slot.demand_type for slot in self.slots)

    def fill_slot(
        self, demand_type: EvidenceDemandType, source_id: UUID
    ) -> EvidenceDemandMatrix:
        new_slots = []
        for slot in self.slots:
            if slot.demand_type == demand_type and slot.status == "open":
                new_slots.append(
                    EvidenceDemandSlot(
                        demand_type=slot.demand_type,
                        priority=slot.priority,
                        requested_by=slot.requested_by,
                        satisfied_by=slot.satisfied_by + (source_id,),
                        status="filled",
                        gap_reason=slot.gap_reason,
                    )
                )
            else:
                new_slots.append(slot)
        return EvidenceDemandMatrix(slots=tuple(new_slots))


def build_default_matrix() -> EvidenceDemandMatrix:
    return EvidenceDemandMatrix(
        slots=tuple(
            EvidenceDemandSlot(
                demand_type=demand_type,
                priority=idx + 1,
                requested_by=("council",),
                satisfied_by=(),
                status="open",
            )
            for idx, demand_type in enumerate(EvidenceDemandType)
        )
    )
