from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


class CheckpointRegressionError(RuntimeError):
    """Raised when a snapshot would move the projector checkpoint backwards."""


@dataclass(frozen=True, slots=True)
class TaskState:
    task_id: UUID
    phase: str
    projector_checkpoint: int
    unresolved_challenges: tuple[UUID, ...]


def restore_task_state(state: TaskState, snapshot: dict[str, object]) -> TaskState:
    """Restore task state from a snapshot, validating checkpoint non-regression."""
    restored_checkpoint = snapshot.get("projector_checkpoint", 0)
    if restored_checkpoint < state.projector_checkpoint:
        raise CheckpointRegressionError(
            f"checkpoint regression: {restored_checkpoint} "
            f"< {state.projector_checkpoint}"
        )
    return TaskState(
        task_id=state.task_id,
        phase=snapshot.get("phase", state.phase),
        projector_checkpoint=restored_checkpoint,
        unresolved_challenges=tuple(snapshot.get("unresolved_challenges", ())),
    )
