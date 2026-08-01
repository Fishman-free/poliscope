from __future__ import annotations

from packages.memory.contracts import (
    Episode,
    RecallResult,
)


class InMemoryMemoryAdapter:
    """Stand-in MemoBrain adapter with strict per-agent isolation."""

    def __init__(self) -> None:
        self._private: dict[str, list[Episode]] = {}
        self._snapshots: dict[str, list[Episode]] = {}

    async def init_private_memory(self, agent_id: str, task: str) -> None:
        self._private[agent_id] = [Episode(kind="task", summary=task)]

    async def memorize_episode(self, agent_id: str, episode: Episode) -> None:
        if agent_id not in self._private:
            raise KeyError(f"agent {agent_id!r} not initialized")
        self._private[agent_id].append(episode)

    async def recall_private(self, agent_id: str, token_budget: int) -> RecallResult:
        episodes = self._private.get(agent_id, [])
        text = " ".join(e.summary for e in episodes)[:token_budget]
        return RecallResult(text=text)

    async def save_snapshot(self, agent_id: str) -> dict[str, object]:
        episodes = self._private.get(agent_id, [])
        return {"episodes": [e.model_dump() for e in episodes]}

    async def load_snapshot(self, agent_id: str, snapshot: dict[str, object]) -> None:
        raw_episodes = snapshot.get("episodes", [])
        if not isinstance(raw_episodes, (list, tuple)):
            # A malformed snapshot must not restore a seat to an empty memory
            # that looks like a legitimately fresh one; CLAUDE.md 10 wants the
            # resume to be verifiable.
            raise ValueError(f"snapshot for {agent_id!r} has no episode list")
        self._private[agent_id] = [
            Episode.model_validate(item) for item in raw_episodes
        ]
