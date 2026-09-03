"""Unit tests for the upstream MemoBrain adapter (round-16).

The vendored MemoBrain (packages/memory/vendor/memobrain, Apache-2.0 -- see
docs/licenses/memobrain.md) is exercised through the same MemoryAdapter
protocol as the heuristic adapters: episodes become dependency-aware graph
nodes via a gateway call, recall runs the FOLD/FLUSH management once the
graph grows, snapshots round-trip, and every failure degrades to the raw
episode buffer instead of raising (CLAUDE.md 10).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from packages.council.contracts import Seat
from packages.memory.adapter import create_memory_adapter
from packages.memory.contracts import Episode
from packages.memory.graph_memory_adapter import GraphMemoryAdapter
from packages.memory.memobrain_adapter import MemoBrainAdapter
from packages.models.contracts import (
    ModelClass,
    ModelRequest,
    ModelResult,
    SchemaStatus,
)

_PATCH: dict[str, object] = {
    "add_nodes": [
        {
            "tmp_id": "tmp1",
            "kind": "evidence",
            "thought": [{"role": "assistant", "content": "claim X stated"}],
        }
    ],
    "add_edges": [{"src": "1", "dst": "tmp1", "rationale": "supports the task"}],
}

_EMPTY_MANAGE: dict[str, object] = {"flush_ops": [], "fold_ops": []}


class _ScriptedGateway:
    """Answers memory calls deterministically; optionally fails every call."""

    def __init__(self, fail: bool = False) -> None:
        self.requests: list[ModelRequest] = []
        self._fail = fail

    async def invoke(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        if self._fail:
            raise RuntimeError("provider down")
        payload = (
            _PATCH
            if request.output_schema == "MemoBrainPatch"
            else _EMPTY_MANAGE
        )
        return ModelResult(
            call_id=uuid4(),
            payload=payload,
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal("0"),
            latency_ms=0,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


async def _no_sleep(seconds: float) -> None:
    del seconds


def _agent(seat: Seat) -> str:
    return f"{uuid4()}:{seat.value}"


async def test_init_private_memory_seeds_the_task_node() -> None:
    adapter = MemoBrainAdapter(_ScriptedGateway())
    agent = _agent(Seat.THEORY_BUILDER)
    await adapter.init_private_memory(agent, "Is screen time causal?")

    nodes = list(adapter._brains[agent].graph.nodes.values())
    assert len(nodes) == 1
    assert nodes[0].kind == "task"
    assert "Is screen time causal?" in nodes[0].thought


async def test_memorize_routes_construction_through_the_gateway() -> None:
    gateway = _ScriptedGateway()
    adapter = MemoBrainAdapter(gateway)
    agent = _agent(Seat.CAUSAL_SCIENTIST)
    await adapter.init_private_memory(agent, "question")
    await adapter.memorize_episode(
        agent, Episode(kind="PRECOMMITMENT", summary="correlational support only")
    )

    request = gateway.requests[0]
    assert request.output_schema == "MemoBrainPatch"
    assert request.purpose == "memory_construction"
    assert request.model_class is ModelClass.LIGHTWEIGHT
    assert request.actor == agent
    brain = adapter._brains[agent]
    assert len(brain.graph.nodes) == 2  # task + one evidence thought
    assert len(brain.graph.edges) == 1


async def test_recall_under_threshold_skips_the_management_call() -> None:
    gateway = _ScriptedGateway()
    adapter = MemoBrainAdapter(gateway)
    agent = _agent(Seat.THEORY_BUILDER)
    await adapter.init_private_memory(agent, "the task question")

    result = await adapter.recall_private(agent, 2000)

    assert "the task question" in result.text
    assert gateway.requests == []  # no model call for a small graph


async def test_recall_runs_management_once_the_graph_is_large() -> None:
    gateway = _ScriptedGateway()
    adapter = MemoBrainAdapter(gateway)
    agent = _agent(Seat.MEASUREMENT_SCIENTIST)
    await adapter.init_private_memory(agent, "question")
    for index in range(8):
        await adapter.memorize_episode(
            agent, Episode(kind=f"PHASE{index}", summary=f"summary {index}")
        )

    result = await adapter.recall_private(agent, 4000)

    managed = [
        request
        for request in gateway.requests
        if request.output_schema == "MemoBrainFlushAndFold"
    ]
    assert managed  # FOLD/FLUSH management ran
    assert managed[0].purpose == "memory_management"
    assert "Round action" in result.text


async def test_failed_construction_degrades_to_the_raw_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    adapter = MemoBrainAdapter(_ScriptedGateway(fail=True))
    agent = _agent(Seat.ADVERSARY_FALSIFIER)
    await adapter.init_private_memory(agent, "question")
    await adapter.memorize_episode(
        agent, Episode(kind="FAILED_ROUND", summary="raw content survives")
    )

    result = await adapter.recall_private(agent, 2000)

    assert "raw content survives" in result.text


async def test_snapshot_round_trip_preserves_the_graph() -> None:
    gateway = _ScriptedGateway()
    adapter = MemoBrainAdapter(gateway)
    agent = _agent(Seat.BOUNDARY_SCIENTIST)
    await adapter.init_private_memory(agent, "question")
    await adapter.memorize_episode(agent, Episode(kind="ROUND", summary="kept"))

    snapshot = await adapter.save_snapshot(agent)
    restored = MemoBrainAdapter(gateway)
    await restored.load_snapshot(agent, snapshot)

    assert len(restored._brains[agent].graph.nodes) == 2
    assert len(restored._brains[agent].graph.edges) == 1


def test_create_memory_adapter_selects_upstream_when_gateway_present() -> None:
    adapter = create_memory_adapter(_ScriptedGateway())
    assert isinstance(adapter, MemoBrainAdapter)


def test_create_memory_adapter_falls_back_without_gateway() -> None:
    adapter = create_memory_adapter(None)
    assert isinstance(adapter, GraphMemoryAdapter)


def test_memobrain_schemas_are_registered() -> None:
    from packages.models.phase_schemas import PHASE_OUTPUT_JSON_SCHEMAS

    assert "MemoBrainPatch" in PHASE_OUTPUT_JSON_SCHEMAS
    assert "MemoBrainFlushAndFold" in PHASE_OUTPUT_JSON_SCHEMAS
