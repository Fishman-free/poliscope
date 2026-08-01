from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from packages.kernel.contracts import thaw_for_serialization
from packages.models.contracts import (
    ModelRequest,
    ModelResult,
    SchemaStatus,
)


class RecordingNotFound(Exception):
    """Raised when no recorded response exists for a request hash."""


def _normalize_request(request: ModelRequest) -> dict[str, object]:
    return cast(
        dict[str, object],
        thaw_for_serialization(request.model_dump(mode="json")),
    )


def _request_hash(request: ModelRequest) -> str:
    normalized = json.dumps(_normalize_request(request), sort_keys=True).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


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
            cost_usd=int(result["cost_usd"]),
            latency_ms=int(result["latency_ms"]),
            retries=int(result["retries"]),
            schema_status=SchemaStatus(result["schema_status"]),
        )
