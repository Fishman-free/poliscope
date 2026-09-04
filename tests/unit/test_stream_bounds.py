"""Unit tests for the SSE stream termination and replay bounds.

Two production incidents a single user could reproduce on their own:

- after a task finished, the ledger stream kept reconnecting clients on
  keep-alive polls forever (the terminal event had already passed the
  cursor), holding one connection and one 1 Hz DB poll per tab until the
  browser closed it; and
- every (re)connect replayed the ENTIRE process trace -- tens of thousands
  of token-delta rows -- which saturated the single API event loop and
  stalled every request for every user, while the same replay froze the
  browser's renderer.

These tests lock the two fixes in ``apps/api/routers/stream.py``: terminate
a finished task's ledger stream on reconnect, and bound the process replay
to the newest ``MAX_PROCESS_REPLAY_ROWS`` rows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import pytest

import apps.api.routers.stream as stream_module
from apps.api.routers.stream import (
    KEEPALIVE_FRAME,
    MAX_PROCESS_REPLAY_ROWS,
    _events,
    _process_events,
)
from packages.evidence.process_stream import ProcessStreamRepository
from packages.research.repository import TaskNotFound


class _FakeRequest:
    """Stands in for a FastAPI Request: reports disconnect after N polls."""

    def __init__(self, disconnect_after: int) -> None:
        self._polls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._polls += 1
        return self._polls > self._disconnect_after


class _FakeSessionFactory:
    """Callable returning an async context manager that yields a session."""

    def __call__(self) -> _FakeSessionContext:
        return _FakeSessionContext()


class _FakeSessionContext:
    async def __aenter__(self) -> SimpleNamespace:
        return SimpleNamespace()

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def _state() -> object:
    # Fakes stand in for the real AppState / Request at the call sites below,
    # which carry type: ignore[arg-type] for exactly that reason.
    return SimpleNamespace(session_factory=_FakeSessionFactory())


async def _drain(generator: AsyncIterator[str]) -> list[str]:
    return [frame async for frame in generator]


def _row(seq: int, kind: str = "model_token", text: str = "t") -> SimpleNamespace:
    return SimpleNamespace(seq=seq, kind=kind, payload={"text": text})


# --- ledger stream ---------------------------------------------------------


async def test_ledger_stream_ends_immediately_after_reconnect_on_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cursor already passed the terminal event: no keep-alive, just end.

    This is the reconnect-after-terminal zombie: the stream used to poll a
    finished task every second until the tab closed.
    """

    class Ledger:
        def __init__(self, session: object) -> None:
            pass

        async def list_since(
            self, task_id: object, after_sequence: int
        ) -> list[object]:
            return []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            return SimpleNamespace(status="FAILED")

    monkeypatch.setattr(stream_module, "SqlEventLedger", Ledger)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)

    frames = await _drain(
        _events(_state(), uuid4(), 42, _FakeRequest(5))  # type: ignore[arg-type]
    )
    assert frames == []


