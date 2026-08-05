"""Process-only live stream: the running council's real-time trace.

This is the wire for the workbench's "live view" -- model token deltas, tool
calls, stage progress -- as opposed to the Scientific Event Ledger, which is
the durable scientific record. The two must not share a table: the ledger is
idempotent, replayable and audited; a token stream is none of those things,
and writing it into the ledger would make the ledger untrustworthy.

Design rules:

- **Written by the worker, read by the API.** Both run as ``poliscope_app``;
  the table carries no evidence semantics, so one role with full DML is fine
  (migration 0015).
- **Per-task monotonic ``seq``.** A task is executed by exactly one worker at
  a time, so ``SELECT COALESCE(MAX(seq), 0) + 1`` has no concurrent allocation
  problem. The API replays a client by ``after_seq``.
- **Not replay-guaranteed.** Reconnecting clients re-read from the start and
  deduplicate by ``seq``; nothing here is ever admitted to the Evidence Graph
  (CLAUDE.md 5.1).
- **Never breaks the run.** The writer's own session is separate from the
  deliberation transaction, so a flush failure (or an unparseable payload)
  degrades to a warning, never to a failed seat or a rolled-back round.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.evidence.models import ProcessStreamModel

logger = logging.getLogger(__name__)

# How many buffered rows trigger a flush while a stream is hot. Token deltas
# are appended one by one; writing every one to Postgres would drown the
# database, so the writer batches and the API's 1s poll adds the rest of the
# latency budget.
FLUSH_AT = 40


@dataclass(frozen=True, slots=True)
class ProcessEventRow:
    seq: int
    kind: str
    payload: dict[str, object]


class ProcessStreamRepository:
    """Read/write access to ``process_stream``. API reads, worker writes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_seq(self, task_id: UUID) -> int:
        current = await self._session.scalar(
            select(ProcessStreamModel.seq)
            .where(ProcessStreamModel.task_id == task_id)
            .order_by(ProcessStreamModel.seq.desc())
            .limit(1)
        )
        return 0 if current is None else current + 1

    def append(
        self, task_id: UUID, seq: int, kind: str, payload: dict[str, object]
    ) -> None:
        self._session.add(
            ProcessStreamModel(
                id=uuid4(),
                task_id=task_id,
                seq=seq,
                kind=kind,
                payload=payload,
            )
        )

    async def list_since(
        self, task_id: UUID, after_seq: int, limit: int = 500
    ) -> list[ProcessEventRow]:
        rows = (
            await self._session.scalars(
                select(ProcessStreamModel)
                .where(
                    ProcessStreamModel.task_id == task_id,
                    ProcessStreamModel.seq > after_seq,
                )
                .order_by(ProcessStreamModel.seq)
                .limit(limit)
            )
        ).all()
        return [
            ProcessEventRow(seq=row.seq, kind=row.kind, payload=dict(row.payload))
            for row in rows
        ]


# Synchronous emit callback: the model stream parser calls it from inside an
# httpx read loop, where awaiting a DB write would stall token delivery.
ProcessCallback = Callable[[str, dict[str, object]], None]


class ProcessStreamWriter:
    """Buffered, self-contained writer for one task.

    Owns its own sessions (from the worker's app factory) so its commits never
    tangle with the deliberation transaction. ``emit`` is synchronous and
    never raises; ``flush``/``close`` are async and swallow failures into a
    warning, because the live trace is auxiliary -- the run must not depend
    on it (CLAUDE.md 10).
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        task_id: UUID,
        *,
        flush_at: int = FLUSH_AT,
    ) -> None:
        self._sessions = sessions
        self._task_id = task_id
        self._flush_at = flush_at
        self._buffer: list[tuple[str, dict[str, object]]] = []

    def emit(self, kind: str, payload: dict[str, object]) -> None:
        """Queue one event. Never raises; a broken trace must not break a run."""
        self._buffer.append((kind, payload))

    async def flush(self) -> None:
        """Write buffered rows in one commit; failures degrade to warnings."""
        if not self._buffer:
            return
        pending, self._buffer = self._buffer, []
        try:
            async with self._sessions() as session:
                repo = ProcessStreamRepository(session)
                seq = await repo.next_seq(self._task_id)
                for kind, payload in pending:
                    repo.append(self._task_id, seq, kind, payload)
                    seq += 1
                await session.commit()
        except Exception:
            # The trace is auxiliary. Log and drop the batch rather than
            # letting a live-view write failure fail the research round.
            logger.warning(
                "process stream flush failed for task %s (%d rows dropped)",
                self._task_id,
                len(pending),
                exc_info=True,
            )

    async def close(self) -> None:
        await self.flush()
