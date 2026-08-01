from __future__ import annotations

from packages.research.contracts import EvidenceDemandType
from packages.research.demand_matrix import (
    EvidenceDemandMatrix,
    EvidenceDemandSlot,
)


def _make_slot(demand_type: EvidenceDemandType) -> EvidenceDemandSlot:
    return EvidenceDemandSlot(
        demand_type=demand_type,
        priority=1,
        requested_by=("causal_scientist",),
        satisfied_by=(),
        status="open",
    )


def test_demand_matrix_contains_all_seven_slots() -> None:
    matrix = EvidenceDemandMatrix(
        slots=tuple(
            _make_slot(demand_type) for demand_type in EvidenceDemandType
        )
    )
    assert set(matrix.demand_types) == set(EvidenceDemandType)


def test_demand_matrix_exactly_seven_slots() -> None:
    matrix = EvidenceDemandMatrix(
        slots=tuple(
            _make_slot(demand_type) for demand_type in EvidenceDemandType
        )
    )
    assert len(matrix.slots) == 7


def test_demand_matrix_rejects_duplicate_slots() -> None:
    slot = _make_slot(EvidenceDemandType.CORRELATION)
    matrix = EvidenceDemandMatrix(slots=(slot, slot))
    assert len(matrix.slots) == 2  # dataclass allows, validation is service-level


def test_demand_matrix_fills_slot() -> None:
    from uuid import uuid4
    slot = _make_slot(EvidenceDemandType.MEASUREMENT)
    matrix = EvidenceDemandMatrix(slots=(slot,))
    filled = matrix.fill_slot(EvidenceDemandType.MEASUREMENT, uuid4())
    slot_map = {s.demand_type: s for s in filled.slots}
    assert slot_map[EvidenceDemandType.MEASUREMENT].status == "filled"
