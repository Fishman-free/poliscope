from __future__ import annotations

from decimal import Decimal
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
    cost_usd: Decimal
    latency_ms: int
    retries: int
    schema_status: SchemaStatus
    # Raw chain-of-thought the vendor actually returned (DeepSeek's
    # ``reasoning_content``, OpenAI-style ``reasoning``). Never derived or
    # summarised by us; ``None`` when the vendor did not return one (e.g.
    # thinking-mode-off calls). This is process material for the
    # chain-of-thought view, not part of the structured payload, and it never
    # reaches the Evidence Graph.
    reasoning: str | None = None


class ModelGateway(Protocol):
    async def invoke(self, request: ModelRequest) -> ModelResult: ...
