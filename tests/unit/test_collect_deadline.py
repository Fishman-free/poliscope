"""The seat-collection deadline: one phase must not hold the worker hostage.

Regression for the round-5 "交叉质询卡死" report: with seven seats run
serially and each model call allowed its own 240s stream + 300s fallback, a
slow provider could pin a single phase for ~63 minutes with no budget check
inside it. ``_collect`` now bounds the whole pass (default 900s) and reports
a seat that cannot answer in time as absent with an honest reason.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from time import monotonic
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    _COLLECT_DEADLINE_REASON,
    PhaseContext,
    _collect,
)
from packages.epistemo.contracts import TaskPhase


class _SlowDeliberator:
    """Every seat answers, but only after ``delay`` seconds."""

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self.last_error: str | None = None
        self.calls = 0

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        self.calls += 1
        await asyncio.sleep(self._delay)
        return {"statement": f"{seat.value} answered"}


def _context(deliberator: object) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.CROSS_EXAMINATION,
        seats=tuple(Seat),
        question="test question",
        confirmed_claims=(),
        deliberator=deliberator,  # type: ignore[arg-type]
    )


async def test_a_slow_seat_does_not_stall_the_phase() -> None:
    """A seat slower than the deadline is absent; the phase returns in time."""
    deliberator = _SlowDeliberator(delay=0.3)
    started = monotonic()
    outputs, unfilled, absent, reasons, attempts = await _collect(
        _context(deliberator),
        deadline_seconds=0.1,
    )
    elapsed = monotonic() - started

    # The pass returned well before seven seats x 0.3s would have finished.
    assert elapsed < 1.0
    # The first seat was cancelled mid-call by the deadline; every seat is
    # absent with the honest reason (no "provider down" fabrication).
    assert len(outputs) == 0
    assert len(absent) == 7
    assert all(
        reasons[seat] == _COLLECT_DEADLINE_REASON for seat in absent
    )
    assert any(f"CROSS_EXAMINATION:{seat.value}" in unfilled for seat in absent)
    # The retry budget still bounds the extra work: no seat is asked more than
    # MAX_SEAT_ATTEMPTS times, and the whole pass returns fast regardless.
    from packages.council.rounds.registry import MAX_SEAT_ATTEMPTS

    assert all(attempts[seat] <= MAX_SEAT_ATTEMPTS for seat in Seat)


async def test_a_fast_phase_runs_all_seats() -> None:
    """Well within the deadline, every seat answers as before."""
    deliberator = _SlowDeliberator(delay=0)
    outputs, unfilled, absent, reasons, attempts = await _collect(
        _context(deliberator),
        deadline_seconds=5.0,
    )
    assert len(outputs) == 7
    assert not absent
    assert not unfilled
    assert deliberator.calls == 7
    assert all(v == 1 for v in attempts.values())


async def test_deadline_cancels_a_mid_call_seat() -> None:
    """A seat whose call outlives the deadline is cancelled and reported."""
    # Each seat takes 0.15s; deadline 0.35s means seats 1-2 finish, the third
    # starts but is cancelled mid-call, and the rest are skipped.
    deliberator = _SlowDeliberator(delay=0.15)
    started = monotonic()
    outputs, unfilled, absent, reasons, attempts = await _collect(
        _context(deliberator),
        deadline_seconds=0.35,
    )
    elapsed = monotonic() - started

    assert len(outputs) == 2
    assert len(absent) == 5
    # The seats that answered were never retried (they succeeded first time);
    # no seat exceeds the retry budget, whatever the exact deadline boundary was.
    assert all(attempts[seat] == 1 for seat in outputs)
    assert all(attempts[seat] <= 2 for seat in Seat)
    assert all(
        reasons[seat] == _COLLECT_DEADLINE_REASON for seat in absent
    )
    # Cancellation happened at the deadline, not after five more seat calls.
    assert elapsed < 1.0


async def test_deadline_seconds_default_is_large_but_present() -> None:
    """The default keeps the old behaviour for healthy providers while still
    bounding the phase (the regression guard itself)."""
    from packages.council.rounds.registry import _COLLECT_DEADLINE_SECONDS

    assert _COLLECT_DEADLINE_SECONDS >= 60.0
