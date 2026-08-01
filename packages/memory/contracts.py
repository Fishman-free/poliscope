from __future__ import annotations

from typing import Protocol

from packages.kernel.contracts import ContractModel


class Episode(ContractModel):
    kind: str
    summary: str


class RecallResult(ContractModel):
    text: str


class MemoryAdapter(Protocol):
    async def init_private_memory(self, agent_id: str, task: str) -> None: ...
    async def memorize_episode(self, agent_id: str, episode: Episode) -> None: ...
    async def recall_private(self, agent_id: str, token_budget: int) -> RecallResult: ...
    async def save_snapshot(self, agent_id: str) -> dict[str, object]: ...
    async def load_snapshot(self, agent_id: str, snapshot: dict[str, object]) -> None: ...
