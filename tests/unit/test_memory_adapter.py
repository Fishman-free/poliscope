from __future__ import annotations

import asyncio

import pytest

from packages.memory.adapter import create_memory_adapter
from packages.memory.contracts import Episode

THEORY_ID = "theory_builder"
CAUSAL_ID = "causal_scientist"


async def test_private_memory_is_isolated() -> None:
    adapter = create_memory_adapter()
    await adapter.init_private_memory(THEORY_ID, "task-theory")
    await adapter.init_private_memory(CAUSAL_ID, "task-causal")
    await adapter.memorize_episode(
        THEORY_ID, Episode(kind="evidence", summary="private-a")
    )
    theory_recall = await adapter.recall_private(THEORY_ID, 100)
    causal_recall = await adapter.recall_private(CAUSAL_ID, 100)
    assert "private-a" in theory_recall.text
    assert "private-a" not in causal_recall.text


async def test_snapshot_round_trip() -> None:
    adapter = create_memory_adapter()
    await adapter.init_private_memory(THEORY_ID, "task")
    await adapter.memorize_episode(
        THEORY_ID, Episode(kind="finding", summary="finding-1")
    )
    snapshot = await adapter.save_snapshot(THEORY_ID)
    await adapter.memorize_episode(
        THEORY_ID, Episode(kind="finding", summary="finding-2")
    )
    await adapter.load_snapshot(THEORY_ID, snapshot)
    recall = await adapter.recall_private(THEORY_ID, 200)
    assert "finding-1" in recall.text
    assert "finding-2" not in recall.text


def test_suite() -> None:
    asyncio.run(test_private_memory_is_isolated())
    asyncio.run(test_snapshot_round_trip())
