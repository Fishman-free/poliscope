"""Round-9 seat retry: an absent scientist is asked again before being lost.

Regression tests for the "科学家缺席补位" feature. ``_collect`` asks each seat
up to ``MAX_SEAT_ATTEMPTS`` times (initial call plus two retries, with a short
linear backoff) with a per-attempt timeout
(``SEAT_ATTEMPT_TIMEOUT_SECONDS``), so a transient provider hiccup -- a
timeout, a recorded failure, or even a raised 5xx/connection error --
re-admits the scientist instead of recording an avoidable absence. A raised
error is contained to that one seat and never fails the whole phase. Two
cases are deliberately NOT retried: the truthful "no provider, cannot
answer" seat (None with no ``last_error``) and a seat cut short by the
whole-pass deadline; ``CouncilCancelled`` always propagates.

Each test injects a small ``attempt_timeout_seconds`` so the retry logic runs
against real event loops without real 120s waits.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

import pytest

from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    _COLLECT_DEADLINE_REASON,
    _DEFAULT_ABSENCE_REASON,
    _SEAT_ATTEMPT_TIMEOUT_REASON,
    MAX_SEAT_ATTEMPTS,
    SEAT_ATTEMPT_TIMEOUT_SECONDS,
    PhaseContext,
    _collect,
)
from packages.epistemo.contracts import CouncilCancelled, TaskPhase


class _FlakyTimeoutDeliberator:
    """A seat that times out on its first call, then answers."""

    def __init__(self, result: Mapping[str, object]) -> None:
        self._result = result
        self.calls: dict[Seat, int] = {}
        self.last_error: str | None = None

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        self.calls[seat] = self.calls.get(seat, 0) + 1
        if self.calls[seat] == 1:
            raise TimeoutError
        return self._result


class _AlwaysTimeoutDeliberator:
    """A seat whose model call never fits inside a single attempt window."""

    last_error: str | None = None

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        raise TimeoutError


class _FlakyNoneDeliberator:
    """A seat that fails once with a recorded error, then answers."""

    def __init__(self, result: Mapping[str, object]) -> None:
        self._result = result
        self.calls: dict[Seat, int] = {}
        self.last_error = "transient provider hiccup"

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        self.calls[seat] = self.calls.get(seat, 0) + 1
        if self.calls[seat] == 1:
            return None
        return self._result


class _SilentNoneDeliberator:
    """A seat that cannot answer and has no failure recorded (e.g. no provider)."""

    def __init__(self) -> None:
        self.calls = 0
        self.last_error: str | None = None

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> None:
        self.calls += 1
        return None


class _FlakyExceptionDeliberator:
    """A seat whose model raises once (e.g. HTTP 5xx), then answers."""

    def __init__(self, result: Mapping[str, object]) -> None:
        self._result = result
        self.calls: dict[Seat, int] = {}

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object]:
        self.calls[seat] = self.calls.get(seat, 0) + 1
        if self.calls[seat] == 1:
            raise RuntimeError("provider 503")
        return self._result


class _AlwaysRaisesDeliberator:
    """A seat whose model raises on every attempt."""

    calls = 0

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object]:
        type(self).calls += 1
        raise RuntimeError("provider down")


class _CancelDeliberator:
    """A seat whose model call is interrupted by a researcher stop."""

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object]:
        raise CouncilCancelled


def _context(deliberator: object) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.CROSS_EXAMINATION,
        seats=tuple(Seat),
        question="test question",
        confirmed_claims=(),
        deliberator=deliberator,  # type: ignore[arg-type]
    )


async def test_a_timeout_is_retried_and_the_seat_then_answers() -> None:
    """First attempt times out, the retry succeeds -- no avoidable absence."""
    deliberator = _FlakyTimeoutDeliberator({"statement": "recovered"})
    outputs, unfilled, absent, reasons, attempts = await _collect(
        _context(deliberator),
        attempt_timeout_seconds=0.05,
        retry_pause_seconds=0.0,
    )
    assert len(outputs) == 7
    assert not absent
    assert not unfilled
    assert all(attempts[seat] == 2 for seat in Seat)
    assert reasons == {}


async def test_two_timeouts_report_the_seat_absent_with_the_honest_reason() -> None:
    """Both attempts time out: absent, reason says "seat attempt timed out"."""
    deliberator = _AlwaysTimeoutDeliberator()
    outputs, unfilled, absent, reasons, attempts = await _collect(
        _context(deliberator),
        attempt_timeout_seconds=0.05,
        retry_pause_seconds=0.0,
    )
    assert not outputs
    assert absent == frozenset(Seat)
    assert all(
        reasons[seat] == _SEAT_ATTEMPT_TIMEOUT_REASON for seat in Seat
    )
    assert all(attempts[seat] == MAX_SEAT_ATTEMPTS for seat in Seat)


async def test_a_recorded_failure_is_retried_and_then_answered() -> None:
    """A real provider error (last_error set) gets a second chance."""
    deliberator = _FlakyNoneDeliberator({"statement": "recovered"})
    outputs, unfilled, absent, reasons, attempts = await _collect(
        _context(deliberator),
        attempt_timeout_seconds=5.0,
        retry_pause_seconds=0.0,
    )
    assert len(outputs) == 7
    assert not absent
    assert all(attempts[seat] == 2 for seat in Seat)


async def test_no_provider_is_not_retried() -> None:
    """A seat returning None with no last_error is the truthful "cannot answer":
    asking again would re-burn the round's budget on the same impossibility."""
    deliberator = _SilentNoneDeliberator()
    outputs, unfilled, absent, reasons, attempts = await _collect(
        _context(deliberator),
        attempt_timeout_seconds=5.0,
    )
    assert not outputs
    assert absent == frozenset(Seat)
    assert deliberator.calls == 7  # one call per seat, no retries
    assert all(attempts[seat] == 1 for seat in Seat)
    assert all(
        reasons[seat] == _DEFAULT_ABSENCE_REASON for seat in Seat
    )


