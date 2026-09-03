from __future__ import annotations

from packages.memory.contracts import MemoryAdapter
from packages.memory.graph_memory_adapter import GraphMemoryAdapter
from packages.memory.memobrain_adapter import MemoBrainAdapter
from packages.models.contracts import ModelGateway


def create_memory_adapter(
    gateway: ModelGateway | None = None,
) -> MemoryAdapter:
    """Return the active MemoBrain-compatible adapter.

    Round-16: with a real model gateway this is the **upstream MemoBrain**
    adapter (vendored, Apache-2.0) -- one dependency-aware ``ReasoningGraph``
    per agent with the paper's thought construction and FOLD/FLUSH management,
    every memory-model call routed through Poliscope's gateway.

    Without a gateway (tests, the evaluation harness's ablations) this stays
    the heuristic :class:`GraphMemoryAdapter`, so the baselines that
    deliberately model the *absence* of upstream executive memory keep their
    exact semantics.
    """
    if gateway is not None:
        return MemoBrainAdapter(gateway)
    return GraphMemoryAdapter()
