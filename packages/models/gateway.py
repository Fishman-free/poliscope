from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from packages.models.audit import record_model_call
from packages.models.contracts import ModelGateway, ModelRequest, ModelResult


class AuditedModelGateway:
    """Decorator that persists every model call attempt to the audit log."""

    def __init__(self, inner: ModelGateway, session: AsyncSession) -> None:
        self._inner = inner
        self._session = session

    async def invoke(self, request: ModelRequest) -> ModelResult:
        result = await self._inner.invoke(request)
        await record_model_call(self._session, request, result)
        return result
