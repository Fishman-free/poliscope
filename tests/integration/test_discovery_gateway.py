from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio

from packages.kernel.contracts import FrozenDict
from packages.tools.adapters import adapter
from packages.tools.contracts import ToolRequest
from packages.tools.recorded import RecordedToolGateway, _request_hash

FIXED_TASK_ID = UUID("22222222-2222-2222-2222-222222222222")

ADAPTER_PAYLOADS: dict[str, dict[str, object]] = {
    "openalex": {
        "id": "W9999999999",
        "title": "Digital behavior and wellbeing",
        "authors": ["Smith", "Lee"],
        "year": 2023,
        "type": "journal-article",
        "retracted": False,
    },
    "crossref": {
        "title": "Digital behavior and wellbeing",
        "authors": ["Smith", "Lee"],
        "year": 2023,
        "type": "journal-article",
    },
    "unpaywall": {
        "oa_status": "hybrid",
        "oa_version": "accepted",
        "url": "https://repo.example.com/a.pdf",
    },
    "semantic_scholar": {
        "paper_id": "S9999999999",
        "title": "Digital behavior and wellbeing",
        "authors": ["Smith", "Lee"],
        "year": 2023,
        "publication_types": ["JournalArticle"],
    },
}


def _build_recording(adapter_name: str, doi: str) -> dict[str, object]:
    request = ToolRequest(
        task_id=FIXED_TASK_ID,
        actor="source_adapter",
        tool_name=adapter_name,
        operation="lookup_doi",
        arguments=FrozenDict({"doi": doi}),
    )
    return {
        "request_hash": _request_hash(request),
        "payload": {
            "payload": ADAPTER_PAYLOADS[adapter_name],
            "input_tokens": 32,
            "output_tokens": 16,
            "cost_usd": 1,
            "latency_ms": 100,
            "retries": 0,
            "error_code": None,
        },
    }


@pytest_asyncio.fixture
async def discovery_gateway(tmp_path: Path) -> RecordedToolGateway:
    doi = "10.9999/integration"
    recordings = [
        json.dumps(_build_recording(name, doi)) for name in ADAPTER_PAYLOADS
    ]
    path = tmp_path / "discovery_recordings.jsonl"
    path.write_text("\n".join(recordings) + "\n", encoding="utf-8")
    return RecordedToolGateway.from_path(path)


@pytest.mark.asyncio
async def test_four_adapters_return_consistent_doi(
    discovery_gateway: RecordedToolGateway,
) -> None:
    """All four adapters normalize the same DOI consistently."""
    for name in ADAPTER_PAYLOADS:
        source = await adapter(
            name, discovery_gateway, task_id=FIXED_TASK_ID
        ).lookup_doi("10.9999/integration")
        assert source.doi == "10.9999/integration"


@pytest.mark.asyncio
async def test_metadata_adapters_return_title(
    discovery_gateway: RecordedToolGateway,
) -> None:
    """OpenAlex, Crossref, Semantic Scholar return title metadata."""
    for name in ["openalex", "crossref", "semantic_scholar"]:
        source = await adapter(
            name, discovery_gateway, task_id=FIXED_TASK_ID
        ).lookup_doi("10.9999/integration")
        assert source.title == "Digital behavior and wellbeing"


@pytest.mark.asyncio
async def test_unpaywall_returns_oa_metadata_not_title(
    discovery_gateway: RecordedToolGateway,
) -> None:
    """Unpaywall returns OA metadata; title is empty (not in its domain)."""
    source = await adapter(
        "unpaywall", discovery_gateway, task_id=FIXED_TASK_ID
    ).lookup_doi("10.9999/integration")
    assert source.oa_status == "hybrid"
    assert source.oa_version == "accepted"
    assert source.title == ""


@pytest.mark.asyncio
async def test_tool_calls_are_recorded_via_gateway(
    discovery_gateway: RecordedToolGateway,
) -> None:
    """Each adapter call routes through the tool gateway."""
    for name in ADAPTER_PAYLOADS:
        await adapter(name, discovery_gateway, task_id=FIXED_TASK_ID).lookup_doi(
            "10.9999/integration"
        )
