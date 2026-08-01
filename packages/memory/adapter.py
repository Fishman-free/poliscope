from __future__ import annotations

from packages.memory.contracts import MemoryAdapter
from packages.memory.in_memory_adapter import InMemoryMemoryAdapter


def create_memory_adapter() -> MemoryAdapter:
    """Return the active MemoBrain-compatible adapter."""
    return InMemoryMemoryAdapter()
