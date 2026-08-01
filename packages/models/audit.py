from __future__ import annotations

import hashlib
import json

from sqlalchemy.ext.asyncio import AsyncSession

from packages.kernel.contracts import FrozenDict
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus
from packages.models.models import ModelCallModel
from packages.tools.contracts import ToolRequest, ToolResult
from packages.tools.models import ToolCallModel


def _request_hash(
    request: dict[str, object] | FrozenDict[str, object],
) -> str:
    normalized = json.dumps(request, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _safe_summary(payload: dict[str, object]) -> dict[str, object]:
    """Strip sensitive fields before persistence."""
    forbidden = {"signed_url", "pdf_binary", "local_path", "full_text"}
    return {k: v for k, v in payload.items() if k not in forbidden}


async def record_model_call(
    session: AsyncSession,
    request: ModelRequest,
    result: ModelResult,
) -> ModelCallModel:
    request_payload = request.model_dump(mode="json")
    row = ModelCallModel(
        task_id=request.task_id,
        status=result.schema_status.value,
        created_by=request.actor,
        actor=request.actor,
        model_class=request.model_class.value,
        purpose=request.purpose,
        output_schema=request.output_schema,
        evidence_refs=list(request.evidence_refs),
        input_hash=_request_hash(request_payload),
        output_hash=_request_hash(result.payload),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        retries=result.retries,
        schema_status=result.schema_status.value,
        request_summary=_safe_summary(request_payload),
    )
    session.add(row)
    await session.commit()
    return row


async def record_tool_call(
    session: AsyncSession,
    request: ToolRequest,
    result: ToolResult,
) -> ToolCallModel:
    request_payload = request.model_dump(mode="json")
    row = ToolCallModel(
        task_id=request.task_id,
        status=result.error_code or "ok",
        created_by=request.actor,
        actor=request.actor,
        tool_name=request.tool_name,
        operation=request.operation,
        input_hash=_request_hash(request_payload),
        output_hash=_request_hash(result.payload),
        cost_usd=0,
        latency_ms=result.latency_ms,
        retries=result.retries,
        error_code=result.error_code,
        evidence_refs=[],
        schema_status=SchemaStatus.OK.value,
        request_summary=_safe_summary(request_payload),
    )
    session.add(row)
    await session.commit()
    return row
