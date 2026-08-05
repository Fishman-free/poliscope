from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from packages.models.audit import record_model_call
from packages.models.contracts import (
    ModelGateway,
    ModelRequest,
    ModelResult,
    StreamEvent,
)


class AuditedModelGateway:
    """Decorator that persists every model call attempt to the audit log."""

    def __init__(self, inner: ModelGateway, session: AsyncSession) -> None:
        self._inner = inner
        self._session = session

    async def invoke(self, request: ModelRequest) -> ModelResult:
        result = await self._inner.invoke(request)
        await record_model_call(self._session, request, result)
        return result

    async def stream_invoke(
        self,
        request: ModelRequest,
        on_event: Callable[[StreamEvent], Awaitable[None]],
    ) -> ModelResult:
        """Forward to the inner gateway when it streams; else plain invoke.

        The audit row is written exactly as in ``invoke`` -- the streamed
        call is still one audited model call, cost and latency included.
        """
        streaming = getattr(self._inner, "stream_invoke", None)
        if streaming is None:
            return await self.invoke(request)
        result: ModelResult = await streaming(request, on_event)
        await record_model_call(self._session, request, result)
        return result