async def test_ledger_stream_ends_when_task_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CANCELLED tasks emit no terminal ledger event; the status must end it."""

    class Ledger:
        def __init__(self, session: object) -> None:
            pass

        async def list_since(
            self, task_id: object, after_sequence: int
        ) -> list[object]:
            return []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            return SimpleNamespace(status="CANCELLED")

    monkeypatch.setattr(stream_module, "SqlEventLedger", Ledger)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)

    frames = await _drain(
        _events(_state(), uuid4(), 0, _FakeRequest(5))  # type: ignore[arg-type]
    )
    assert frames == []


async def test_ledger_stream_ends_when_task_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Ledger:
        def __init__(self, session: object) -> None:
            pass

        async def list_since(
            self, task_id: object, after_sequence: int
        ) -> list[object]:
            return []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            raise TaskNotFound("deleted")

    monkeypatch.setattr(stream_module, "SqlEventLedger", Ledger)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)

    frames = await _drain(
        _events(_state(), uuid4(), 0, _FakeRequest(5))  # type: ignore[arg-type]
    )
    assert frames == []


async def test_ledger_stream_keeps_alive_while_task_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Ledger:
        def __init__(self, session: object) -> None:
            pass

        async def list_since(
            self, task_id: object, after_sequence: int
        ) -> list[object]:
            return []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            return SimpleNamespace(status="RUNNING")

    monkeypatch.setattr(stream_module, "SqlEventLedger", Ledger)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)
    monkeypatch.setattr(stream_module, "POLL_INTERVAL_SECONDS", 0)

    frames = await _drain(
        _events(_state(), uuid4(), 0, _FakeRequest(2))  # type: ignore[arg-type]
    )
    assert frames == [KEEPALIVE_FRAME, KEEPALIVE_FRAME]


async def test_ledger_stream_still_delivers_terminal_event_and_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Ledger:
        def __init__(self, session: object) -> None:
            pass

        async def list_since(
            self, task_id: object, after_sequence: int
        ) -> list[object]:
            if after_sequence >= 1:
                return []
            return [
                SimpleNamespace(
                    sequence=1, event_type="TASK_COMPLETED", payload={}
                )
            ]

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            return SimpleNamespace(status="COMPLETED")

    monkeypatch.setattr(stream_module, "SqlEventLedger", Ledger)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)

    frames = await _drain(
        _events(_state(), uuid4(), 0, _FakeRequest(5))  # type: ignore[arg-type]
    )
    assert len(frames) == 1
    assert "TASK_COMPLETED" in frames[0]


# --- process stream --------------------------------------------------------


async def test_process_stream_caps_reconnect_replay_to_recent_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_after: list[int] = []

    class ProcRepo:
        def __init__(self, session: object) -> None:
            pass

        async def latest_seq(self, task_id: object) -> int:
            return 12_000

        async def list_structural_before(
            self, task_id: object, before_seq: int, limit: int = 3000
        ) -> list[object]:
            return []

        async def list_since(
            self, task_id: object, after_seq: int, limit: int = 500
        ) -> list[object]:
            first_after.append(after_seq)
            return []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            return SimpleNamespace(status="RUNNING")

    monkeypatch.setattr(stream_module, "ProcessStreamRepository", ProcRepo)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)
    monkeypatch.setattr(stream_module, "POLL_INTERVAL_SECONDS", 0)

    frames = await _drain(
        _process_events(
            _state(),  # type: ignore[arg-type]
            uuid4(),
            _FakeRequest(disconnect_after=1),  # type: ignore[arg-type]
        )
    )
    # The replay starts at the newest MAX_PROCESS_REPLAY_ROWS rows, not at -1.
    assert first_after[0] == 12_000 - MAX_PROCESS_REPLAY_ROWS
    assert frames == [KEEPALIVE_FRAME]


async def test_process_stream_replays_from_start_when_trace_is_small(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_after: list[int] = []

    class ProcRepo:
        def __init__(self, session: object) -> None:
            pass

        async def latest_seq(self, task_id: object) -> int:
            return 100

        async def list_since(
            self, task_id: object, after_seq: int, limit: int = 500
        ) -> list[object]:
            first_after.append(after_seq)
            return []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            return SimpleNamespace(status="RUNNING")

    monkeypatch.setattr(stream_module, "ProcessStreamRepository", ProcRepo)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)
    monkeypatch.setattr(stream_module, "POLL_INTERVAL_SECONDS", 0)

    frames = await _drain(
        _process_events(
            _state(),  # type: ignore[arg-type]
            uuid4(),
            _FakeRequest(disconnect_after=1),  # type: ignore[arg-type]
        )
    )
    assert first_after[0] == -1
    assert frames == [KEEPALIVE_FRAME]


async def test_process_stream_ends_after_replay_for_terminal_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProcRepo:
        def __init__(self, session: object) -> None:
            pass

        async def latest_seq(self, task_id: object) -> int:
            return 2

        async def list_since(
            self, task_id: object, after_seq: int, limit: int = 500
        ) -> list[object]:
            if after_seq >= 2:
                return []
            return [_row(1, "model_done"), _row(2, "tool_result")]

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            return SimpleNamespace(status="COMPLETED")

    monkeypatch.setattr(stream_module, "ProcessStreamRepository", ProcRepo)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)

    frames = await _drain(
        _process_events(
            _state(),  # type: ignore[arg-type]
            uuid4(),
            _FakeRequest(disconnect_after=5),  # type: ignore[arg-type]
        )
    )
    assert len(frames) == 2
    assert all("event: process" in frame for frame in frames)
    assert "id: p1" in frames[0]
    assert "id: p2" in frames[1]


# --- repository ------------------------------------------------------------


class _ScalarSession:
    def __init__(self, value: int | None) -> None:
        self._value = value

    async def scalar(self, statement: object) -> int | None:
        return self._value


async def test_latest_seq_returns_minus_one_when_trace_empty() -> None:
    repo = ProcessStreamRepository(_ScalarSession(None))  # type: ignore[arg-type]
    assert await repo.latest_seq(uuid4()) == -1
    assert await repo.next_seq(uuid4()) == 0


async def test_latest_seq_returns_newest_row() -> None:
    repo = ProcessStreamRepository(_ScalarSession(41))  # type: ignore[arg-type]
    assert await repo.latest_seq(uuid4()) == 41
    assert await repo.next_seq(uuid4()) == 42


async def test_process_stream_replays_structural_anchors_outside_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anchors older than the heavy tail window are replayed first.

    Reproduces the production bug: two seats keep streaming token deltas past
    5000 rows, the early-finishing seats' seat_deliberation/model_done anchors
    scroll out of the tail, and after reconnect their cards never open.
    """
    calls: list[tuple[str, int]] = []

    class ProcRepo:
        def __init__(self, session: object) -> None:
            pass

        async def latest_seq(self, task_id: object) -> int:
            return 12_000

        async def list_structural_before(
            self, task_id: object, before_seq: int, limit: int = 3000
        ) -> list[object]:
            calls.append(("anchors", before_seq))
            return [
                _row(50, "seat_deliberation"),
                _row(60, "model_done"),
            ]

        async def list_since(
            self, task_id: object, after_seq: int, limit: int = 500
        ) -> list[object]:
            calls.append(("tail", after_seq))
            if after_seq >= 12_000:
                return []
            return [_row(11_999, "model_token"), _row(12_000, "model_token")]

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            return SimpleNamespace(status="RUNNING")

    monkeypatch.setattr(stream_module, "ProcessStreamRepository", ProcRepo)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)
    monkeypatch.setattr(stream_module, "POLL_INTERVAL_SECONDS", 0)

    frames = await _drain(
        _process_events(
            _state(),  # type: ignore[arg-type]
            uuid4(),
            _FakeRequest(disconnect_after=1),  # type: ignore[arg-type]
        )
    )
    # Anchor query uses the same tail cursor, then the tail streams after it.
    assert calls[0] == ("anchors", 12_000 - MAX_PROCESS_REPLAY_ROWS)
    assert ("tail", 12_000 - MAX_PROCESS_REPLAY_ROWS) in calls
    process_frames = [frame for frame in frames if "event: process" in frame]
    # Anchors come first, in ascending seq, then the heavy tail.
    assert "id: p50" in process_frames[0]
    assert "id: p60" in process_frames[1]
    assert "id: p11999" in process_frames[2]
    assert "id: p12000" in process_frames[3]


async def test_process_stream_small_trace_skips_anchor_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the whole trace fits the tail window there is nothing to anchor."""

    class ProcRepo:
        def __init__(self, session: object) -> None:
            pass

        async def latest_seq(self, task_id: object) -> int:
            return 42

        async def list_structural_before(self, *args: object) -> list[object]:
            raise AssertionError("must not query anchors when tail covers all")

        async def list_since(
            self, task_id: object, after_seq: int, limit: int = 500
        ) -> list[object]:
            return []

    class Repo:
        def __init__(self, session: object) -> None:
            pass

        async def get_task(self, task_id: object) -> SimpleNamespace:
            return SimpleNamespace(status="RUNNING")

    monkeypatch.setattr(stream_module, "ProcessStreamRepository", ProcRepo)
    monkeypatch.setattr(stream_module, "ResearchRepository", Repo)
    monkeypatch.setattr(stream_module, "POLL_INTERVAL_SECONDS", 0)

    frames = await _drain(
        _process_events(
            _state(),  # type: ignore[arg-type]
            uuid4(),
            _FakeRequest(disconnect_after=1),  # type: ignore[arg-type]
        )
    )
    assert frames == [KEEPALIVE_FRAME]
