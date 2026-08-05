"""Knowledge-base management endpoints, end to end.

A knowledge base is the researcher's long-term memory: upload once, parse to
text, reuse across tasks. These tests pin the API contract -- creation,
upload with validation, preview truncation, and reference-checked deletion.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import fitz  # type: ignore[import-untyped]
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.schemas import CreateTaskRequest
from packages.research.models import ResearchTaskModel
from tests.factories import make_research_contract

KB_PATH = "/api/knowledge-bases"


def _pdf_bytes(text: str = "Knowledge base document body.") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    return bytes(document.tobytes())


async def _create_kb(
    api_client: httpx.AsyncClient,
    name: str = "mental-health-lit",
) -> dict[str, Any]:
    response = await api_client.post(KB_PATH, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _upload(
    api_client: httpx.AsyncClient,
    kb_id: str,
    content: bytes,
    filename: str = "paper.pdf",
) -> httpx.Response:
    return await api_client.post(
        f"{KB_PATH}/{kb_id}/documents/upload",
        files={"file": (filename, content, "application/pdf")},
    )


async def test_create_and_list_knowledge_bases(
    api_client: httpx.AsyncClient,
) -> None:
    created = await _create_kb(api_client, name="my kb")
    assert created["name"] == "my kb"
    assert created["document_count"] == 0

    response = await api_client.get(KB_PATH)
    assert response.status_code == 200
    ids = [kb["id"] for kb in response.json()]
    assert created["id"] in ids


async def test_create_knowledge_base_requires_a_name(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(KB_PATH, json={"name": "   "})
    assert response.status_code == 422


async def test_upload_document_extracts_text_and_page_count(
    api_client: httpx.AsyncClient,
) -> None:
    kb = await _create_kb(api_client)
    response = await _upload(api_client, kb["id"], _pdf_bytes("A real PDF."))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["page_count"] == 1
    assert body["title"] == "paper.pdf"
    assert body["size_bytes"] > 0

    detail = await api_client.get(f"{KB_PATH}/{kb['id']}")
    assert detail.status_code == 200
    assert len(detail.json()["documents"]) == 1

    preview = await api_client.get(
        f"{KB_PATH}/{kb['id']}/documents/{body['document_id']}"
    )
    assert preview.status_code == 200
    assert "A real PDF." in preview.json()["text"]
    assert preview.json()["truncated"] is False


async def test_upload_rejects_non_pdf_and_oversized(
    api_client: httpx.AsyncClient,
) -> None:
    kb = await _create_kb(api_client)
    non_pdf = await _upload(api_client, kb["id"], b"PK\x03\x04 not a pdf")
    assert non_pdf.status_code == 422
    assert "not a PDF" in non_pdf.text

    oversized = await _upload(
        api_client, kb["id"], b"%PDF" + b"\0" * (21 * 1024 * 1024)
    )
    assert oversized.status_code == 422
    assert "20 MB" in oversized.text


async def test_upload_to_unknown_knowledge_base_returns_404(
    api_client: httpx.AsyncClient,
) -> None:
    response = await _upload(api_client, str(uuid4()), _pdf_bytes("x"))
    assert response.status_code == 404


async def test_delete_document_and_delete_base(
    api_client: httpx.AsyncClient,
) -> None:
    kb = await _create_kb(api_client)
    uploaded = await _upload(api_client, kb["id"], _pdf_bytes("To be deleted."))
    doc_id = uploaded.json()["document_id"]

    response = await api_client.delete(f"{KB_PATH}/{kb['id']}/documents/{doc_id}")
    assert response.status_code == 204
    detail = await api_client.get(f"{KB_PATH}/{kb['id']}")
    assert detail.json()["documents"] == []

    response = await api_client.delete(f"{KB_PATH}/{kb['id']}")
    assert response.status_code == 204
    assert (await api_client.get(f"{KB_PATH}/{kb['id']}")).status_code == 404


async def test_create_task_with_knowledge_base_links_task(
    api_client: httpx.AsyncClient,
    app_sessions: async_sessionmaker[AsyncSession],
) -> None:
    kb = await _create_kb(api_client)
    payload = make_research_contract().model_dump(mode="json")
    payload["knowledge_base_id"] = kb["id"]
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 201, response.text
    task_id = response.json()["task_id"]

    async with app_sessions() as session:
        row = (
            await session.execute(
                select(ResearchTaskModel).where(
                    ResearchTaskModel.task_id == task_id
                )
            )
        ).scalar_one()
    assert row.knowledge_base_id is not None
    # The id round-trips as a plain UUID string, never as a nested object.
    assert str(row.knowledge_base_id) == kb["id"]


async def test_create_task_with_unknown_knowledge_base_404(
    api_client: httpx.AsyncClient,
) -> None:
    payload = make_research_contract().model_dump(mode="json")
    payload["knowledge_base_id"] = str(uuid4())
    response = await api_client.post("/api/tasks", json=payload)
    assert response.status_code == 404
    assert "knowledge base" in response.text


async def test_request_dto_accepts_knowledge_base_id() -> None:
    payload = make_research_contract().model_dump(mode="json")
    payload["knowledge_base_id"] = str(uuid4())
    request = CreateTaskRequest(
        question=payload["question"],
        scope=payload["scope"],
        budget=payload["budget"],
        user_evidence=payload["user_evidence"],
        knowledge_base_id=payload["knowledge_base_id"],
    )
    assert request.knowledge_base_id is not None
