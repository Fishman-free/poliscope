"""Tests for the task lifecycle endpoints.

The earlier version constructed the request DTOs and asserted Pydantic had
stored their fields, which exercised Pydantic rather than Poliscope. These tests
drive the real routes and assert the state the database ends up in.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx

from apps.api.schemas import ConfirmClaimsRequest, CreateTaskRequest
from packages.epistemo.contracts import TaskStatus
from tests.factories import make_research_contract


def _contract_payload() -> dict[str, Any]:
    payload: dict[str, Any] = make_research_contract().model_dump(mode="json")
    return payload


async def _create(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/tasks", json=_contract_payload())
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_request_dtos_accept_the_contract_shape() -> None:
    """The wire contract and the domain contract must stay compatible."""
    payload = _contract_payload()
    request = CreateTaskRequest(
        question=payload["question"],
        scope=payload["scope"],
        budget=payload["budget"],
        user_evidence=payload["user_evidence"],
    )
    assert request.question == payload["question"]
    assert len(ConfirmClaimsRequest(claim_ids=(uuid4(), uuid4())).claim_ids) == 2


async def test_create_returns_suggested_claims_and_does_not_queue(
    api_client: httpx.AsyncClient,
) -> None:
    """CLAUDE.md 2 keeps the researcher in control of what gets investigated."""
    body = await _create(api_client)
    assert body["status"] == TaskStatus.AWAITING_CLAIM_CONFIRMATION
    assert len(body["suggested_claims"]) >= 2
    assert all(
        {"id", "statement", "claim_type", "falsification_condition"} <= set(claim)
        for claim in body["suggested_claims"]
    )


async def test_created_task_is_readable_back(
    api_client: httpx.AsyncClient,
) -> None:
    """A task that only existed in the creating process would not be resumable."""
    created = await _create(api_client)
    response = await api_client.get(f"/api/tasks/{created['task_id']}")
    assert response.status_code == 200
    assert response.json()["question"] == _contract_payload()["question"]


async def test_confirming_claims_queues_the_task(
    api_client: httpx.AsyncClient,
) -> None:
    created = await _create(api_client)
    chosen = created["suggested_claims"][0]["id"]
    response = await api_client.post(
        f"/api/tasks/{created['task_id']}/confirm-claims",
        json={"claim_ids": [chosen]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == TaskStatus.QUEUED

    readback = await api_client.get(f"/api/tasks/{created['task_id']}")
    assert readback.json()["status"] == TaskStatus.QUEUED


async def test_unconfirmed_claims_are_reported_as_discarded_not_dropped(
    api_client: httpx.AsyncClient,
) -> None:
    """CLAUDE.md 5.3 forbids removing what the council once considered."""
    created = await _create(api_client)
    suggested = created["suggested_claims"]
    chosen = suggested[0]["id"]
    body = (
        await api_client.post(
            f"/api/tasks/{created['task_id']}/confirm-claims",
            json={"claim_ids": [chosen]},
        )
    ).json()
    statuses = {claim["id"]: claim["status"] for claim in body["claims"]}
    assert len(statuses) == len(suggested)
    assert statuses[chosen] == "CONFIRMED"
    assert all(
        statuses[claim["id"]] == "DISCARDED"
        for claim in suggested
        if claim["id"] != chosen
    )


async def test_confirming_a_claim_from_another_task_is_refused(
    api_client: httpx.AsyncClient,
) -> None:
    """A claim id is not a capability to modify an unrelated task."""
    first = await _create(api_client)
    second = await _create(api_client)
    response = await api_client.post(
        f"/api/tasks/{first['task_id']}/confirm-claims",
        json={"claim_ids": [second["suggested_claims"][0]["id"]]},
    )
    assert response.status_code == 422
    assert "do not belong to task" in response.json()["detail"]


async def test_confirming_no_claims_is_refused(
    api_client: httpx.AsyncClient,
) -> None:
    created = await _create(api_client)
    response = await api_client.post(
        f"/api/tasks/{created['task_id']}/confirm-claims",
        json={"claim_ids": []},
    )
    assert response.status_code == 422


async def test_confirming_claims_on_an_unknown_task_returns_404(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        f"/api/tasks/{uuid4()}/confirm-claims",
        json={"claim_ids": [str(uuid4())]},
    )
    assert response.status_code == 404


async def test_unknown_task_returns_404(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get(f"/api/tasks/{uuid4()}")).status_code == 404


async def test_an_invalid_contract_is_rejected_with_422(
    api_client: httpx.AsyncClient,
) -> None:
    payload = _contract_payload()
    payload["budget"]["wall_clock_minutes"] = 0
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 422


async def test_a_scope_with_an_inverted_date_range_is_rejected(
    api_client: httpx.AsyncClient,
) -> None:
    """The contract validator must run on the API path, not only in unit tests."""
    payload = _contract_payload()
    payload["scope"]["date_from"] = "2030-01-01"
    payload["scope"]["date_until"] = "2020-01-01"
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 422