async def test_a_seat_never_reached_records_zero_attempts() -> None:
    """When the whole-pass deadline has already expired, the seat is not asked."""
    deliberator = _AlwaysTimeoutDeliberator()
    outputs, unfilled, absent, reasons, attempts = await _collect(
        _context(deliberator),
        deadline_seconds=0.0,
    )
    assert not outputs
    assert absent == frozenset(Seat)
    assert all(attempts[seat] == 0 for seat in Seat)
    assert all(
        reasons[seat] == _COLLECT_DEADLINE_REASON for seat in Seat
    )


def test_constants_are_sane() -> None:
    """The tunables must permit at least one attempt and a positive timeout."""
    assert MAX_SEAT_ATTEMPTS >= 1
    assert SEAT_ATTEMPT_TIMEOUT_SECONDS > 0


def test_attempt_timeout_is_overridable_by_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators can shrink the per-call window via the environment."""
    from packages.council.rounds.registry import _seat_attempt_timeout_seconds

    monkeypatch.setenv("POLISCOPE_SEAT_ATTEMPT_TIMEOUT_SECONDS", "0.05")
    assert _seat_attempt_timeout_seconds() == 0.05

    # A malformed override falls back to the compiled default rather than
    # crashing the worker at collect time.
    monkeypatch.setenv("POLISCOPE_SEAT_ATTEMPT_TIMEOUT_SECONDS", "nope")
    assert _seat_attempt_timeout_seconds() == SEAT_ATTEMPT_TIMEOUT_SECONDS


async def test_a_raised_provider_error_is_retried_and_then_answered() -> None:
    """A non-timeout exception on attempt 1 must not fail the whole phase:
    the seat is retried and re-admitted when it recovers (gap reduction)."""
    deliberator = _FlakyExceptionDeliberator({"statement": "recovered"})
    outputs, unfilled, absent, _reasons, attempts = await _collect(
        _context(deliberator),
        attempt_timeout_seconds=5.0,
        retry_pause_seconds=0.0,
    )
    assert len(outputs) == 7
    assert not absent
    assert not unfilled
    assert all(attempts[seat] == 2 for seat in Seat)


async def test_a_seat_that_always_raises_is_absent_but_phase_survives() -> None:
    """After MAX_SEAT_ATTEMPTS raised errors the seat is honestly absent with
    the exception type in its reason, rather than the exception escaping and
    failing every other seat in the phase."""
    deliberator = _AlwaysRaisesDeliberator()
    _AlwaysRaisesDeliberator.calls = 0
    outputs, _unfilled, absent, reasons, attempts = await _collect(
        _context(deliberator),
        attempt_timeout_seconds=5.0,
        retry_pause_seconds=0.0,
    )
    assert not outputs
    assert absent == frozenset(Seat)
    assert all(attempts[seat] == MAX_SEAT_ATTEMPTS for seat in Seat)
    assert all(
        reasons[seat].startswith("RuntimeError: provider down") for seat in Seat
    )
    # exactly MAX_SEAT_ATTEMPTS asks per seat -- no infinite retry
    assert _AlwaysRaisesDeliberator.calls == len(Seat) * MAX_SEAT_ATTEMPTS


async def test_council_cancelled_propagates_instead_of_being_retried() -> None:
    """A researcher stop is a redirection: it must escape _collect on the
    first ask, never become a retry or a recorded absence."""
    deliberator = _CancelDeliberator()
    with pytest.raises(CouncilCancelled):
        await _collect(
            _context(deliberator),
            attempt_timeout_seconds=5.0,
            retry_pause_seconds=0.0,
        )
