from __future__ import annotations

import pytest

from packages.council.contracts import Seat
from packages.council.rounds.precommitment import (
    PrecommitmentHandler,
    PrecommitmentNotSealed,
    PrecommitmentOutput,
)
from packages.council.runtime import CouncilRuntime


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


async def test_every_seat_can_precommit_before_the_seal() -> None:
    """The runtime used to seal on the first submission, blocking the other six.

    Sealing per submission meant seat two onwards raised PrecommitmentNotSealed
    and the round could never assemble a full council -- the exact opposite of
    the independent precommitment CLAUDE.md 4 requires.
    """
    runtime = CouncilRuntime()
    for seat in Seat:
        await runtime.submit_precommitment(
            seat,
            PrecommitmentOutput(initial_judgment=f"j-{seat.value}", confidence=0.5),
        )
    await runtime.seal_precommitments()

    assert len(await runtime.read_all_precommitments()) == 7


async def test_the_runtime_refuses_a_read_before_the_seal() -> None:
    """Reading early would let a seat anchor on another's judgment."""
    runtime = CouncilRuntime()
    await runtime.submit_precommitment(
        Seat.THEORY_BUILDER,
        PrecommitmentOutput(initial_judgment="j", confidence=0.5),
    )

    with pytest.raises(PrecommitmentNotSealed):
        await runtime.read_all_precommitments()


async def test_the_runtime_refuses_a_submission_after_the_seal() -> None:
    """A judgment revised after the seal is no longer an independent one."""
    runtime = CouncilRuntime()
    await runtime.seal_precommitments()

    with pytest.raises(PrecommitmentNotSealed):
        await runtime.submit_precommitment(
            Seat.CAUSAL_SCIENTIST,
            PrecommitmentOutput(initial_judgment="late", confidence=0.9),
        )
