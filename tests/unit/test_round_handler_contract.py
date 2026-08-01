from __future__ import annotations

from uuid import uuid4

import pytest

from packages.council.rounds.precommitment import (
    PrecommitmentHandler,
    PrecommitmentNotSealed,
    PrecommitmentOutput,
)
from packages.council.runtime import CouncilRuntime
from packages.council.contracts import Seat


async def test_precommitments_hidden_until_sealed() -> None:
    handler = PrecommitmentHandler()
    await handler.submit(
        Seat.THEORY_BUILDER,
        PrecommitmentOutput(initial_judgment="x", confidence=0.5),
    )
    with pytest.raises(PrecommitmentNotSealed):
        await handler.read_all()


async def test_precommitment_seal_reveals_all() -> None:
    handler = PrecommitmentHandler()
    for seat in Seat:
        await handler.submit(
            seat,
            PrecommitmentOutput(initial_judgment=f"j-{seat.value}", confidence=0.5),
        )
    await handler.seal()
    all_p = await handler.read_all()
    assert len(all_p) == 7


async def test_timeout_records_gap_not_abort() -> None:
    handler = PrecommitmentHandler()
    await handler.submit(
        Seat.THEORY_BUILDER,
        PrecommitmentOutput(initial_judgment="j", confidence=0.5),
    )
    status = handler.task_status()
    assert status == "degraded_running"


def test_suite() -> None:
    import asyncio
    asyncio.run(test_precommitments_hidden_until_sealed())
    asyncio.run(test_precommitment_seal_reveals_all())
    asyncio.run(test_timeout_records_gap_not_abort())
