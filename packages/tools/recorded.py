from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from packages.kernel.recording import stable_request_hash
from packages.tools.contracts import ToolRequest, ToolResult

# An external lookup returns the same payload no matter which seat asked, and
# the shared candidate pool fetches each paper exactly once, so neither the
# requesting seat nor the task identity may take part in the recording key.
# The requesting seat is still persisted by the audit layer.
VOLATILE_REQUEST_FIELDS = frozenset({"task_id", "actor"})


class RecordingNotFound(Exception):
    """Raised when no recorded response exists for a request hash."""


def _request_hash(request: ToolRequest) -> str:
    return stable_request_hash(request, exclude=VOLATILE_REQUEST_FIELDS)


class RecordedToolGateway:
    """Deterministic tool gateway backed by a JSONL recording file."""

    def __init__(self, recordings: list[dict[str, object]]) -> None:
        self._recordings: dict[str, Any] = {}
        for entry in recordings:
            key = str(entry["request_hash"])
            self._recordings[key] = entry

    @classmethod
    def from_path(cls, path: Path) -> RecordedToolGateway:
        recordings: list[dict[str, object]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    recordings.append(cast(dict[str, object], json.loads(line)))
        return cls(recordings)

    async def execute(self, request: ToolRequest) -> ToolResult:
        key = _request_hash(request)
        if key not in self._recordings:
            raise RecordingNotFound(f"No recording for tool request hash: {key}")
        entry = cast(dict[str, Any], self._recordings[key])
        result = entry["payload"]
        return ToolResult(
            call_id=uuid4(),
            payload=cast(dict[str, object], result["payload"]),
            latency_ms=int(result["latency_ms"]),
            retries=int(result["retries"]),
            error_code=cast(str | None, result.get("error_code")),
        )
