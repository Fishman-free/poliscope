from __future__ import annotations

from uuid import uuid4

import pytest

from packages.memory.branches import BranchService
from packages.evidence.lifecycle import LifecycleService, ResurrectionConditionNotMet
from packages.epistemo.recovery import TaskState, restore_task_state, CheckpointRegressionError


def test_fork_creates_branch() -> None:
    service = BranchService()
    claim_id = uuid4()
    branch = service.fork(claim_id, "if confounding controlled")
    assert branch.claim_id == claim_id
    assert branch.status == "proposed"


def test_merge_requires_two_branches() -> None:
    service = BranchService()
    with pytest.raises(ValueError, match="at least two"):
        service.merge((uuid4(),), condition_variable="")


def test_merge_requires_condition_variable() -> None:
    service = BranchService()
    b1 = service.fork(uuid4(), "c1")
    b2 = service.fork(uuid4(), "c2")
    with pytest.raises(ValueError, match="condition_variable"):
        service.merge((b1.id, b2.id), condition_variable="")


def test_merge_succeeds_with_requirements() -> None:
    service = BranchService()
    b1 = service.fork(uuid4(), "c1")
    b2 = service.fork(uuid4(), "c2")
    merged = service.merge((b1.id, b2.id), condition_variable="moderator=z")
    assert merged.condition_variable == "moderator=z"


def test_quarantine_requires_fields() -> None:
    service = LifecycleService()
    node = service.quarantine(
        uuid4(), reason="unreplicated", attacker="adversarial",
        missing_evidence="replication", resurrection_condition="independent replication"
    )
    assert node.status == "quarantined"


def test_resurrect_requires_new_evidence() -> None:
    service = LifecycleService()
    node_id = uuid4()
    service.quarantine(node_id, "r", "a", "m", "c")
    with pytest.raises(ResurrectionConditionNotMet):
        service.resurrect(node_id, evidence_refs=())
    assert service.node_exists(node_id)


def test_resurrect_succeeds_with_evidence() -> None:
    service = LifecycleService()
    node_id = uuid4()
    service.quarantine(node_id, "r", "a", "m", "c")
    updated = service.resurrect(node_id, evidence_refs=(uuid4(),))
    assert updated.status == "resurrected"


def test_snapshot_resume_keeps_checkpoint() -> None:
    state = TaskState(uuid4(), "evidence_exchange", 5, (uuid4(),))
    restored = restore_task_state(state, {"projector_checkpoint": 7, "phase": "reporting"})
    assert restored.projector_checkpoint == 7
    assert restored.phase == "reporting"


def test_checkpoint_regression_blocked() -> None:
    state = TaskState(uuid4(), "evidence_exchange", 5, ())
    with pytest.raises(CheckpointRegressionError):
        restore_task_state(state, {"projector_checkpoint": 3})
