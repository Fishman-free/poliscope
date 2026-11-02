"""The worker process: claim queued tasks and run them.

**Why the queue is PostgreSQL and not arq.** ``arq`` and Redis are declared in
``pyproject.toml`` and Redis is still the right home for caches and SSE fan-out,
but the work queue itself is one ``SELECT ... FOR UPDATE SKIP LOCKED`` over a
table that must exist anyway. Putting it there keeps the claim and the status
update in the same transaction, so a worker that dies mid-task releases its claim
automatically instead of leaving a task marked running in Postgres and acked in
Redis. CLAUDE.md 8 makes the database the source of truth; a second store that
can disagree with it about which tasks are running is the disagreement worth
avoiding. This is recorded here rather than assumed, per CLAUDE.md 17.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from apps.worker.jobs import JobResult, TaskNotRunnable, run_task
from packages.epistemo.contracts import TaskStatus
from packages.kernel.config import DatabaseConfig
from packages.kernel.database import (
    canonical_uuid,
    create_database_engine,
    create_session_factory,
    is_connection_failure,
)
from packages.models.contracts import ModelGateway
from packages.models.openai_compatible import gateway_from_env
from packages.papers.object_store import PrivateObjectStore
from packages.reports.safety import sanitize_export
from packages.reports.synthesis import synthesize_paper
from packages.research.models import ResearchTaskModel
from packages.research.repository import ResearchRepository
from packages.tools.contracts import ToolGateway
from packages.tools.fulltext_fetcher import FullTextFetcher, fulltext_fetcher_from_env
from packages.tools.http_gateway import tool_gateway_from_env

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0
MAX_LOOP_BACKOFF_SECONDS = 60.0
COUNCIL_INPUT_GRACE_ENV = "POLISCOPE_COUNCIL_INPUT_GRACE_SECONDS"
# 15 分钟（900 秒）：留给研究者充足时间查看前 5 轮立场并提交方向性备注，
# 但仍有界——超时后以「无方向性干预」自动继续，研究不会因走开而卡死。
DEFAULT_COUNCIL_INPUT_GRACE_SECONDS = 900.0
MIN_COUNCIL_INPUT_GRACE_SECONDS = 30.0


@dataclass(slots=True)
class WorkerContext:
    """The two identities a worker needs, created once per process.

    ``gateway`` is ``None`` whenever no vendor is configured -- CLAUDE.md 10's
    honest gap, not an error. :func:`gateway_from_env` returns ``None`` only
    when ``POLISCOPE_MODEL_API_KEY`` is simply unset; if it is set but the
    rest of the configuration is broken, it raises rather than guessing.

    ``tools`` has no such "unset" state: OpenAlex, Crossref, and Semantic
    Scholar need no credential, so :func:`tool_gateway_from_env` always
    returns a working gateway. Only the Unpaywall operation can still fail,
    and only lazily, if ``POLISCOPE_TOOLS_CONTACT_EMAIL`` is missing when it
    is actually called.

    ``fulltext_fetcher`` has the same "always present" shape as ``tools``:
    downloading an already-resolved open-access URL needs no vendor
    credential of its own, so :func:`fulltext_fetcher_from_env` always
    returns a working fetcher.

    ``object_store`` is likewise always present -- it is a local-file-backed
    stand-in with no vendor credential to be missing (see
    ``packages.papers.object_store``) -- and is created once per process
    rather than once per task so every uploaded-PDF extraction in this worker
    reads from the same root path.
    """

    app_engine: AsyncEngine
    projector_engine: AsyncEngine
    app_sessions: async_sessionmaker[AsyncSession]
    projector_sessions: async_sessionmaker[AsyncSession]
    gateway: ModelGateway | None = None
    tools: ToolGateway | None = None
    fulltext_fetcher: FullTextFetcher | None = None
    object_store: PrivateObjectStore | None = None

    @classmethod
    def from_urls(
        cls,
        app_url: str,
        projector_url: str,
        gateway: ModelGateway | None = None,
        tools: ToolGateway | None = None,
        fulltext_fetcher: FullTextFetcher | None = None,
        object_store: PrivateObjectStore | None = None,
    ) -> WorkerContext:
        app_engine = create_database_engine(app_url)
        projector_engine = create_database_engine(projector_url)
        return cls(
            app_engine=app_engine,
            projector_engine=projector_engine,
            app_sessions=create_session_factory(app_engine),
            projector_sessions=create_session_factory(projector_engine),
            gateway=gateway,
            tools=tools,
            fulltext_fetcher=fulltext_fetcher,
            object_store=object_store,
        )

    @classmethod
    def from_env(cls) -> WorkerContext:
        return cls.from_urls(
            DatabaseConfig.app_url_from_env(),
            DatabaseConfig.projector_url_from_env(),
            gateway_from_env(),
            tool_gateway_from_env(),
            fulltext_fetcher_from_env(),
            PrivateObjectStore.from_env(),
        )

    async def dispose(self) -> None:
        for provider in (self.gateway, self.tools, self.fulltext_fetcher):
            aclose = getattr(provider, "aclose", None)
            if callable(aclose):
                await aclose()
        await self.app_engine.dispose()
        await self.projector_engine.dispose()


def _council_input_grace_seconds(
    env: Mapping[str, str] | None = None,
) -> float:
    """Resolve a bounded grace period without accepting silent bad config."""
    source = os.environ if env is None else env
    raw = source.get(
        COUNCIL_INPUT_GRACE_ENV, str(DEFAULT_COUNCIL_INPUT_GRACE_SECONDS)
    )
    try:
        seconds = float(raw)
    except ValueError as error:
        raise ValueError(
            f"{COUNCIL_INPUT_GRACE_ENV} must be a finite number of seconds"
        ) from error
    if not math.isfinite(seconds) or seconds < MIN_COUNCIL_INPUT_GRACE_SECONDS:
        raise ValueError(
            f"{COUNCIL_INPUT_GRACE_ENV} must be at least "
            f"{MIN_COUNCIL_INPUT_GRACE_SECONDS:g} seconds"
        )
    return seconds


async def claim_queued_tasks(
    sessions: async_sessionmaker[AsyncSession],
    limit: int = 1,
) -> tuple[UUID, ...]:
    """Take up to ``limit`` queued tasks, skipping ones another worker holds.

    ``SKIP LOCKED`` is what makes running several workers safe: two of them
    scanning at the same moment take disjoint sets rather than colliding on the
    same row.

    The claim flips the row to ``RUNNING`` inside the same transaction, before
    the lock is released. That is what makes the ``SKIP LOCKED`` safety real:
    without it the status stays ``QUEUED``, the next poll of *this same
    worker* re-selects the task while the first run is still going, and the
    two parallel runs append the same idempotency keys with different payloads
    until one of them dies on ``EventConflict``. A crashed worker leaves a
    stale ``RUNNING`` row behind; :func:`recover_stale_running` reclaims it.
    """
    async with sessions() as session:
        try:
            result = await session.execute(
                select(ResearchTaskModel.task_id)
                .where(ResearchTaskModel.status == TaskStatus.QUEUED)
                .order_by(ResearchTaskModel.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            # canonical_uuid at the driver boundary: asyncpg returns its own UUID
            # subclass, and every ContractModel this id later flows into (e.g.
            # ModelRequest) admits a leaf only when its type matches exactly. See
            # packages.kernel.database and apps/worker/jobs.py's
            # _confirmed_claim_ids for the same normalisation.
            claimed = tuple(canonical_uuid(value) for value in result.scalars())
            if claimed:
                await session.execute(
                    update(ResearchTaskModel)
                    .where(ResearchTaskModel.task_id.in_(claimed))
                    .values(
                        status=TaskStatus.RUNNING,
                        updated_at=func.now(),
                    )
                )
            await session.commit()
            return claimed
        except BaseException:
            await session.rollback()
            raise


# A worker holds a session-level advisory lock on every task it runs, on a
# connection of its own, for the whole run. PostgreSQL drops the lock the
# moment that connection closes -- including when the process is SIGKILLed,
# which is how every `docker compose up -d --build` ends a running worker
# (Python as PID 1 ignores SIGTERM). A RUNNING row nobody holds the lease for
# is therefore orphaned *now*, not "after twice its budget": the old
# time-only rule left a deploy-killed task silent for 135 minutes before it
# was resumed (a production task waited 138 minutes, then failed).
#
# The two-key form keeps these locks out of the single-bigint key space the
# ledger's per-append ``pg_advisory_xact_lock`` uses, so the lease can never
# block an append of the task it protects.
TASK_LEASE_NAMESPACE = 0x504C5343  # "PLSC"
# Covers the instant between the claim commit and the lease acquisition, so a
# just-claimed task is never mistaken for an orphan.
LEASE_CLAIM_GRACE_SECONDS = 120

_LEASE_KEY = "hashtext(cast(:task_id as text))"

# The run_one watchdog fails a task that outlives wall_clock + this buffer.
# The wall-clock budget stops a *healthy* run only between phases, so the
# buffer must cover the longest thing a healthy run may still legally do
# after the budget's last check: the phase already in flight and the final
# rejudgment that always runs (two 900 s seat-collection deadlines), the
# final synthesis call (its own 600 s deadline) and the projection. A shorter
# buffer turned a slow but healthy run into FAILED (the old 300 s was less
# than a single phase). What the watchdog is for is a *stuck* run that never
# reaches a phase boundary.
WATCHDOG_BUFFER_SECONDS = 2700.0
DEFAULT_WATCHDOG_SECONDS = 3600.0


@asynccontextmanager
async def task_lease(engine: AsyncEngine, task_id: UUID) -> AsyncIterator[bool]:
    """Hold the task's liveness lease for the duration of the block.

    Yields whether the lease was taken. Not taking it (a hash collision with
    another running task, or a database blip) never stops the run: the claim
    already made the task ours, and the lease only tells reclaimers that a
    live worker is on it. Every failure here degrades to "no lease".
    """
    try:
        connection = await engine.connect()
    except Exception:
        logger.warning("could not open a lease connection for %s", task_id)
        yield False
        return
    try:
        try:
            taken = bool(
                await connection.scalar(
                    text(f"SELECT pg_try_advisory_lock(:ns, {_LEASE_KEY})"),
                    {"ns": TASK_LEASE_NAMESPACE, "task_id": str(task_id)},
                )
            )
            # Session-level locks outlive transactions; end the implicit one
            # so the lease connection is not "idle in transaction".
            await connection.commit()
        except Exception:
            logger.warning("could not take the lease for %s", task_id)
            taken = False
        yield taken
    finally:
        with suppress(Exception):
            await connection.execute(text("SELECT pg_advisory_unlock_all()"))
            await connection.commit()
        with suppress(Exception):
            await connection.close()


async def recover_stale_running(
    sessions: async_sessionmaker[AsyncSession],
) -> int:
    """Reset orphaned ``RUNNING`` claims back to ``QUEUED``.

    A claim is orphaned when no live worker holds its lease (see
    :func:`task_lease`) and it is older than the claim grace. B7 crash-safe
    council: the dead worker rolled back only the interrupted phase's open
    transaction; every finished phase was committed by the per-phase
    checkpoint hook, so the reclaimed run resumes from its stored checkpoint
    instead of re-running PRECOMMITMENT. Both ``RUNNING`` and
    ``DEGRADED_RUNNING`` claims are reclaimed: a degraded run is still a live
    run. A run whose terminal status committed is in neither state, so this
    can never resurrect a finished task. ``SKIP LOCKED`` passes over a row a
    live deliberation transaction is writing.

    Returns how many claims were reclaimed.
    """
    async with sessions() as session:
        try:
            rows = await session.execute(
                select(ResearchTaskModel.task_id)
                .where(
                    ResearchTaskModel.status.in_(
                        (TaskStatus.RUNNING, TaskStatus.DEGRADED_RUNNING)
                    ),
                    ResearchTaskModel.updated_at
                    < func.now()
                    - text(":grace * interval '1 second'").bindparams(
                        grace=LEASE_CLAIM_GRACE_SECONDS
                    ),
                )
                .with_for_update(skip_locked=True)
            )
            orphaned: list[UUID] = []
            for task_id in rows.scalars():
                # Taking the lease proves nobody holds it; it is released at
                # once (the lock is session-level, the transaction is not).
                free = await session.scalar(
                    text(f"SELECT pg_try_advisory_lock(:ns, {_LEASE_KEY})"),
                    {"ns": TASK_LEASE_NAMESPACE, "task_id": str(task_id)},
                )
                if free:
                    await session.execute(
                        text(f"SELECT pg_advisory_unlock(:ns, {_LEASE_KEY})"),
                        {"ns": TASK_LEASE_NAMESPACE, "task_id": str(task_id)},
                    )
                    orphaned.append(task_id)
            if orphaned:
                await session.execute(
                    update(ResearchTaskModel)
                    .where(ResearchTaskModel.task_id.in_(orphaned))
                    .values(status=TaskStatus.QUEUED, updated_at=func.now())
                )
            await session.commit()
            return len(orphaned)
        except BaseException:
            with suppress(Exception):
                await session.rollback()
            raise


async def requeue_task(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> bool:
    """Put a still-RUNNING task back in the queue to resume from its checkpoint.

    Used when the run lost its database connection for longer than the
    in-process recovery window: that says nothing about the research, so the
    task waits for the database instead of ending FAILED. If even this write
    fails (the database is still down), the row stays RUNNING without a
    lease and :func:`recover_stale_running` requeues it once it is back.
    """
    async with sessions() as session:
        result = await session.execute(
            update(ResearchTaskModel)
            .where(
                ResearchTaskModel.task_id == task_id,
                ResearchTaskModel.status.in_(
                    (TaskStatus.RUNNING, TaskStatus.DEGRADED_RUNNING)
                ),
            )
            .values(status=TaskStatus.QUEUED, updated_at=func.now())
        )
        await session.commit()
        return bool(cast(CursorResult[Any], result).rowcount)


async def resume_expired_council_inputs(
    sessions: async_sessionmaker[AsyncSession],
    *,
    grace_seconds: float,
) -> int:
    """Queue expired human checkpoints without fabricating human guidance.

    The checkpoint JSON is left byte-for-byte intact, including
    ``guidance=None``. The conditional UPDATE is the concurrency guard: if a
    researcher submits guidance first, that transaction changes the status to
    QUEUED and this path no longer matches the row.
    """
    if (
        not math.isfinite(grace_seconds)
        or grace_seconds < MIN_COUNCIL_INPUT_GRACE_SECONDS
    ):
        raise ValueError(
            "council input grace must be finite and at least "
            f"{MIN_COUNCIL_INPUT_GRACE_SECONDS:g} seconds"
        )
    async with sessions() as session:
        result = await session.execute(
            update(ResearchTaskModel)
            .where(
                ResearchTaskModel.status == TaskStatus.AWAITING_COUNCIL_INPUT,
                ResearchTaskModel.council_checkpoint.is_not(None),
                ResearchTaskModel.updated_at
                < func.now()
                - text(":grace_seconds * interval '1 second'").bindparams(
                    grace_seconds=grace_seconds
                ),
            )
            .values(status=TaskStatus.QUEUED, updated_at=func.now())
        )
        await session.commit()
        cursor = cast(CursorResult[Any], result)
        return int(cursor.rowcount or 0)


async def _watchdog_timeout(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> float:
    """Wall-clock budget of the task plus the buffer, for run_one's deadline."""
    async with sessions() as session:
        row = await session.scalar(
            select(ResearchTaskModel.wall_clock_minutes).where(
                ResearchTaskModel.task_id == task_id
            )
        )
    if row is None:
        # Unknown row (deleted between claim and run): fall back to an hour,
        # after which the stale reclaimer owns it.
        return DEFAULT_WATCHDOG_SECONDS
    return float(row * 60 + WATCHDOG_BUFFER_SECONDS)


