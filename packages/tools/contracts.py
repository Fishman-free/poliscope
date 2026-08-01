from __future__ import annotations

from typing import Protocol
from uuid import UUID

from packages.kernel.contracts import ContractModel, FrozenDict


class ToolRequest(ContractModel):
    task_id: UUID
    actor: str
    tool_name: str
    operation: str
    arguments: FrozenDict[str, object]


class ToolResult(ContractModel):
    call_id: UUID
    payload: FrozenDict[str, object]
    latency_ms: int
    retries: int
    error_code: str | None


class ToolGateway(Protocol):
    async def execute(self, request: ToolRequest) -> ToolResult: ...
