"""The database boundary must hand the rest of the system canonical UUIDs.

``PGUUID`` columns come back as ``asyncpg.pgproto.pgproto.UUID`` -- a *subclass*
of ``uuid.UUID``, not ``uuid.UUID`` itself. ``ContractModel`` admits a leaf only
when its type matches exactly, on purpose (a scalar subclass can carry mutable
state), so an un-normalised value reaching a frozen contract raises a
ValidationError. That is not hypothetical: it answered 500 on every
「重新研究」 and 时间旅行复跑 for a task that carried a knowledge base, because
``rerun_fresh`` rebuilds the contract from the row it just read.
"""

from uuid import UUID, uuid4

from asyncpg.pgproto.pgproto import UUID as AsyncpgUUID

from packages.research.contracts import ResearchContract
from packages.research.repository import StoredTask
from tests.factories import make_research_contract


def _from_driver(value: UUID) -> AsyncpgUUID:
    """The exact type asyncpg returns for a PGUUID column."""
    return AsyncpgUUID(str(value))


def _stored(**overrides: object) -> StoredTask:
    values: dict[str, object] = {
        "task_id": uuid4(),
        "question": "数字行为是否影响心理健康？",
        "status": "FAILED",
        "created_by": "api",
    }
    values.update(overrides)
    return StoredTask(**values)  # type: ignore[arg-type]


def test_stored_task_normalises_every_identity_field_from_the_driver() -> None:
    stored = _stored(
        task_id=_from_driver(uuid4()),
        knowledge_base_id=_from_driver(uuid4()),
        user_id=_from_driver(uuid4()),
        skill_ids=(_from_driver(uuid4()), _from_driver(uuid4())),
        replay_of_task_id=_from_driver(uuid4()),
    )

    for value in (
        stored.task_id,
        stored.knowledge_base_id,
        stored.user_id,
        *stored.skill_ids,
        stored.replay_of_task_id,
    ):
        assert type(value) is UUID


def test_absent_identity_fields_stay_none() -> None:
    """A task with no knowledge base or owner must not be normalised into one."""
    stored = _stored(knowledge_base_id=None, user_id=None, replay_of_task_id=None)

    assert stored.knowledge_base_id is None
    assert stored.user_id is None
    assert stored.replay_of_task_id is None
    assert stored.skill_ids == ()


def test_a_contract_rebuilt_from_a_stored_task_validates() -> None:
    """The production 500, at the smallest size that still reproduces it.

    ``rerun_fresh`` rebuilds a ``ResearchContract`` out of the task row it just
    read; before the boundary normalisation this raised
    ``unsupported mutable or unknown leaf type: UUID``.
    """
    source = make_research_contract()
    stored = _stored(
        task_id=_from_driver(uuid4()),
        knowledge_base_id=_from_driver(uuid4()),
        user_id=_from_driver(uuid4()),
        skill_ids=(_from_driver(uuid4()),),
    )

    contract = ResearchContract(
        question=stored.question,
        scope=source.scope,
        budget=source.budget,
        user_evidence=source.user_evidence,
        knowledge_base_id=stored.knowledge_base_id,
        skill_ids=stored.skill_ids,
        replay_of_task_id=stored.task_id,
    )

    assert contract.knowledge_base_id == stored.knowledge_base_id
    assert contract.skill_ids == stored.skill_ids
    assert contract.replay_of_task_id == stored.task_id
