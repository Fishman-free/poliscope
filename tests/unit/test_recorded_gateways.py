from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from packages.models.contracts import (
    ModelClass,
    ModelMessage,
    ModelRequest,
)
from packages.models.recorded import RecordedModelGateway
from packages.models.recorded import RecordingNotFound as ModelRecordingNotFound
from packages.tools.contracts import ToolRequest
from packages.tools.recorded import RecordedToolGateway
from packages.tools.recorded import RecordingNotFound as ToolRecordingNotFound


def _make_model_recording(request_hash: str) -> dict[str, object]:
    return {
        "request_hash": request_hash,
        "payload": {
            "payload": {
                "summary": (
                    "Digital behavior is weakly associated with "
                    "mental health outcomes."
                )
            },
            "input_tokens": 256,
            "output_tokens": 128,
            "cost_usd": 5,
            "latency_ms": 350,
            "retries": 0,
            "schema_status": "ok",
        },
    }


def _make_tool_recording(request_hash: str) -> dict[str, object]:
    return {
        "request_hash": request_hash,
        "payload": {
            "payload": {
                "matches": [
                    {"doi": "10.1234/example", "title": "Digital behavior"}
                ]
            },
            "input_tokens": 64,
            "output_tokens": 32,
            "cost_usd": 2,
            "latency_ms": 120,
            "retries": 0,
            "error_code": None,
        },
    }


@pytest.fixture
def model_request() -> ModelRequest:
    return ModelRequest(
        task_id=uuid4(),
        actor="theory_builder",
        purpose="summarize evidence",
        model_class=ModelClass.STRONG_REASONING,
        messages=(ModelMessage(role="user", content="summarize"),),
        output_schema="summary",
        evidence_refs=(),
    )


@pytest.fixture
def tool_request() -> ToolRequest:
    return ToolRequest(
        task_id=uuid4(),
        actor="theory_builder",
        tool_name="openalex",
        operation="lookup_doi",
        arguments={"doi": "10.1234/example"},
    )


@pytest.fixture
def recorded_model_gateway(
    tmp_path: Path,
    model_request: ModelRequest,
) -> RecordedModelGateway:
    from packages.models.recorded import _request_hash

    request_hash = _request_hash(model_request)
    recording = _make_model_recording(request_hash)
    path = tmp_path / "model_recordings.jsonl"
    path.write_text(json.dumps(recording) + "\n")
    return RecordedModelGateway.from_path(path)


@pytest.fixture
def recorded_tool_gateway(
    tmp_path: Path,
    tool_request: ToolRequest,
) -> RecordedToolGateway:
    from packages.tools.recorded import _request_hash

    request_hash = _request_hash(tool_request)
    recording = _make_tool_recording(request_hash)
    path = tmp_path / "tool_recordings.jsonl"
    path.write_text(json.dumps(recording) + "\n")
    return RecordedToolGateway.from_path(path)


async def test_recorded_model_gateway_is_hash_deterministic(
    recorded_model_gateway: RecordedModelGateway,
    model_request: ModelRequest,
) -> None:
    first = await recorded_model_gateway.invoke(model_request)
    second = await recorded_model_gateway.invoke(model_request)
    assert first.payload == second.payload
    assert first.call_id != second.call_id


async def test_recorded_tool_gateway_is_hash_deterministic(
    recorded_tool_gateway: RecordedToolGateway,
    tool_request: ToolRequest,
) -> None:
    first = await recorded_tool_gateway.execute(tool_request)
    second = await recorded_tool_gateway.execute(tool_request)
    assert first.payload == second.payload
    assert first.call_id != second.call_id


def test_model_recording_key_ignores_task_identity(
    model_request: ModelRequest,
) -> None:
    """A frozen recording must survive a new task_id on the next run."""
    from packages.models.recorded import _request_hash

    other_task = model_request.model_dump()
    other_task["task_id"] = uuid4()
    assert other_task["task_id"] != model_request.task_id
    assert _request_hash(ModelRequest(**other_task)) == _request_hash(model_request)


def test_model_recording_key_tracks_prompt_content(
    model_request: ModelRequest,
) -> None:
    changed = model_request.model_dump()
    changed["messages"] = ({"role": "user", "content": "critique"},)
    from packages.models.recorded import _request_hash

    assert _request_hash(ModelRequest(**changed)) != _request_hash(model_request)


def test_tool_recording_key_ignores_task_and_requesting_seat(
    tool_request: ToolRequest,
) -> None:
    """One shared fetch per paper, so the asking seat cannot change the key."""
    from packages.tools.recorded import _request_hash

    other = tool_request.model_dump()
    other["task_id"] = uuid4()
    other["actor"] = "adversarial_falsifier"
    assert _request_hash(ToolRequest(**other)) == _request_hash(tool_request)


def test_tool_recording_key_tracks_arguments(
    tool_request: ToolRequest,
) -> None:
    from packages.tools.recorded import _request_hash

    changed = tool_request.model_dump()
    changed["arguments"] = {"doi": "10.5678/other"}
    assert _request_hash(ToolRequest(**changed)) != _request_hash(tool_request)


async def test_recorded_model_gateway_raises_on_missing_recording(
    tmp_path: Path,
    model_request: ModelRequest,
) -> None:
    gateway = RecordedModelGateway.from_path(tmp_path / "missing.jsonl")
    with pytest.raises(ModelRecordingNotFound):
        await gateway.invoke(model_request)


async def test_recorded_tool_gateway_raises_on_missing_recording(
    tmp_path: Path,
    tool_request: ToolRequest,
) -> None:
    gateway = RecordedToolGateway.from_path(tmp_path / "missing.jsonl")
    with pytest.raises(ToolRecordingNotFound):
        await gateway.execute(tool_request)
