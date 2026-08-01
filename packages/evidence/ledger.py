from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID


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


def _payload_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class EventLedger:
    """In-memory implementation of the scientific event ledger.

    Production deployments would persist via the ScientificEventModel.
    """

    def __init__(self) -> None:
        self._entries: dict[UUID, LedgerEntry] = {}
        self._sequence = 0

    def append(
        self,
        task_id: UUID,
        event_type: str,
        payload: dict[str, object],
        idempotency_key: str,
        status: str = "pending",
    ) -> LedgerEntry:
        for entry in self._entries.values():
            if entry.idempotency_key == idempotency_key:
                if _payload_hash(entry.payload) == _payload_hash(payload):
                    return entry
                raise EventConflict(
                    f"idempotency key {idempotency_key!r} reused with "
                    "different payload"
                )
        self._sequence += 1
        event_id = UUID(int=self._sequence)
        entry = LedgerEntry(
            event_id=event_id,
            task_id=task_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
            sequence=self._sequence,
            status=status,
        )
        self._entries[event_id] = entry
        return entry

    def get(self, event_id: UUID) -> LedgerEntry | None:
        return self._entries.get(event_id)

    def list_admitted(self) -> list[LedgerEntry]:
        return [e for e in self._entries.values() if e.status == "admitted"]
