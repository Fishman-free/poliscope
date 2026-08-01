from __future__ import annotations

from uuid import uuid4

from apps.api.schemas import SSEEvent


def test_sse_event_has_required_fields() -> None:
    event = SSEEvent(
        event_id="evt-1",
        task_id=str(uuid4()),
        kind="workspace_update",
        workspace_version=1,
        payload={"status": "running"},
    )
    dumped = event.model_dump()
    assert "event_id" in dumped
    assert "task_id" in dumped
    assert "kind" in dumped
    assert "workspace_version" in dumped


def test_sse_event_formats_with_id_event_data() -> None:
    event = SSEEvent(
        event_id="evt-1",
        task_id=str(uuid4()),
        kind="update",
        workspace_version=1,
        payload={"x": 1},
    )
    formatted = event.format_sse()
    assert formatted.startswith("id: evt-1\n")
    assert "event: update\n" in formatted
    assert "data:" in formatted


def test_suite() -> None:
    test_sse_event_has_required_fields()
    test_sse_event_formats_with_id_event_data()
