from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from packages.kernel.contracts import ContractModel, FrozenDict


class ModelClass(StrEnum):
    STRONG_REASONING = "strong_reasoning"
    MEDIUM = "medium"
    LIGHTWEIGHT = "lightweight"


class SchemaStatus(StrEnum):
    OK = "ok"
    REPAIRED = "repaired"
    QUARANTINED = "quarantined"


class ModelMessage(ContractModel):
    role: str
    content: str


class ModelRequest(ContractModel):
    task_id: UUID
    actor: str
    purpose: str
    model_class: ModelClass
    messages: tuple[ModelMessage, ...]
    output_schema: str
    evidence_refs: tuple[UUID, ...]


class ModelResult(ContractModel):
    call_id: UUID
    payload: FrozenDict[str, object]
    input_tokens: int
    output_tokens: int
    cost_usd: int
    latency_ms: int
    retries: int
    schema_status: SchemaStatus


class ModelGateway(Protocol):
    async def invoke(self, request: ModelRequest) -> ModelResult: ...
