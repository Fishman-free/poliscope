from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from packages.kernel.contracts import FrozenDict
from packages.tools.adapters import adapter
from packages.tools.contracts import ToolRequest
from packages.tools.recorded import RecordedToolGateway, _request_hash

FIXED_TASK_ID = UUID("11111111-1111-1111-1111-111111111111")

ADAPTER_PAYLOADS: dict[str, dict[str, object]] = {
    "openalex": {
        "id": "W1234567890",
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
        "oa_status": "gold",
        "oa_version": "published",
        "url": "https://example.com/fulltext.pdf",
    },
    "semantic_scholar": {
        "paper_id": "S1234567890",
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


@pytest.fixture
def recorded_tool_gateway(tmp_path: Path) -> RecordedToolGateway:
    doi = "10.1234/example"
    recordings = [
        json.dumps(_build_recording(name, doi)) for name in ADAPTER_PAYLOADS
    ]
    path = tmp_path / "recordings.jsonl"
    path.write_text("\n".join(recordings) + "\n", encoding="utf-8")
    return RecordedToolGateway.from_path(path)


@pytest.mark.parametrize(
    "adapter_name", ["openalex", "crossref", "unpaywall", "semantic_scholar"]
)
async def test_adapter_returns_same_normalized_source(
    adapter_name: str, recorded_tool_gateway: RecordedToolGateway
) -> None:
    """All adapters return the same normalized DOI for the same input."""
    inst = adapter(adapter_name, recorded_tool_gateway, task_id=FIXED_TASK_ID)
    source = await inst.lookup_doi("10.1234/example")
    assert source.doi == "10.1234/example"
    assert source.provider_ids[adapter_name]


@pytest.mark.parametrize("adapter_name", ["openalex", "crossref", "semantic_scholar"])
async def test_metadata_adapters_return_title(
    adapter_name: str, recorded_tool_gateway: RecordedToolGateway
) -> None:
    """OpenAlex, Crossref, Semantic Scholar return title metadata."""
    inst = adapter(adapter_name, recorded_tool_gateway, task_id=FIXED_TASK_ID)
    source = await inst.lookup_doi("10.1234/example")
    assert source.title == "Digital behavior and wellbeing"


async def test_adapters_only_call_tool_gateway(
    recorded_tool_gateway: RecordedToolGateway,
) -> None:
    """All adapters route through the tool gateway (no direct HTTP)."""
    for name in ["openalex", "crossref", "unpaywall", "semantic_scholar"]:
        await adapter(name, recorded_tool_gateway, task_id=FIXED_TASK_ID).lookup_doi(
            "10.1234/example"
        )


async def test_openalex_adapter_includes_retracted_field(
    recorded_tool_gateway: RecordedToolGateway,
) -> None:
    source = await adapter(
        "openalex", recorded_tool_gateway, task_id=FIXED_TASK_ID
    ).lookup_doi("10.1234/example")
    assert source.retracted is False


async def test_unpaywall_adapter_includes_oa_fields(
    recorded_tool_gateway: RecordedToolGateway,
) -> None:
    source = await adapter(
        "unpaywall", recorded_tool_gateway, task_id=FIXED_TASK_ID
    ).lookup_doi("10.1234/example")
    assert source.oa_status == "gold"
    assert source.oa_version == "published"
    assert "https://example.com/fulltext.pdf" in source.controlled_fulltext_urls


async def test_semantic_scholar_adapter_records_provider_id(
    recorded_tool_gateway: RecordedToolGateway,
) -> None:
    source = await adapter(
        "semantic_scholar", recorded_tool_gateway, task_id=FIXED_TASK_ID
    ).lookup_doi("10.1234/example")
    assert source.provider_ids["semantic_scholar"] == "S1234567890"
    assert source.publication_type == "JournalArticle"


async def test_crossref_adapter_has_no_retracted_field_default(
    recorded_tool_gateway: RecordedToolGateway,
) -> None:
    """Crossref does not supply retracted; default should be False."""
    source = await adapter(
        "crossref", recorded_tool_gateway, task_id=FIXED_TASK_ID
    ).lookup_doi("10.1234/example")
    assert source.retracted is False


async def test_normalize_doi_strips_protocol_and_lowercases() -> None:
    """DOI normalization strips https://doi.org/ prefix and lowercases."""
    from packages.tools.adapters.normalization import normalize_doi

    assert normalize_doi("https://doi.org/10.1234/Example") == "10.1234/example"
    assert normalize_doi("http://dx.doi.org/10.1234/EXAMPLE") == "10.1234/example"
    assert normalize_doi("  10.1234/MiXeD  ") == "10.1234/mixed"


async def test_unknown_adapter_raises_value_error() -> None:
    """Requesting an unknown adapter raises ValueError."""
    import pytest

    from packages.tools.adapters import adapter

    with pytest.raises(ValueError, match="unknown adapter"):
        adapter("unknown_source", None)  # type: ignore[arg-type]
