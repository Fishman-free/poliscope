from __future__ import annotations

from packages.memory.contracts import MemoryAdapter
from packages.memory.graph_memory_adapter import GraphMemoryAdapter


def create_memory_adapter() -> MemoryAdapter:
    """Return the active MemoBrain-compatible adapter.

    A dependency-aware :class:`GraphMemoryAdapter` (one ReasoningGraph per
    agent, with Flush/Fold/Recall), not the flat in-memory stand-in. The
    ``InMemoryMemoryAdapter`` remains for the evaluation harness's
    ``SharedLinearMemoryAdapter`` baseline, which deliberately models the
    *absence* of per-seat graph memory.
    """
    return GraphMemoryAdapter()