async def emergency_finalize_task(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    *,
    reason: str,
) -> bool:
    """Terminalize a still-RUNNING task, then emit a deterministic artifact.

    Status and fallback use separate fresh sessions. Consequently a fallback
    failure cannot roll back the terminal status, and this function runs only
    after ``asyncio.wait_for`` has finished cancelling the original job.
    """
    safe_reason = sanitize_export(reason)[:500] or "unexpected worker failure"
    async with sessions() as status_session:
        result = await status_session.execute(
            update(ResearchTaskModel)
            .where(
                ResearchTaskModel.task_id == task_id,
                ResearchTaskModel.status == TaskStatus.RUNNING,
            )
            .values(status=TaskStatus.FAILED, updated_at=func.now())
        )
        cursor = cast(CursorResult[Any], result)
        terminalized = bool(cursor.rowcount)
        if terminalized:
            # The run is over; a stop request left for it must not cancel a
            # later re-research of the same task.
            await ResearchRepository(status_session).clear_cancel_request(task_id)
        await status_session.commit()
    if not terminalized:
        return False

    try:
        async with sessions() as fallback_session:
            try:
                await synthesize_paper(
                    fallback_session,
                    task_id,
                    None,
                    emergency_reason=safe_reason,
                )
                await fallback_session.commit()
            except BaseException:
                await fallback_session.rollback()
                raise
    except BaseException:  # noqa: BLE001 -- status is already durable
        logger.exception(
            "emergency fallback failed for terminal task %s", task_id
        )
    return True


