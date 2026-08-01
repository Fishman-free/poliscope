from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from packages.models.audit import record_tool_call
from packages.tools.contracts import ToolGateway, ToolRequest, ToolResult


class AuditedToolGateway:
    """Decorator that persists every tool call attempt to the audit log."""

    def __init__(self, inner: ToolGateway, session: AsyncSession) -> None:
        self._inner = inner
        self._session = session

    async def execute(self, request: ToolRequest) -> ToolResult:
        result = await self._inner.execute(request)
        await record_tool_call(self._session, request, result)
        return result
