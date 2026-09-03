"""缺席原因诚实化（CLAUDE.md 7）：SEAT_UNAVAILABLE 必须报告真实失败原因。

Regression test for a real production incident: a researcher configured a
wrong model base_url (https://platform.deepseek.com, the console portal,
instead of the API endpoint), every seat's model call failed instantly, and
the absence events blamed "no model provider is connected to the Model
Gateway" -- true only when no provider is configured at all, which was not
this case. A researcher who cannot tell "no provider" from "provider
rejected us" cannot fix their configuration, so the reason now travels from
GatewayDeliberator.last_error into the event payload.
"""

from __future__ import annotations

from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.deliberation import GatewayDeliberator
from packages.council.rounds.registry import (
    MAX_SEAT_ATTEMPTS,
    SEAT_UNAVAILABLE,
    PhaseContext,
    _collect,
    _unavailable_events,
)
from packages.epistemo.contracts import TaskPhase


class _BrokenGateway:
    """A model provider that fails every call, like a wrong base_url would."""

    async def invoke(self, request: object) -> object:
        raise RuntimeError(
            "connection error: DNS lookup failed for platform.deepseek.com"
        )


async def test_deliberator_records_why_the_seat_could_not_answer() -> None:
    deliberator = GatewayDeliberator(_BrokenGateway())  # type: ignore[arg-type]
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.PRECOMMITMENT,
        seats=(Seat.CAUSAL_SCIENTIST,),
        question="question",
        confirmed_claims=(),
        deliberator=deliberator,
        carried={},
    )
    result = await deliberator.deliberate(
        Seat.CAUSAL_SCIENTIST, TaskPhase.PRECOMMITMENT, context
    )
    assert result is None
    assert "platform.deepseek.com" in (deliberator.last_error or "")


class _FailingDeliberator:
    """Stands in for a deliberator that failed, with the failure recorded."""

    last_error = "connection error: DNS lookup failed"

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> None:
        return None


class _SilentDeliberator:
    """A deliberator that failed without recording why (e.g. no provider)."""

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> None:
        return None


def _context(deliberator: object) -> PhaseContext:
    return PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.PRECOMMITMENT,
        seats=(Seat.CAUSAL_SCIENTIST,),
        question="question",
        confirmed_claims=(),
        deliberator=deliberator,  # type: ignore[arg-type]
        carried={},
    )


async def test_unavailable_event_reports_the_real_reason() -> None:
    context = _context(_FailingDeliberator())
    _, _, absent, reasons, attempts = await _collect(context)
    assert absent == frozenset({Seat.CAUSAL_SCIENTIST})
    assert "DNS lookup failed" in reasons[Seat.CAUSAL_SCIENTIST]
    events = _unavailable_events(context, absent, reasons, attempts)
    assert events[0].event_type == SEAT_UNAVAILABLE
    assert events[0].payload["reason"] == "connection error: DNS lookup failed"
    # The seat was asked MAX_SEAT_ATTEMPTS times (retry budget exhausted).
    assert attempts[Seat.CAUSAL_SCIENTIST] == MAX_SEAT_ATTEMPTS
    # The attempts count rides along on the event so the researcher can tell a
    # single failure from a retried-and-still-down one.
    assert events[0].payload["attempts"] == MAX_SEAT_ATTEMPTS


async def test_unavailable_event_defaults_when_no_reason_was_recorded() -> None:
    context = _context(_SilentDeliberator())
    _, _, absent, reasons, attempts = await _collect(context)
    events = _unavailable_events(context, absent, reasons)
    assert str(events[0].payload["reason"]).startswith(
        "no model provider is connected"
    )
    # No provider configured: the truthful "cannot answer" case is not retried.
    assert attempts[Seat.CAUSAL_SCIENTIST] == 1
