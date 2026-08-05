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
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
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
from packages.research.models import ResearchTaskModel
from packages.tools.contracts import ToolGateway
from packages.tools.fulltext_fetcher import FullTextFetcher, fulltext_fetcher_from_env
from packages.tools.http_gateway import tool_gateway_from_env

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2.0


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


async def claim_queued_tasks(
    sessions: async_sessionmaker[AsyncSession],
    limit: int = 1,
) -> tuple[UUID, ...]:
    """Take up to ``limit`` queued tasks, skipping ones another worker holds.

    ``SKIP LOCKED`` is what makes running several workers safe: two of them
    scanning at the same moment take disjoint sets rather than colliding on the
    same row.
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
            await session.commit()
            return claimed
        except BaseException:
            await session.rollback()
            raise


async def run_one(context: WorkerContext, task_id: UUID) -> JobResult | None:
    """Run one task, letting a failure end that task rather than the worker.

    CLAUDE.md 10 requires one task's failure to stay local. The exception is
    logged with the task id so it is diagnosable, and the loop continues.
    """
    try:
        return await run_task(
            context.app_sessions,
            context.projector_sessions,
            task_id,
            gateway=context.gateway,
            tools=context.tools,
            fulltext_fetcher=context.fulltext_fetcher,
            object_store=context.object_store,
        )
    except TaskNotRunnable:
        # Another worker finished it between the claim and the run.
        logger.info("task %s was no longer runnable", task_id)
        return None
    except Exception:
        logger.exception("task %s failed", task_id)
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
    try:
        while True:
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
