from __future__ import annotations

from packages.epistemo.contracts import (
    PHASE_SEQUENCE,
    TERMINAL_STATUSES,
    TaskPhase,
    TaskStatus,
)


class InvalidTransition(Exception):
    """Raised when an illegal phase/status transition is attempted."""


class TaskStateMachine:
    def __init__(
        self,
        status: TaskStatus = TaskStatus.DRAFT,
        phase: TaskPhase | None = None,
    ) -> None:
        self._status = status
        self._phase = phase
        self._history: list[tuple[TaskStatus | None, TaskPhase | None]] = [
            (status, phase)
        ]

    @property
    def status(self) -> TaskStatus:
        return self._status

    @property
    def phase(self) -> TaskPhase | None:
        return self._phase

    @property
    def history(self) -> list[tuple[TaskStatus | None, TaskPhase | None]]:
        return list(self._history)

    def is_terminal(self, status: TaskStatus) -> bool:
        return status in TERMINAL_STATUSES

    def transition_to(
        self, target: TaskPhase | TaskStatus
    ) -> None:
        if isinstance(target, TaskStatus):
            self._transition_to_status(target)
        else:
            self._transition_to_phase(target)

    def _transition_to_phase(self, target: TaskPhase) -> None:
        if self._phase is not None and self._phase == target:
            return  # idempotent
        current_index = (
            PHASE_SEQUENCE.index(self._phase)
            if self._phase is not None
            else -1
        )
        target_index = PHASE_SEQUENCE.index(target)
        if target_index != current_index + 1:
            raise InvalidTransition(
                f"cannot jump from {self._phase} to {target}"
            )
        self._phase = target
        self._status = TaskStatus.QUEUED
        self._history.append((self._status, self._phase))

    def _transition_to_status(self, target: TaskStatus) -> None:
        if target in TERMINAL_STATUSES:
            self._status = target
            self._history.append((self._status, self._phase))
            return
        if target == TaskStatus.DEGRADED_RUNNING:
            self._status = target
            self._history.append((self._status, self._phase))
            return
        raise InvalidTransition(
            f"direct transition to {target} not allowed"
        )
