"""「交叉质询阶段研究中断」回归测试（生产故障，辩题「女士优先」）。

生产日志中的完整链条：

1. 交叉质询走强推理模型、输出长，单席 120s 超时频繁触发；
2. ``asyncio.wait_for`` 取消席位协程时，它正在共享的 deliberation session
   上执行数据库操作（审计行 commit、每秒一次的停止请求探测）；
3. asyncpg 语句被取消 → SQLAlchemy 作废该连接；此后这个 session 上每条
   语句都抛 ``PendingRollbackError: Can't reconnect until invalid
   transaction is rolled back``；
4. 后续六席全部“缺席”，阶段事件写不进账本，整个任务 FAILED，用户只拿到
   一份「未经模型整合」的应急结论。

修复分两层，本文件逐条锁住：

* **根因**：数据库写入免疫取消（``run_to_completion``）并按 session 串行
  （``guarded_write``）；停止探测改用独立 session；
* **韧性**：连接级故障不再被误记为席位缺席或阶段失败，而是作为
  ``DatabaseSessionLost`` 一路上抛，由 worker 在新 session 中从最近的
  阶段检查点续跑（``run_task``）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker import jobs
from packages.council.contracts import Seat
from packages.council.deliberation import GatewayDeliberator
from packages.council.rounds.registry import PhaseContext, _collect
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.epistemo.orchestrator import TaskRunReport
from packages.kernel.database import (
    DatabaseSessionLost,
    guarded_write,
    is_connection_failure,
    run_to_completion,
)
from packages.models.audit import record_model_call
from packages.models.contracts import (
    ModelClass,
    ModelMessage,
    ModelRequest,
    ModelResult,
    SchemaStatus,
)

_INVALIDATED = (
    "Can't reconnect until invalid transaction is rolled back.  Please "
    "rollback() fully before proceeding"
)


# --- 根因：写入免疫取消、按 session 串行 -------------------------------


async def test_a_cancelled_caller_still_lets_the_write_finish() -> None:
    """取消落在写入中途时，写入先完成，取消随后照常传播。"""
    finished = asyncio.Event()

    async def write() -> int:
        await asyncio.sleep(0.05)
        finished.set()
        return 1

    task = asyncio.create_task(run_to_completion(write()))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_a_seat_timeout_never_lands_mid_statement() -> None:
    """席位超时（wait_for）发生在 commit 中途：commit 完成，超时照常报告。"""
    committed = asyncio.Event()
    session = _LockOnlySession()

    async def seat_call() -> None:
        async def commit() -> None:
            await asyncio.sleep(0.05)
            committed.set()

        await guarded_write(cast(AsyncSession, session), commit)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(seat_call(), timeout=0.01)
    assert committed.is_set()


async def test_a_second_cancellation_does_not_abandon_the_write() -> None:
    finished = asyncio.Event()

    async def write() -> None:
        await asyncio.sleep(0.05)
        finished.set()

    task = asyncio.create_task(run_to_completion(write()))
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_concurrent_writers_on_one_session_are_serialised() -> None:
    """取证阶段三路并发检索各自提交审计行：同一 session 上绝不重叠。"""
    session = _LockOnlySession()
    active = 0
    peak = 0

    async def write() -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    await asyncio.gather(
        *(guarded_write(cast(AsyncSession, session), write) for _ in range(5))
    )
    assert peak == 1


# --- 连接级故障的识别 ---------------------------------------------------


def test_an_invalidated_connection_is_a_connection_failure() -> None:
    assert is_connection_failure(PendingRollbackError(_INVALIDATED, None, None))
    assert is_connection_failure(DatabaseSessionLost("gone"))
    invalidated = DBAPIError("SELECT 1", None, Exception("reset"))
    invalidated.connection_invalidated = True
    assert is_connection_failure(invalidated)


def test_a_failure_caused_by_a_lost_connection_is_one_too() -> None:
    try:
        try:
            raise PendingRollbackError(_INVALIDATED, None, None)
        except PendingRollbackError as error:
            raise RuntimeError("phase failed") from error
    except RuntimeError as wrapped:
        assert is_connection_failure(wrapped)


def test_a_data_error_is_not_a_connection_failure() -> None:
    """唯一约束冲突换一条连接也照样失败：不能被当作可重试的基础设施故障。"""
    echoed = PendingRollbackError(
        "This Session's transaction has been rolled back due to a previous "
        "exception during flush. Original exception was: duplicate key value "
        "violates unique constraint \"uq_event_idempotency\"",
        None,
        None,
    )
    assert not is_connection_failure(echoed)
    assert not is_connection_failure(ValueError("bad payload"))


# --- 审计写入：连接丢失时立刻停下本轮 -----------------------------------


async def test_an_audit_commit_on_a_dead_session_raises_session_lost() -> None:
    session = _DeadSession()
    with pytest.raises(DatabaseSessionLost):
        await record_model_call(
            cast(AsyncSession, session), _request(), _result()
        )


async def test_a_lost_session_is_not_reported_as_an_absent_seat() -> None:
    """连接丢失不是这位科学家的失败：上抛而不是返回 None（缺席）。

    过程流上的思考片段仍要收尾，否则前端会永远显示「思考中…」。
    """
    streamed: list[dict[str, Any]] = []
    deliberator = GatewayDeliberator(
        _SessionLostGateway(),
        on_process=lambda kind, payload: streamed.append({"kind": kind, **payload}),
    )
    with pytest.raises(DatabaseSessionLost):
        await deliberator.deliberate(
            Seat.CAUSAL_SCIENTIST,
            TaskPhase.CROSS_EXAMINATION,
            _context(deliberator, (Seat.CAUSAL_SCIENTIST,)),
        )
    assert streamed[-1]["kind"] == "seat_absent"


async def test_a_lost_session_ends_the_pass_instead_of_seven_absences() -> None:
    """旧行为：第一席连接丢失后其余六席逐一“缺席”。现在整轮立即上抛。"""
    deliberator = _CountingLostDeliberator()
    with pytest.raises(DatabaseSessionLost):
        await _collect(
            _context(deliberator, tuple(Seat)),
            retry_pause_seconds=0.0,
        )
    assert deliberator.calls == 1


async def test_a_raw_invalidated_connection_also_ends_the_pass() -> None:
    deliberator = _CountingLostDeliberator(
        PendingRollbackError(_INVALIDATED, None, None)
    )
    with pytest.raises(PendingRollbackError):
        await _collect(
            _context(deliberator, tuple(Seat)),
            retry_pause_seconds=0.0,
        )
    assert deliberator.calls == 1


# --- 韧性：worker 从检查点续跑，而不是判任务失败 ------------------------


async def test_run_task_resumes_after_a_lost_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = uuid4()
    calls: list[bool] = []

    async def deliberate_once(*_args: object, resuming: bool) -> TaskRunReport:
        calls.append(resuming)
        if len(calls) == 1:
            raise DatabaseSessionLost("connection invalidated mid-round")
        return TaskRunReport(task_id=task_id, final_status=TaskStatus.COMPLETED)

    _patch_run_task(monkeypatch, deliberate_once)
    result = await jobs.run_task(_sessions(), _sessions(), task_id)

    assert result.run.final_status is TaskStatus.COMPLETED
    # 第二遍是续跑：允许 _claim 接手“状态已提交、结论未写入”的任务。
    assert calls == [False, True]


async def test_run_task_gives_up_after_the_bounded_recoveries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def deliberate_once(*_args: object, resuming: bool) -> TaskRunReport:
        nonlocal attempts
        attempts += 1
        raise DatabaseSessionLost("database is down")

    _patch_run_task(monkeypatch, deliberate_once)
    with pytest.raises(DatabaseSessionLost):
        await jobs.run_task(_sessions(), _sessions(), uuid4())
    assert attempts == len(jobs.DATABASE_RECOVERY_BACKOFF_SECONDS) + 1


async def test_run_task_does_not_retry_a_research_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def deliberate_once(*_args: object, resuming: bool) -> TaskRunReport:
        nonlocal attempts
        attempts += 1
        raise ValueError("a bug, not an outage")

    _patch_run_task(monkeypatch, deliberate_once)
    with pytest.raises(ValueError):
        await jobs.run_task(_sessions(), _sessions(), uuid4())
    assert attempts == 1


async def test_claim_on_resume_takes_a_completed_task_without_its_paper() -> None:
    """状态已随审计行提交、结论却未写入：续跑必须能接手补写结论。"""
    row = _TaskRow(TaskStatus.COMPLETED, checkpoint={"run_phases": []})
    session = _ScriptedSession([row, None])
    claimed = await jobs._claim(
        cast(AsyncSession, session), uuid4(), resuming=True
    )
    # _TaskRow stands in for the ORM row; identity is what is being pinned.
    assert cast(object, claimed) is row


async def test_claim_on_resume_still_refuses_a_finished_task() -> None:
    row = _TaskRow(TaskStatus.COMPLETED, checkpoint={"run_phases": []})
    session = _ScriptedSession([row, uuid4()])
    with pytest.raises(jobs.TaskNotRunnable):
        await jobs._claim(cast(AsyncSession, session), uuid4(), resuming=True)


async def test_a_first_run_never_takes_a_completed_task() -> None:
    row = _TaskRow(TaskStatus.COMPLETED, checkpoint={"run_phases": []})
    session = _ScriptedSession([row, None])
    with pytest.raises(jobs.TaskNotRunnable):
        await jobs._claim(cast(AsyncSession, session), uuid4())


# --- fakes ---------------------------------------------------------------


class _LockOnlySession:
    def __init__(self) -> None:
        self.info: dict[str, object] = {}


class _DeadSession(_LockOnlySession):
    def add(self, _row: object) -> None:
        pass

    async def commit(self) -> None:
        raise PendingRollbackError(_INVALIDATED, None, None)


class _TaskRow:
    def __init__(self, status: TaskStatus, checkpoint: object) -> None:
        self.status = status
        self.council_checkpoint = checkpoint


class _ScriptedSession:
    def __init__(self, scalars: list[object]) -> None:
        self._scalars = list(scalars)

    async def scalar(self, _statement: object) -> object:
        return self._scalars.pop(0)


class _SessionLostGateway:
    async def stream_invoke(self, request: ModelRequest, on_event: object) -> Any:
        raise DatabaseSessionLost("audit commit failed")

    async def invoke(self, request: ModelRequest) -> Any:  # pragma: no cover
        raise AssertionError("no fallback call after the session is lost")


class _CountingLostDeliberator:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or DatabaseSessionLost("gone")
        self.calls = 0
        self.last_error: str | None = None

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        self.calls += 1
        raise self._error


class _FakeSession:
    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


def _sessions() -> async_sessionmaker[AsyncSession]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    return cast(async_sessionmaker[AsyncSession], factory)


def _patch_run_task(monkeypatch: pytest.MonkeyPatch, deliberate_once: Any) -> None:
    async def project(_session: object, _task_id: object) -> None:
        return None

    monkeypatch.setattr(jobs, "_deliberate_once", deliberate_once)
    monkeypatch.setattr(jobs, "project", project)
    monkeypatch.setattr(jobs, "DATABASE_RECOVERY_BACKOFF_SECONDS", (0.0, 0.0, 0.0))


def _context(deliberator: object, seats: tuple[Seat, ...]) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.CROSS_EXAMINATION,
        seats=seats,
        question="“女士优先”有助于还是无助于性别平权？",
        confirmed_claims=(),
        deliberator=deliberator,  # type: ignore[arg-type]
        carried={},
    )


def _request() -> ModelRequest:
    return ModelRequest(
        task_id=uuid4(),
        actor=Seat.CAUSAL_SCIENTIST.value,
        purpose=TaskPhase.CROSS_EXAMINATION.value,
        model_class=ModelClass.STRONG_REASONING,
        messages=(ModelMessage(role="user", content="q"),),
        output_schema="ChallengeSet",
        evidence_refs=(),
    )


def _result() -> ModelResult:
    return ModelResult(
        call_id=uuid4(),
        payload={},
        input_tokens=1,
        output_tokens=1,
        cost_usd=Decimal("0"),
        latency_ms=1,
        retries=0,
        schema_status=SchemaStatus.OK,
    )
