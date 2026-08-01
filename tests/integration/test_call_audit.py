from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.models.audit import _request_hash, _safe_summary
from packages.models.contracts import (
    ModelClass,
    ModelMessage,
    ModelRequest,
)
from packages.models.models import ModelCallModel
from packages.models.recorded import RecordedModelGateway
from packages.tools.contracts import ToolRequest
from packages.tools.models import ToolCallModel
from packages.tools.recorded import RecordedToolGateway


@pytest.fixture
def model_request(seeded_task: UUID) -> ModelRequest:
    return ModelRequest(
        task_id=seeded_task,
        actor="theory_builder",
        purpose="summarize evidence",
        model_class=ModelClass.STRONG_REASONING,
        messages=(ModelMessage(role="user", content="summarize"),),
        output_schema="summary",
        evidence_refs=(),
    )


@pytest.fixture
def tool_request(seeded_task: UUID) -> ToolRequest:
    return ToolRequest(
        task_id=seeded_task,
        actor="theory_builder",
        tool_name="openalex",
        operation="lookup_doi",
        arguments={"doi": "10.1234/example"},
    )


@pytest.fixture
def recorded_model_gateway() -> RecordedModelGateway:
    recordings = json.loads(
        Path("tests/fixtures/recordings/gateways.jsonl").read_text().splitlines()[0]
    )
    return RecordedModelGateway([recordings])


@pytest.fixture
def recorded_tool_gateway() -> RecordedToolGateway:
    recordings = json.loads(
        Path("tests/fixtures/recordings/gateways.jsonl").read_text().splitlines()[1]
    )
    return RecordedToolGateway([recordings])


async def test_each_gateway_attempt_is_audited(
    recorded_tool_gateway: RecordedToolGateway,
    tool_request: ToolRequest,
    app_session: AsyncSession,
) -> None:
    from packages.tools.audit import AuditedToolGateway

    audited = AuditedToolGateway(recorded_tool_gateway, app_session)
    await audited.execute(tool_request)
    stmt = select(ToolCallModel).where(ToolCallModel.task_id == tool_request.task_id)
    row = (await app_session.execute(stmt)).scalar_one_or_none()
    assert row is not None
    assert row.input_hash
    assert row.output_hash
    assert row.latency_ms >= 0
    assert row.retries == 0
    assert "signed_url" not in (row.request_summary or {})


async def test_model_gateway_attempt_is_audited(
    recorded_model_gateway: RecordedModelGateway,
    model_request: ModelRequest,
    app_session: AsyncSession,
) -> None:
    from packages.models.audit import record_model_call

    result = await recorded_model_gateway.invoke(model_request)
    await record_model_call(app_session, model_request, result)
    stmt = select(ModelCallModel).where(ModelCallModel.task_id == model_request.task_id)
    row = (await app_session.execute(stmt)).scalar_one_or_none()
    assert row is not None
    assert row.input_hash
    assert row.output_hash
    assert row.schema_status == "ok"


def test_safe_summary_strips_signed_url() -> None:
    summary = _safe_summary({"query": "test", "signed_url": "https://example.com"})
    assert "signed_url" not in summary
    assert summary["query"] == "test"


def test_request_hash_is_stable() -> None:
    payload = {"query": "test", "limit": 10}
    assert _request_hash(payload) == _request_hash(payload)
