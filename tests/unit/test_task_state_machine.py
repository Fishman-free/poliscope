from __future__ import annotations

import pytest

from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.epistemo.state_machine import (
    InvalidTransition,
    TaskStateMachine,
)


def test_degraded_running_is_not_terminal() -> None:
    sm = TaskStateMachine()
    assert sm.is_terminal(TaskStatus.DEGRADED_RUNNING) is False
    assert sm.is_terminal(TaskStatus.COMPLETED_WITH_GAPS) is True
    assert sm.is_terminal(TaskStatus.COMPLETED) is True
    assert sm.is_terminal(TaskStatus.FAILED) is True


def test_awaiting_council_input_is_not_terminal() -> None:
    sm = TaskStateMachine()
    sm.transition_to(TaskStatus.AWAITING_COUNCIL_INPUT)
    assert sm.status == TaskStatus.AWAITING_COUNCIL_INPUT
    assert sm.is_terminal(TaskStatus.AWAITING_COUNCIL_INPUT) is False


def test_initial_status_is_draft() -> None:
    sm = TaskStateMachine()
    assert sm.status == TaskStatus.DRAFT


def test_valid_phase_transition() -> None:
    sm = TaskStateMachine()
    sm.transition_to(TaskPhase.PRECOMMITMENT)
    assert sm.phase == TaskPhase.PRECOMMITMENT


def test_invalid_transition_raises() -> None:
    sm = TaskStateMachine()
    with pytest.raises(InvalidTransition):
        sm.transition_to(TaskPhase.CROSS_EXAMINATION)  # can't skip


def test_full_phase_sequence() -> None:
    sm = TaskStateMachine()
    phases = [
        TaskPhase.PRECOMMITMENT,
        TaskPhase.ACQUISITION,
        TaskPhase.EVIDENCE_EXCHANGE,
        TaskPhase.CROSS_EXAMINATION,
        TaskPhase.BLINDSPOT_BOUNTY,
        TaskPhase.JOINT_MODELING,
        TaskPhase.FINAL_REJUDGMENT,
        TaskPhase.REPORTING,
    ]
    for phase in phases:
        sm.transition_to(phase)
    assert sm.phase == TaskPhase.REPORTING
    sm.transition_to(TaskStatus.COMPLETED)
    assert sm.is_terminal(sm.status)
