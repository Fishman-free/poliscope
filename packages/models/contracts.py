from __future__ import annotations

from collections.abc import Awaitable, Callable
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


class StreamEvent(ContractModel):
    """One incremental slice of a streaming model call.

    ``kind`` is ``"token"`` (structured output text), ``"reasoning"`` (the
    vendor's chain of thought, DeepSeek-style ``reasoning_content``) or
    ``"done"``. The worker relays these to ``process_stream`` for the live
    view; they are process material only and never reach the Evidence Graph.
    """

    kind: str
    text: str = ""


class StreamingModelGateway(Protocol):
    """Optional extension of :class:`ModelGateway`: emit the call live.

    A gateway implementing this streams the request to the provider and
    reports every delta through ``on_event`` before returning the same
    :class:`ModelResult` ``invoke`` would. Callers must treat this as an
    optimisation over ``invoke``, not a replacement: a streaming failure
    degrades to a plain ``invoke`` call, and the returned result is the
    source of truth either way.
    """

    async def stream_invoke(
        self,
        request: ModelRequest,
        on_event: Callable[[StreamEvent], Awaitable[None]],
    ) -> ModelResult: ...
