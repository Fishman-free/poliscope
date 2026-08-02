from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID, uuid4


class EventConflict(Exception):
    """Raised when the same idempotency key is reused with a different payload."""


class EventNotAdmitted(Exception):
    """Raised when the projector tries to project a non-ADMITTED event."""


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    event_id: UUID
    task_id: UUID
    event_type: str
    payload: dict[str, object]
    idempotency_key: str
    sequence: int
    status: str


def payload_hash(payload: dict[str, object]) -> str:
    """Hash a payload so that a replayed append can be recognised as identical.

    Sorting keys is what makes the hash stable: two dictionaries that differ only
    in insertion order describe the same event and must not look like a conflict.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class EventLedgerProtocol(Protocol):
    """The append-only interface the council and the projector depend on.

    Declared as a Protocol so that the orchestrator can be tested against the
    in-memory ledger while production runs on PostgreSQL, without either
    implementation importing the other.
    """

    def append(
        self,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
        status: str = ...,
    ) -> LedgerEntry: ...

    def get(self, event_id: UUID) -> LedgerEntry | None: ...

    def list_admitted(self) -> list[LedgerEntry]: ...


class EventLedger:
    """In-memory ledger used by unit tests and by the deterministic evaluator.

    Production uses :class:`packages.evidence.sql_ledger.SqlEventLedger`, which
    persists to ``scientific_events``. Both scope idempotency keys per task,
    matching the ``uq_event_idempotency`` constraint in revision 0003: two
    unrelated tasks may legitimately choose the same key.
    """

    def __init__(self) -> None:
        self._entries: dict[UUID, LedgerEntry] = {}
        self._sequences: dict[UUID, int] = {}

    def append(
        self,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
        status: str = "pending",
    ) -> LedgerEntry:
        for entry in self._entries.values():
            if entry.task_id != task_id:
                continue
            if entry.idempotency_key != idempotency_key:
                continue
            if payload_hash(entry.payload) == payload_hash(payload):
                return entry
            raise EventConflict(
                f"idempotency key {idempotency_key!r} reused with different payload"
            )
        sequence = self._sequences.get(task_id, 0) + 1
        self._sequences[task_id] = sequence
        entry = LedgerEntry(
            event_id=uuid4(),
            task_id=task_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
            sequence=sequence,
            status=status,
        )
        self._entries[entry.event_id] = entry
        return entry

    def get(self, event_id: UUID) -> LedgerEntry | None:
        return self._entries.get(event_id)

    def list_admitted(self) -> list[LedgerEntry]:
        return [
            entry for entry in self._entries.values() if entry.status == "admitted"
        ]

    def list_for_task(self, task_id: UUID) -> list[LedgerEntry]:
        """All entries for one task, in sequence order.

        Used by the deterministic evaluator (packages/evaluation/harness.py) to
        read back a run's full event stream -- admitted, quarantined, and
        process-only alike -- without reaching into ``_entries`` directly.
        """
        return sorted(
            (entry for entry in self._entries.values() if entry.task_id == task_id),
            key=lambda entry: entry.sequence,
        )

    def set_status(self, event_id: UUID, status: str) -> LedgerEntry:
        """Move an entry from ``pending`` to its verdict after gate evaluation.

        The in-memory ledger predates any gate being wired to it, so ``append``
        alone had no way to record a disposition decided after the fact. This is
        the minimal extension the deterministic evaluator needs, not a
        duplicate of ``SqlEventLedger`` -- that class persists to Postgres and
        the projector decides status by writing a new row instead.
        """
        entry = self._entries.get(event_id)
        if entry is None:
            raise KeyError(f"unknown event: {event_id}")
        updated = replace(entry, status=status)
        self._entries[event_id] = updated
        return updated
