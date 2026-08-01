from __future__ import annotations

from uuid import uuid4

from apps.api.schemas import SafetyNotice, WorkspaceSnapshot


def test_workspace_dto_is_whitelisted() -> None:
    ws = WorkspaceSnapshot(
        task={"id": str(uuid4()), "question": "test"},
        brief={"status": "running"},
        seats=(),
        graph={"nodes": [], "edges": []},
        blindspots=(),
        discriminating_studies=(),
        dissents=(),
        evolution=(),
        paper_count=0,
        independent_cluster_count=0,
        workspace_version=1,
        safety_notice=SafetyNotice(),
    )
    body = ws.model_dump()
    assert set(body) == {
        "task",
        "brief",
        "seats",
        "graph",
        "blindspots",
        "discriminating_studies",
        "dissents",
        "evolution",
        "paper_count",
        "independent_cluster_count",
        "workspace_version",
        "safety_notice",
    }
    assert "private_reasoning" not in body


def test_safety_notice_has_required_fields() -> None:
    notice = SafetyNotice()
    assert len(notice.classification) > 0
    assert len(notice.medical_disclaimer) > 0
    assert len(notice.limitations) > 0


def test_suite() -> None:
    test_workspace_dto_is_whitelisted()
    test_safety_notice_has_required_fields()


def test_sse_event_format() -> None:
    from apps.api.schemas import SSEEvent
    event = SSEEvent(
        event_id="evt-1",
        task_id=str(uuid4()),
        kind="workspace_update",
        workspace_version=1,
        payload={"status": "running"},
    )
    assert event.event_id == "evt-1"
    assert event.kind == "workspace_update"


def test_sse_resume_after_last_event_id() -> None:
    from apps.api.schemas import SSEEvent
    events = [
        SSEEvent(
            event_id=f"evt-{i}",
            task_id=str(uuid4()),
            kind="update",
            workspace_version=i,
            payload={},
        )
        for i in range(1, 5)
    ]
    after = [e for e in events if int(e.event_id.split("-")[1]) > 2]
    assert [e.event_id for e in after] == ["evt-3", "evt-4"]
