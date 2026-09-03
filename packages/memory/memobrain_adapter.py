"""Upstream MemoBrain integration: each seat's private executive memory.

The vendored MemoBrain code (``packages/memory/vendor/memobrain/``, upstream
https://github.com/qhjqhj00/MemoBrain @ 82f16e1, Apache-2.0 -- see
``docs/licenses/memobrain.md``) implements the paper's two processes:

- **memory construction**: each completed episode is abstracted into a
  dependency-aware ``thought`` (subtask/evidence) linked into a per-agent
  reasoning graph; and
- **memory management**: when the graph grows, a FOLD/FLUSH pass collapses
  resolved sub-trajectories and compresses superseded steps into the compact
  backbone the seat actually recalls.

This adapter plugs those two processes into Poliscope's council in two ways:

1. One vendored ``MemoBrain`` instance per seat (agent id ``task:seat``), so
   the seven private states stay private (CLAUDE.md 3) while every seat gains
   the upstream dependency graph instead of the old heuristic one.
2. Every memory-model call is routed through Poliscope's :class:`ModelGateway`
   (CLAUDE.md 8: no vendor SDK calls outside the gateway), using the
   ``MemoBrainPatch`` / ``MemoBrainFlushAndFold`` JSON schemas registered in
   ``packages/models/phase_schemas.py``. The vendored code is untouched: the
   subclass below only replaces ``_create_completion``, which is the single
   choke point all upstream model calls flow through.

Process memory is auxiliary: a failing memory-model call must never fail a
seat or a round (CLAUDE.md 10), so every operation degrades to the raw
episode buffer instead of raising.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from uuid import UUID

from packages.memory.contracts import Episode, MemoryAdapter, RecallResult
from packages.memory.vendor.memobrain.memobrain import MemoBrain
from packages.memory.vendor.memobrain.prompts import (
    FLUSH_AND_FOLD_PROMPT,
    MEMORY_SYS_PROMPT,
)
from packages.models.contracts import (
    ModelClass,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResult,
)

logger = logging.getLogger(__name__)

# How many active graph nodes a seat must have before the FOLD/FLUSH
# management call (an extra LLM round-trip) is worth running. Below that the
# plain graph print is already a compact recall; the paper triggers management
# when the context approaches its budget, and this is the council's analogue.
MANAGE_THRESHOLD = 8

# Character budget cap applied to whatever recall text the upstream brain
# returns, before the caller's own budget slice. The council's recall budget
# is small on purpose (CLAUDE.md 6); this cap is a second line of defence
# against an unexpectedly large organised context.
MAX_RECALL_CHARS = 4000

# How many failed-construction episodes are kept raw per agent. Bounded so a
# persistently broken memory model degrades to a fixed-size log, never to an
# unbounded buffer (same discipline as the process stream).
MAX_FALLBACK_ENTRIES = 20


class _GatewayMemoBrain(MemoBrain):
    """Vendored MemoBrain whose model calls go through Poliscope's gateway.

    The upstream class talks to an OpenAI-compatible endpoint directly; this
    subclass overrides ``_create_completion`` -- the one method every upstream
    model call funnels through -- and answers from the gateway instead. The
    upstream retry/parse logic around it is reused as-is.
    """

    def __init__(
        self,
        gateway: ModelGateway,
        task_id: UUID,
        actor: str,
    ) -> None:
        self._gateway = gateway
        self._task_id = task_id
        self._actor = actor
        # Dummy credentials: the parent only stores an AsyncOpenAI client that
        # this subclass never uses (every call is intercepted below).
        super().__init__(
            api_key="unused", base_url="http://unused", model_name="unused"
        )

    async def _create_completion(
        self, messages: list[dict[str, object]], stream: bool = False
    ) -> _FakeCompletion:
        del stream  # the gateway owns streaming; upstream parses the final text
        system = str(messages[0].get("content", "")) if messages else ""
        if system == MEMORY_SYS_PROMPT:
            purpose = "memory_construction"
            output_schema = "MemoBrainPatch"
        elif system == FLUSH_AND_FOLD_PROMPT:
            purpose = "memory_management"
            output_schema = "MemoBrainFlushAndFold"
        else:
            raise RuntimeError(f"unknown MemoBrain prompt: {system[:60]!r}")
        request = ModelRequest(
            task_id=self._task_id,
            actor=self._actor,
            purpose=purpose,
            model_class=ModelClass.LIGHTWEIGHT,
            messages=tuple(
                ModelMessage(role=str(m["role"]), content=str(m["content"]))
                for m in messages
            ),
            output_schema=output_schema,
            evidence_refs=(),
        )
        result: ModelResult = await self._gateway.invoke(request)
        # The gateway returns the schema-validated payload; hand it back to
        # the upstream parser as the JSON text it expects. The payload is
        # frozen (FrozenDict at every level), so convert recursively before
        # serialising.
        content = json.dumps(_to_plain(result.payload), ensure_ascii=False)
        return _FakeCompletion(content)


def _to_plain(value: object) -> object:
    """Recursively thaw a ContractModel payload into plain JSON-safe data."""
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    return value


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = type("Message", (), {})()
        self.message.content = content


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


def _task_id_from_agent_id(agent_id: str) -> UUID:
    """The task id from a ``{task_id}:{seat}`` agent key (council_memory.py)."""
    return UUID(agent_id.split(":", 1)[0])


class MemoBrainAdapter(MemoryAdapter):
    """Per-seat upstream MemoBrain memory, degraded gracefully on failure."""

    def __init__(
        self,
        gateway: ModelGateway,
        manage_threshold: int = MANAGE_THRESHOLD,
    ) -> None:
        self._gateway = gateway
        self._manage_threshold = manage_threshold
        self._brains: dict[str, _GatewayMemoBrain] = {}
        # Raw episodes, kept as the honest fallback when a memory-model call
        # fails: a broken memory model must degrade to a plain log, never to a
        # failed seat (CLAUDE.md 10).
        self._fallback: dict[str, list[str]] = {}

    async def init_private_memory(self, agent_id: str, task: str) -> None:
        brain = _GatewayMemoBrain(
            self._gateway, _task_id_from_agent_id(agent_id), agent_id
        )
        brain.init_memory(task)  # type: ignore[no-untyped-call]
        self._brains[agent_id] = brain
        self._fallback[agent_id] = []

    async def memorize_episode(self, agent_id: str, episode: Episode) -> None:
        brain = self._brains.get(agent_id)
        fallback = self._fallback.setdefault(agent_id, [])
        if brain is None:
            # Memory initialised lazily elsewhere in unusual flows; the raw
            # episode is still worth keeping for recall.
            fallback.append(f"[{episode.kind}] {episode.summary}")
            del fallback[:-MAX_FALLBACK_ENTRIES]
            return
        pair = [
            {"role": "user", "content": f"Round action: {episode.kind}"},
            {"role": "assistant", "content": episode.summary},
        ]
        try:
            await brain.memorize(pair)
        except Exception:
            logger.warning(
                "MemoBrain memorize failed for %s (%s); kept raw episode",
                agent_id,
                episode.kind,
                exc_info=True,
            )
            # Only failures land here: on success the graph owns the episode
            # and duplicating it into recall would double-report it.
            fallback.append(f"[{episode.kind}] {episode.summary}")
            del fallback[:-MAX_FALLBACK_ENTRIES]

    async def recall_private(self, agent_id: str, token_budget: int) -> RecallResult:
        brain = self._brains.get(agent_id)
        fallback = self._fallback.get(agent_id, [])
        if brain is None:
            return RecallResult(text=self._fallback_text(agent_id, token_budget))
        active = sum(
            1 for node in brain.graph.nodes.values() if node.active is True
        )
        try:
            if active >= self._manage_threshold:
                organized = await brain.recall()  # type: ignore[no-untyped-call]
                text = _messages_to_text(organized)
            else:
                text = brain.graph.pretty_print()
        except Exception:
            logger.warning(
                "MemoBrain recall failed for %s; degraded to raw episodes",
                agent_id,
                exc_info=True,
            )
            text = self._fallback_text(agent_id, MAX_RECALL_CHARS)
        if fallback:
            # Episodes whose construction failed are still the seat's own
            # reasoning; recall must not silently lose them (CLAUDE.md 10).
            text = f"{text}\n[未能写入记忆图的原始片段]\n" + "\n".join(fallback)
        return RecallResult(text=text[:token_budget])

    def _fallback_text(self, agent_id: str, budget: int) -> str:
        entries = self._fallback.get(agent_id, [])
        return "\n".join(entries)[:budget]

    async def save_snapshot(self, agent_id: str) -> dict[str, object]:
        brain = self._brains.get(agent_id)
        if brain is None:
            return {}
        return {
            "graph": brain.graph.to_dict(),
            "messages": list(brain.messages),
            "fallback": list(self._fallback.get(agent_id, [])),
        }

    async def load_snapshot(
        self, agent_id: str, snapshot: dict[str, object]
    ) -> None:
        graph = snapshot.get("graph")
        if not isinstance(graph, dict):
            return
        brain = _GatewayMemoBrain(
            self._gateway, _task_id_from_agent_id(agent_id), agent_id
        )
        brain.load_dict_memory(graph)
        messages = snapshot.get("messages")
        if isinstance(messages, list):
            brain.messages = [dict(m) for m in messages if isinstance(m, dict)]
        fallback = snapshot.get("fallback")
        self._fallback[agent_id] = (
            [str(entry) for entry in fallback]
            if isinstance(fallback, list)
            else []
        )
        self._brains[agent_id] = brain


def _messages_to_text(messages: list[dict[str, object]]) -> str:
    """Render the upstream organised context (a message list) as recall text."""
    lines: list[str] = []
    for message in messages:
        content = str(message.get("content", ""))
        if not content:
            continue
        lines.append(content)
    return "\n".join(lines)


__all__ = ["MANAGE_THRESHOLD", "MemoBrainAdapter"]
