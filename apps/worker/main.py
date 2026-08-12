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
from collections.abc import Mapping, Sequence
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
)
from packages.models.contracts import ModelGateway
from packages.models.openai_compatible import gateway_from_env
from packages.papers.object_store import PrivateObjectStore
from packages.reports.safety import sanitize_export
from packages.reports.synthesis import synthesize_paper
from packages.research.models import ResearchTaskModel
from packages.tools.contracts import ToolGateway
from packages.tools.fulltext_fetcher import FullTextFetcher, fulltext_fetcher_from_env
from packages.tools.http_gateway import tool_gateway_from_env

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0
COUNCIL_INPUT_GRACE_ENV = "POLISCOPE_COUNCIL_INPUT_GRACE_SECONDS"
DEFAULT_COUNCIL_INPUT_GRACE_SECONDS = 120.0
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


# A claimed task may legally run for its wall-clock budget (the budget stops
# the run when exhausted). Anything still RUNNING after twice that budget plus
# this buffer has outlived any legitimate run and is a crashed worker's stale
# claim.
STALE_RUNNING_BUFFER_MINUTES = 15

# The run_one watchdog fails a task that outlives wall_clock + this buffer.
# The wall-clock budget already stops a *healthy* run between phases; this
# catches a *stuck* run (a stalled vendor stream, a deadlocked transaction)
# that never reaches a phase boundary. Without it the loop is blocked inside
# the hung task forever, the stale reclaimer never runs, and every queued
# task waits behind it.
WATCHDOG_BUFFER_SECONDS = 300.0
DEFAULT_WATCHDOG_SECONDS = 3600.0


async def recover_stale_running(
    sessions: async_sessionmaker[AsyncSession],
) -> int:
    """Reset crashed workers' ``RUNNING`` claims back to ``QUEUED``.

    A worker that dies mid-task rolls back its uncommitted deliberation, so
    re-running the task from PRECOMMITMENT is safe: none of its events were
    ever committed. A run whose events *were* committed necessarily also
    committed its terminal status (both live in one transaction), so such a
    task is never ``RUNNING`` here -- which is exactly why resetting stale
    ``RUNNING`` rows can never resurrect a finished task.

    Returns how many claims were reclaimed.
    """
    async with sessions() as session:
        result = await session.execute(
            update(ResearchTaskModel)
            .where(
                ResearchTaskModel.status == TaskStatus.RUNNING,
                ResearchTaskModel.updated_at
                < func.now()
                - text(
                    "((wall_clock_minutes * 2) + :buffer) * interval '1 minute'"
                ).bindparams(buffer=STALE_RUNNING_BUFFER_MINUTES),
            )
            .values(status=TaskStatus.QUEUED)
        )
        await session.commit()
        # execute() returns Result; an UPDATE's row count lives on the
        # underlying CursorResult, which mypy cannot see through the base type.
        cursor = cast(CursorResult[Any], result)
        return int(cursor.rowcount or 0)


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
    try:
        while True:
            # A crashed worker leaves RUNNING claims behind; reclaim them
            # before claiming anything new so they do not wait forever.
            reclaimed = await recover_stale_running(active.app_sessions)
            if reclaimed:
                logger.info("reclaimed %d stale RUNNING task(s)", reclaimed)
            resumed = await resume_expired_council_inputs(
                active.app_sessions,
                grace_seconds=council_input_grace,
            )
            if resumed:
                logger.info(
                    "automatically resumed %d expired council checkpoint(s)",
                    resumed,
                )
            if not await drain(active):
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
