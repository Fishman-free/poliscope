"""Tests for the workspace snapshot endpoint.

The previous version of this file asserted that a list comprehension written
inside the test filtered a list it had just built. That proves nothing about the
API, so these tests go over HTTP against the real application and a real
database instead.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from apps.api.schemas import SafetyNotice, WorkspaceSnapshot
from tests.factories import make_research_contract

WORKSPACE_FIELDS = {
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
    "paper",
    "consensus",
}


async def _create_task(client: httpx.AsyncClient) -> str:
    contract = make_research_contract().model_dump(mode="json")
    response = await client.post("/api/tasks", json=contract)
    assert response.status_code == 201, response.text
    task_id: str = response.json()["task_id"]
    return task_id


def test_workspace_dto_exposes_exactly_the_whitelisted_fields() -> None:
    """No private reasoning may leak through the DTO.

    CLAUDE.md 11 forbids showing model chain-of-thought, and a field added to
    the snapshot without thought is the most likely way it would escape.
    """
    snapshot = WorkspaceSnapshot(
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
    assert set(snapshot.model_dump()) == WORKSPACE_FIELDS


def test_safety_notice_states_the_research_only_positioning() -> None:
    """CLAUDE.md 16 requires the medical disclaimer to travel with the data."""
    notice = SafetyNotice()
    assert notice.classification
    assert notice.medical_disclaimer
    assert notice.limitations


async def test_workspace_returns_the_whitelisted_shape_over_http(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)
    response = await api_client.get(f"/api/workspace/{task_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert set(body) == WORKSPACE_FIELDS
    assert body["task"]["task_id"] == task_id


async def test_workspace_reports_paper_and_cluster_counts_separately(
    api_client: httpx.AsyncClient,
) -> None:
    """CLAUDE.md 7.4 requires both numbers to reach the interface.

    A single count invites the reader to mistake paper volume for evidence
    volume, which is the error the whole independence model exists to prevent.
    """
    task_id = await _create_task(api_client)
    body = (await api_client.get(f"/api/workspace/{task_id}")).json()
    assert body["paper_count"] == 0
    assert body["independent_cluster_count"] == 0


async def test_workspace_carries_the_safety_notice(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)
    body = (await api_client.get(f"/api/workspace/{task_id}")).json()
    assert body["safety_notice"]["medical_disclaimer"]


async def test_unknown_task_returns_404(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(f"/api/workspace/{uuid4()}")
    assert response.status_code == 404


async def test_malformed_task_id_returns_422(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.get("/api/workspace/not-a-uuid")
    assert response.status_code == 422


async def test_health_reports_database_reachability(
    api_client: httpx.AsyncClient,
) -> None:
    """A liveness check that never queries would report ok while all calls fail."""
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
