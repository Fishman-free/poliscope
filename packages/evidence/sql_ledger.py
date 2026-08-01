"""PostgreSQL-backed Scientific Event Ledger.

CLAUDE.md 8 makes the database the source of truth, and CLAUDE.md 10 requires
long tasks to survive a snapshot, pause, resume, and replay. Neither is possible
while the ledger lives in a process's memory, so this is the implementation the
API and the worker use; the in-memory one in :mod:`packages.evidence.ledger`
remains for unit tests and the deterministic evaluator.

Two properties are load bearing and both are enforced by the database rather
than by this code:

* ``uq_event_idempotency`` makes a replayed append a no-op instead of a
  duplicate, which is what lets a resumed task re-run a round safely;
* ``uq_event_sequence`` makes the per-task order total, which is what lets the
  projector process events exactly once in a defined order.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.evidence.ledger import EventConflict, LedgerEntry, payload_hash
from packages.evidence.models import ScientificEventModel


def _to_entry(row: ScientificEventModel) -> LedgerEntry:
    return LedgerEntry(
        event_id=row.id,
        task_id=row.task_id,
        event_type=row.event_type,
        payload=dict(row.payload),
        idempotency_key=row.idempotency_key,
        sequence=row.sequence,
        status=row.status,
    )


class SqlEventLedger:
    """Append-only ledger over ``scientific_events``.

    One instance wraps one :class:`AsyncSession`; it neither commits nor rolls
    back, so the caller decides the transaction boundary and a failed round
    leaves no partial events behind.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _lock_task(self, task_id: UUID) -> None:
        """Serialise appends for one task without blocking other tasks.

        The next sequence number is derived from the current maximum, so two
        concurrent appends would otherwise both read the same maximum and one
        would fail the unique constraint. A transaction scoped advisory lock is
        released automatically on commit or rollback, which means a crashed
        worker cannot leave the task wedged.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": str(task_id)},
        )

    async def _find_by_key(
        self,
        task_id: UUID,
        idempotency_key: str,
    ) -> ScientificEventModel | None:
        result = await self._session.execute(
            select(ScientificEventModel).where(
                ScientificEventModel.task_id == task_id,
                ScientificEventModel.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def append(
        self,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
        status: str = "pending",
        *,
        evidence_level: str | None = None,
        source_id: UUID | None = None,
        finding_id: UUID | None = None,
        claim_id: UUID | None = None,
    ) -> LedgerEntry:
        """Append one event, or return the existing one if it was already added.

        The evidence columns are set on insert rather than by a later update
        because the application role holds INSERT and no UPDATE on this table.
        That is deliberate: an event whose level or source could be edited after
        the fact would make the audit trail in CLAUDE.md 7.2 unfalsifiable.

        Raises :class:`EventConflict` when the same key arrives with a different
        payload, because that means two different events were assigned the same
        identity and silently keeping either one would corrupt the replay.
        """
        await self._lock_task(task_id)
        existing = await self._find_by_key(task_id, idempotency_key)
        if existing is not None:
            if payload_hash(dict(existing.payload)) != payload_hash(payload):
                raise EventConflict(
                    f"idempotency key {idempotency_key!r} reused with "
                    "different payload"
                )
            return _to_entry(existing)

        next_sequence = await self._session.scalar(
            select(
                text("coalesce(max(sequence), 0) + 1")
            ).select_from(ScientificEventModel).where(
                ScientificEventModel.task_id == task_id
            )
        )
        row = ScientificEventModel(
            id=uuid4(),
            task_id=task_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
            sequence=int(next_sequence or 1),
            status=status,
            evidence_level=evidence_level,
            source_id=source_id,
            finding_id=finding_id,
            claim_id=claim_id,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_entry(row)

    async def get(self, event_id: UUID) -> LedgerEntry | None:
        row = await self._session.get(ScientificEventModel, event_id)
        return None if row is None else _to_entry(row)

    async def list_admitted(self, task_id: UUID) -> list[LedgerEntry]:
        """Return admitted events for one task in projection order."""
        result = await self._session.execute(
            select(ScientificEventModel)
            .where(
                ScientificEventModel.task_id == task_id,
                ScientificEventModel.status == "admitted",
            )
            .order_by(ScientificEventModel.sequence)
        )
        return [_to_entry(row) for row in result.scalars()]

    async def list_since(
        self,
        task_id: UUID,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[LedgerEntry]:
        """Return events after ``after_sequence`` in order.

        This is what backs SSE resume: a reconnecting client sends the sequence
        it last saw as ``Last-Event-ID`` and receives only what it missed.
        """
        statement = (
            select(ScientificEventModel)
            .where(
                ScientificEventModel.task_id == task_id,
                ScientificEventModel.sequence > after_sequence,
            )
            .order_by(ScientificEventModel.sequence)
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return [_to_entry(row) for row in result.scalars()]

    async def latest_sequence(self, task_id: UUID) -> int:
        """Return the highest sequence for a task, or 0 when it has no events."""
        value = await self._session.scalar(
            select(
                text("coalesce(max(sequence), 0)")
            ).select_from(ScientificEventModel).where(
                ScientificEventModel.task_id == task_id
            )
        )
        return int(value or 0)
