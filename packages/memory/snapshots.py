from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    id: UUID
    agent_snapshots: dict[str, dict[str, object]]
    process_graph_version: int
    task_phase: str
    budget: dict[str, object]
    unresolved_challenges: tuple[UUID, ...]
    projector_checkpoint: int


def validate_snapshot(snapshot: MemorySnapshot, expected_hash: str | None = None) -> bool:
    if expected_hash is None:
        return True
    canonical = json.dumps(snapshot.__dict__, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest() == expected_hash
