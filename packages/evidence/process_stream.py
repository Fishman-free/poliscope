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

import asyncio
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

# Events that close a seat's thinking slice on the live view. A flush that
# drops one of these leaves the seat on "thinking…" forever (round-15), so a
# failed batch containing them is retried instead of dropped; token/heartbeat
# batches are pure display and stay droppable. Bound on retries so a
# persistently broken database cannot grow the buffer without limit.
CLOSING_KINDS = frozenset({"model_done", "seat_absent"})
MAX_CLOSING_RETRIES = 2

# Sparse, load-bearing structural rows, as opposed to the high-volume token
# deltas/heartbeats. The live view opens a seat's card only after seeing
# ``seat_deliberation`` and closes it on ``model_done``/``seat_absent``; the
# tool cards pair ``tool_call`` with ``tool_result``. When the replay window
# is the newest N rows, the heavy token tail can push these anchors out -- the
# reconnecting client then never opens the early-finishing seats' cards
# (production bug: after a background-tab thaw only the still-streaming seats
# render). Structural rows are tiny (a handful per seat per phase plus tool
# calls), so replay keeps them even when they sit before the heavy tail.
STRUCTURAL_KINDS = frozenset(
    {"seat_deliberation", "model_done", "seat_absent", "tool_call", "tool_result"}
)


@dataclass(frozen=True, slots=True)
class ProcessEventRow:
    seq: int
    kind: str
    payload: dict[str, object]


class ProcessStreamRepository:
    """Read/write access to ``process_stream``. API reads, worker writes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_seq(self, task_id: UUID) -> int:
        """The newest stored seq for a task, or -1 when the trace is empty.

        The API's process stream uses this to bound its (re)connect replay to
        the newest rows: a long run stores tens of thousands of token deltas,
        and replaying all of them on every reconnect saturated the event loop
        and froze every client (see apps/api/routers/stream.py).
        """
        current = await self._session.scalar(
            select(ProcessStreamModel.seq)
            .where(ProcessStreamModel.task_id == task_id)
            .order_by(ProcessStreamModel.seq.desc())
            .limit(1)
        )
        return -1 if current is None else current

    async def next_seq(self, task_id: UUID) -> int:
        latest = await self.latest_seq(task_id)
        return 0 if latest < 0 else latest + 1

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

    async def list_structural_before(
        self,
        task_id: UUID,
        before_seq: int,
        limit: int = 3000,
    ) -> list[ProcessEventRow]:
        """Structural rows at or before ``before_seq`` (ascending seq).

        The process replay tail bounds heavy token rows; this method returns
        the sparse anchors (deliberation start/done/absent, tool call/result)
        that the tail window may have scrolled past, so a reconnecting client
        can still open every seat card and tool card. Returns the newest
        ``limit`` such rows, ordered ascending to match ``list_since``.
        """
        if before_seq < 0 or limit <= 0:
            return []
        rows = (
            await self._session.scalars(
                select(ProcessStreamModel)
                .where(
                    ProcessStreamModel.task_id == task_id,
                    ProcessStreamModel.seq <= before_seq,
                    ProcessStreamModel.kind.in_(STRUCTURAL_KINDS),
                )
                .order_by(ProcessStreamModel.seq.desc())
                .limit(limit)
            )
        ).all()
        return [
            ProcessEventRow(seq=row.seq, kind=row.kind, payload=dict(row.payload))
            for row in reversed(rows)
        ]

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
        # Round-13 fix: ``next_seq`` reads ``MAX(seq)`` then inserts, so two
        # concurrent flushes (token-delta flush racing a heartbeat flush) can
        # allocate the same seq and silently drop a row in the live view.
        # Serialising flushes makes seq allocation single-writer.
        self._flush_lock = asyncio.Lock()
        # Round-15: how many closing-event batches have been put back for a
        # retry. Bounds the retry so a persistently broken database cannot
        # grow the buffer without limit.
        self._closing_retries = 0

    def emit(self, kind: str, payload: dict[str, object]) -> None:
        """Queue one event. Never raises; a broken trace must not break a run."""
        self._buffer.append((kind, payload))

    async def flush(self) -> None:
        """Write buffered rows in one commit; failures degrade to warnings."""
        if not self._buffer:
            return
        pending, self._buffer = self._buffer, []
        try:
            async with self._flush_lock:
                await self._flush_pending(pending)
        except Exception:
            # The trace is auxiliary. Log and drop the batch rather than
            # letting a live-view write failure fail the research round.
            logger.warning(
                "process stream flush failed for task %s (%d rows dropped)",
                self._task_id,
                len(pending),
                exc_info=True,
            )
            # Round-15: dropping a batch that contains a closing event
            # (``model_done`` / ``seat_absent``) would leave the live view on
            # "thinking…" forever even though the model finished. Put such
            # batches back so ``close()`` (or the next flush) retries them --
            # bounded, so a persistent DB failure still ends as a warning,
            # never a failed run, and the buffer never grows without limit.
            if (
                self._closing_retries < MAX_CLOSING_RETRIES
                and any(kind in CLOSING_KINDS for kind, _ in pending)
            ):
                self._closing_retries += 1
                self._buffer = pending + self._buffer

    async def _flush_pending(
        self, pending: list[tuple[str, dict[str, object]]]
    ) -> None:
        async with self._sessions() as session:
            repo = ProcessStreamRepository(session)
            seq = await repo.next_seq(self._task_id)
            for kind, payload in pending:
                repo.append(self._task_id, seq, kind, payload)
                seq += 1
            await session.commit()

    async def close(self) -> None:
        await self.flush()