async def _mark_timed_out(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> None:
    """Compatibility wrapper for the watchdog's emergency finalization."""
    await emergency_finalize_task(
        sessions,
        task_id,
        reason="worker watchdog timeout before research completion",
    )


async def run_one(context: WorkerContext, task_id: UUID) -> JobResult | None:
    """Run one task, letting a failure end that task rather than the worker.

    CLAUDE.md 10 requires one task's failure to stay local. The exception is
    logged with the task id so it is diagnosable, and the loop continues.

    A task that outlives its wall-clock budget plus a buffer is treated as
    stuck and failed outright: the phase-level wall-clock check only fires
    between phases, so a hang inside a phase would otherwise block this single
    worker loop forever (the stale reclaimer cannot run while ``drain`` is
    awaiting a hung task).
    """
    timeout_seconds = await _watchdog_timeout(context.app_sessions, task_id)
    try:
        async with task_lease(context.app_engine, task_id):
            return await asyncio.wait_for(
                run_task(
                    context.app_sessions,
                    context.projector_sessions,
                    task_id,
                    gateway=context.gateway,
                    tools=context.tools,
                    fulltext_fetcher=context.fulltext_fetcher,
                    object_store=context.object_store,
                ),
                timeout=timeout_seconds,
            )
    except TimeoutError:
        logger.error(
            "task %s exceeded its watchdog deadline (%.0fs); failing it",
            task_id,
            timeout_seconds,
        )
        await _mark_timed_out(context.app_sessions, task_id)
        return None
    except TaskNotRunnable:
        # Another worker finished it between the claim and the run.
        logger.info("task %s was no longer runnable", task_id)
        return None
    except Exception as error:
        if is_connection_failure(error):
            # run_task already retried in-process; the database is still
            # unreachable. Nothing about the research failed, so the task
            # waits in the queue and resumes from its checkpoint.
            logger.warning(
                "task %s lost the database beyond in-process recovery; "
                "requeueing it to resume from its checkpoint",
                task_id,
                exc_info=True,
            )
            with suppress(Exception):
                await requeue_task(context.app_sessions, task_id)
            return None
        logger.exception("task %s failed", task_id)
        await emergency_finalize_task(
            context.app_sessions,
            task_id,
            reason=(
                f"unexpected worker exception: {type(error).__name__}: {error}"
            ),
        )
        return None


async def drain(context: WorkerContext, limit: int = 1) -> Sequence[JobResult]:
    """Claim and run whatever is queued right now, then return.

    Separate from :func:`run_worker` so a test, the CLI, or a one-shot container
    can advance the queue without starting an endless loop.
    """
    results: list[JobResult] = []
    for task_id in await claim_queued_tasks(context.app_sessions, limit):
        result = await run_one(context, task_id)
        if result is not None:
            results.append(result)
    return results


async def run_worker(
    context: WorkerContext | None = None,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> None:
    """Poll for queued tasks until cancelled."""
    owned = context is None
    active = WorkerContext.from_env() if context is None else context
    council_input_grace = _council_input_grace_seconds()
    failures = 0
    try:
        while True:
            try:
                # A killed worker leaves RUNNING claims behind; reclaim them
                # before claiming anything new. On a fresh start (a deploy)
                # this is what resumes the task the old process was running.
                reclaimed = await recover_stale_running(active.app_sessions)
                if reclaimed:
                    logger.info("reclaimed %d orphaned RUNNING task(s)", reclaimed)
                resumed = await resume_expired_council_inputs(
                    active.app_sessions,
                    grace_seconds=council_input_grace,
                )
                if resumed:
                    logger.info(
                        "automatically resumed %d expired council checkpoint(s)",
                        resumed,
                    )
                drained = await drain(active)
                failures = 0
            except Exception:
                # One bad iteration (a database restart, a lost connection in
                # a claim or a reclaim) must not end the process: a dead
                # worker strands every queued task until the next deploy. A
                # task caught mid-claim stays RUNNING without a lease and is
                # reclaimed on a later iteration.
                failures += 1
                delay = min(poll_interval * 2**failures, MAX_LOOP_BACKOFF_SECONDS)
                logger.exception(
                    "worker loop iteration failed (%d in a row); retrying in %.0fs",
                    failures,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            if not drained:
                await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        logger.info("worker stopping")
        raise
    finally:
        if owned:
            await active.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
