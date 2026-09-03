"""Integration tests for the research-tools endpoints.

Covers the HTTP surface added for A1-A4 / B6 / C9-C10 / D12:
- A2 read-only share mint / public read / revoke (redaction included);
- A3 replay status gate and the deterministic claim-set compare;
- A4 save-to-knowledge terminal-state gate;
- B6 researcher adjudication appending a PROCESS-only ledger event;
- C9 annotation batch lifecycle with inter-rater agreement;
- C10 model hot-swap state gate (draft editable, RUNNING refused 409).

Everything goes over HTTP against the real ASGI app and a real PostgreSQL
container, so routing, dependency injection and role grants are all exercised.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.evidence.models import ScientificEventModel
from tests.factories import make_research_contract


async def _create_task(client: httpx.AsyncClient) -> str:
    contract = make_research_contract().model_dump(mode="json")
    response = await client.post("/api/tasks", json=contract)
    assert response.status_code == 201, response.text
    return str(response.json()["task_id"])


# --- A2 read-only share ----------------------------------------------------


async def test_share_mint_public_read_and_revoke(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)

    minted = await api_client.post(f"/api/tasks/{task_id}/share", json={})
    assert minted.status_code == 200, minted.text
    token = minted.json()["share_token"]
    assert token and isinstance(token, str)

    # The public endpoint resolves the token WITHOUT authentication semantics
    # (the authed test client still proves the route works; the point is the
    # route uses no CurrentUserDep) and returns a redacted snapshot.
    public = await api_client.get(f"/api/shared/{token}")
    assert public.status_code == 200, public.text
    body = public.json()
    assert body["task"]["question"]
    # Share metadata and model configuration must never reach a public reader.
    assert "share_token" not in body["task"]
    assert "effective_model_config" not in body["task"]
    assert body["usage"] is None

    revoked = await api_client.delete(f"/api/tasks/{task_id}/share")
    assert revoked.status_code == 200, revoked.text
    gone = await api_client.get(f"/api/shared/{token}")
    assert gone.status_code == 404


async def test_unknown_share_token_is_404(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(f"/api/shared/does-not-exist-{uuid4()}")
    assert response.status_code == 404


# --- A3 time travel --------------------------------------------------------


async def test_replay_refuses_a_task_that_is_not_finished(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)  # sits at AWAITING_CLAIM_CONFIRMATION
    response = await api_client.post(
        f"/api/tasks/{task_id}/replay", json={"corpus_cutoff": "2018-12-31"}
    )
    assert response.status_code == 409, response.text


async def test_compare_two_owned_tasks_returns_set_difference(
    api_client: httpx.AsyncClient,
) -> None:
    left = await _create_task(api_client)
    right = await _create_task(api_client)
    response = await api_client.get(f"/api/tasks/{left}/compare/{right}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_a"]["task_id"] == left
    assert body["task_b"]["task_id"] == right
    assert isinstance(body["shared"], list)
    assert isinstance(body["only_in_a"], list)
    assert isinstance(body["only_in_b"], list)


# --- A4 save to knowledge --------------------------------------------------


async def test_save_to_knowledge_requires_terminal_task(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)
    response = await api_client.post(
        f"/api/tasks/{task_id}/save-to-knowledge",
        json={"knowledge_base_id": str(uuid4())},
    )
    # Draft task: the state gate fires before any knowledge-base lookup.
    assert response.status_code == 409, response.text


# --- B6 researcher adjudication -------------------------------------------


async def test_adjudication_appends_a_process_event_only(
    api_client: httpx.AsyncClient,
    app_session: AsyncSession,
) -> None:
    task_id = await _create_task(api_client)
    response = await api_client.post(
        f"/api/tasks/{task_id}/adjudicate",
        json={"target_key": "candidate-1", "decision": "保持分离", "note": "x"},
    )
    assert response.status_code == 200, response.text

    rows = (
        await app_session.execute(
            select(ScientificEventModel).where(
                ScientificEventModel.task_id == UUID(task_id)
            )
        )
    ).scalars().all()
    adjudications = [row for row in rows if row.event_type == "RESEARCHER_ADJUDICATION"]
    assert len(adjudications) == 1
    assert adjudications[0].payload["target_key"] == "candidate-1"
    assert adjudications[0].payload["decision"] == "保持分离"


async def test_adjudication_rejects_blank_target(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)
    response = await api_client.post(
        f"/api/tasks/{task_id}/adjudicate",
        json={"target_key": "  ", "decision": ""},
    )
    assert response.status_code == 422


# --- C9 human annotation ---------------------------------------------------


async def test_annotation_batch_labels_and_agreement(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)
    created = await api_client.post(
        f"/api/tasks/{task_id}/annotation-batches",
        json={
            "title": "batch",
            "note": "",
            "items": [
                {
                    "ref_kind": "blindspot",
                    "ref_node_id": str(uuid4()),
                    "statement": "Possible publication bias",
                    "position": {},
                },
                {
                    "ref_kind": "claim",
                    "ref_node_id": str(uuid4()),
                    "statement": "Correlation is not causation here",
                    "position": {},
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    batch_id = created.json()["batch_id"]
    assert created.json()["item_count"] == 2

    listed = await api_client.get(f"/api/tasks/{task_id}/annotation-batches")
    assert listed.status_code == 200
    assert any(b["id"] == batch_id for b in listed.json())

    # Two raters agree on both items -> Cohen's kappa is 1.0.
    detail_url = f"/api/annotation-batches/{batch_id}"
    detail = (await api_client.get(detail_url)).json()
    item_ids = [item["id"] for item in detail["items"]]
    for rater in ("alice", "bob"):
        for item_id in item_ids:
            labelled = await api_client.post(
                f"/api/annotation-batches/{batch_id}/labels",
                json={
                    "item_id": item_id,
                    "rater_name": rater,
                    "label": "relevant",
                    "note": "",
                },
            )
            assert labelled.status_code == 200, labelled.text

    final = (await api_client.get(detail_url)).json()
    assert final["agreement"]["rater_count"] == 2
    assert final["agreement"]["method"] == "cohen_kappa"
    assert final["agreement"]["score"] == 1.0


# --- C10 model hot-swap ----------------------------------------------------


async def test_model_overridable_while_draft_and_cleared(
    api_client: httpx.AsyncClient,
) -> None:
    task_id = await _create_task(api_client)  # draft state is swappable
    applied = await api_client.put(
        f"/api/tasks/{task_id}/model-override",
        json={
            "config": {
                "base_url": "https://example.invalid/v1",
                "api_key": "sk-test",
                "model_name": "m1",
            },
            "clear": False,
        },
    )
    assert applied.status_code == 200, applied.text
    cleared = await api_client.put(
        f"/api/tasks/{task_id}/model-override", json={"config": None, "clear": True}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["override"] is None


async def test_model_override_refused_while_running(
    api_client: httpx.AsyncClient,
    app_session: AsyncSession,
    account: dict[str, Any],
) -> None:
    from packages.research.models import ResearchTaskModel

    task_id = uuid4()
    app_session.add(
        ResearchTaskModel(
            id=uuid4(),
            task_id=task_id,
            question="running task",
            status="RUNNING",
            created_by="test_harness",
            user_id=UUID(account["id"]),
            wall_clock_minutes=60,
            model_cost_usd=Decimal("10.0000"),
            tool_call_limit=100,
            source_limit=50,
            user_evidence={},
        )
    )
    # Commit (not just flush): the API reads through a separate role
    # connection and cannot see an uncommitted transaction.
    await app_session.commit()

    response = await api_client.put(
        f"/api/tasks/{task_id}/model-override",
        json={"config": None, "clear": True},
    )
    assert response.status_code == 409, response.text
