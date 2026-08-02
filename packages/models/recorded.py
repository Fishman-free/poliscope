from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from packages.kernel.recording import stable_request_hash
from packages.models.contracts import (
    ModelRequest,
    ModelResult,
    SchemaStatus,
)

# task_id identifies the research task, not the prompt, so it must not take
# part in the recording key. Everything else changes what the model is asked.
VOLATILE_REQUEST_FIELDS = frozenset({"task_id"})


class RecordingNotFound(Exception):
    """Raised when no recorded response exists for a request hash."""


def _request_hash(request: ModelRequest) -> str:
    return stable_request_hash(request, exclude=VOLATILE_REQUEST_FIELDS)


class RecordedModelGateway:
    """Deterministic model gateway backed by a JSONL recording file."""

    def __init__(self, recordings: list[dict[str, object]]) -> None:
        self._recordings: dict[str, Any] = {}
        for entry in recordings:
            key = str(entry["request_hash"])
            self._recordings[key] = entry

    @classmethod
    def from_path(cls, path: Path) -> RecordedModelGateway:
        recordings: list[dict[str, object]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    recordings.append(cast(dict[str, object], json.loads(line)))
        return cls(recordings)

    async def invoke(self, request: ModelRequest) -> ModelResult:
        key = _request_hash(request)
        if key not in self._recordings:
            raise RecordingNotFound(f"No recording for model request hash: {key}")
        entry = cast(dict[str, Any], self._recordings[key])
        result = entry["payload"]
        return ModelResult(
            call_id=uuid4(),
            payload=cast(dict[str, object], result["payload"]),
            input_tokens=int(result["input_tokens"]),
            output_tokens=int(result["output_tokens"]),
            cost_usd=Decimal(str(result["cost_usd"])),
            latency_ms=int(result["latency_ms"]),
            retries=int(result["retries"]),
            schema_status=SchemaStatus(result["schema_status"]),
        )
