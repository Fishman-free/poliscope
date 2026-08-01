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


def _uuids(value: object) -> tuple[UUID, ...]:
    """Read challenge ids out of an untrusted snapshot.

    A snapshot is JSON that has been round-tripped through storage, so its
    challenge ids arrive as strings. Dropping an unparseable one silently would
    lose an unresolved challenge, and CLAUDE.md 6 requires a restore to preserve
    exactly those, so anything unreadable raises instead.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    parsed: list[UUID] = []
    for item in value:
        if isinstance(item, UUID):
            parsed.append(item)
            continue
        try:
            parsed.append(UUID(str(item)))
        except ValueError as error:
            raise ValueError(
                f"snapshot holds an unreadable challenge id {item!r}"
            ) from error
    return tuple(parsed)


def restore_task_state(state: TaskState, snapshot: dict[str, object]) -> TaskState:
    """Restore task state from a snapshot, validating checkpoint non-regression."""
    raw_checkpoint = snapshot.get("projector_checkpoint", 0)
    if not isinstance(raw_checkpoint, int):
        raise CheckpointRegressionError(
            f"snapshot checkpoint is not an integer: {raw_checkpoint!r}"
        )
    if raw_checkpoint < state.projector_checkpoint:
        raise CheckpointRegressionError(
            f"checkpoint regression: {raw_checkpoint} "
            f"< {state.projector_checkpoint}"
        )
    raw_phase = snapshot.get("phase", state.phase)
    return TaskState(
        task_id=state.task_id,
        phase=str(raw_phase),
        projector_checkpoint=raw_checkpoint,
        unresolved_challenges=_uuids(snapshot.get("unresolved_challenges", ())),
    )
