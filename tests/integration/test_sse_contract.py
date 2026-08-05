"""Tests for the Server-Sent Events stream.

The earlier version of this file constructed an SSEEvent and asserted the
constructor had stored its arguments, then filtered a list it had built in the
test and asserted the filter worked. Neither touched the endpoint. Resume is the
property that actually matters here, and it can only be demonstrated against the
real stream backed by the real ledger.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routers.stream import parse_last_event_id
from apps.api.schemas import SSEEvent
from packages.evidence.sql_ledger import SqlEventLedger
from tests.factories import make_research_contract

TERMINAL = "TASK_COMPLETED"


async def _create_task(client: httpx.AsyncClient) -> UUID:
    contract = make_research_contract().model_dump(mode="json")
    response = await client.post("/api/tasks", json=contract)
    assert response.status_code == 201, response.text
    return UUID(response.json()["task_id"])


async def _seed_events(
    session: AsyncSession,
    task_id: UUID,
    kinds: tuple[str, ...],
) -> None:
    """Append events and commit, so the streaming session can read them."""
    ledger = SqlEventLedger(session)
    for index, kind in enumerate(kinds, start=1):
        await ledger.append(task_id, kind, {"n": index}, f"seed-{index}")
    await session.commit()


def _kinds(frames: list[dict[str, str]]) -> list[str]:
    """The event kind now lives in the JSON body, not on an `event:` line."""
    return [str(json.loads(frame["data"])["kind"]) for frame in frames]


def _frames(body: str) -> list[dict[str, str]]:
    """Parse a full SSE body into frames, skipping comment lines."""
    frames: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in body.split("\n"):
        line = line.rstrip("\r")
        if line == "":
            if current:
                frames.append(current)
                current = {}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        current[field.strip()] = value.lstrip()
    if current:
        frames.append(current)
    return frames


def test_sse_frame_uses_the_id_data_wire_format() -> None:
    """The browser EventSource ignores a frame that omits these fields."""
    formatted = SSEEvent(
        event_id="7",
        task_id=str(uuid4()),
        kind="SEAT_UPDATE",
        workspace_version=7,
        payload={"seat": "causal_scientist"},
    ).format_sse()
    assert formatted.startswith("id: 7\n")
    assert formatted.endswith("\n\n")
    data_line = next(
        line for line in formatted.split("\n") if line.startswith("data:")
    )
    assert json.loads(data_line[len("data:") :])["payload"] == {
        "seat": "causal_scientist"
    }


def test_no_frame_carries_a_custom_event_type() -> None:
    """A typed frame reaches only listeners registered for that exact type.

    Putting the kind on the ``event:`` line made every client enumerate the
    backend's event vocabulary, and silently drop anything it had not been
    taught -- which is how the audit trail came to show nine of fifty-three
    events with no indication that forty-four were missing. The kind travels in
    the body instead, where a client cannot fail to receive it.
    """
    formatted = SSEEvent(
        event_id="7",
        task_id=str(uuid4()),
        kind="SEAT_UNAVAILABLE",
        workspace_version=7,
        payload={},
    ).format_sse()
    assert "event:" not in formatted
    data_line = next(
        line for line in formatted.split("\n") if line.startswith("data:")
    )
    assert json.loads(data_line[len("data:") :])["kind"] == "SEAT_UNAVAILABLE"


def test_a_nested_payload_serialises_instead_of_killing_the_stream() -> None:
    """The payload is frozen recursively, so a nested object is not a plain dict.

    ``json.dumps(dict(payload))`` raised on the inner FrozenDict from inside the
    response generator, which the browser experienced as the connection simply
    ending. The audit trail stopped at event 38 of 53 -- the first one with a
    nested payload -- and reported nothing wrong.
    """
    formatted = SSEEvent(
        event_id="38",
        task_id=str(uuid4()),
        kind="BOUNTY_ASSIGNED",
        workspace_version=38,
        payload={
            "assignments": [
                {"blindspot_id": "b-1", "priority_rank": 1, "score": "0.78"}
            ]
        },
    ).format_sse()

    data_line = next(
        line for line in formatted.split("\n") if line.startswith("data:")
    )
    assert json.loads(data_line[len("data:") :])["payload"] == {
        "assignments": [
            {"blindspot_id": "b-1", "priority_rank": 1, "score": "0.78"}
        ]
    }


async def test_the_stream_delivers_every_event_including_nested_payloads(
    api_client: httpx.AsyncClient,
    app_session: AsyncSession,
    account: dict[str, Any],
) -> None:
    """End to end: a nested payload mid-stream must not truncate the rest.

    Asserted over the real endpoint rather than the formatter, because the
    failure was that the *generator* died -- the formatter was only where the
    exception came from.
    """
    task_id = await _create_task(api_client)
    ledger = SqlEventLedger(app_session)
    await ledger.append(task_id, "A", {"n": 1}, "seed-1")
    await ledger.append(
        task_id,
        "BOUNTY_ASSIGNED",
        {"assignments": [{"blindspot_id": "b-1", "priority_rank": 1}]},
        "seed-2",
    )
    await ledger.append(task_id, TERMINAL, {"n": 3}, "seed-3")
    await app_session.commit()

    response = await api_client.get(
        f"/api/stream/{task_id}",
        params={"token": account["token"]},
    )

    frames = _frames(response.text)
    assert [frame["id"] for frame in frames] == ["1", "2", "3"]
    assert _kinds(frames) == ["A", "BOUNTY_ASSIGNED", TERMINAL]


@pytest.mark.parametrize(
    ("header", "expected"),
    (
        (None, 0),
        ("0", 0),
        ("41", 41),
        ("-3", 0),
        ("not-a-number", 0),
        ("", 0),
    ),
)
def test_last_event_id_header_is_parsed_defensively(
    header: str | None,
    expected: int,
) -> None:
    """A malformed header replays from the start rather than failing the request.

    Rejecting the reconnect would strand a client that has no other way to catch
    up, so the safe direction is to send too much rather than too little.
    """
    assert parse_last_event_id(header) == expected


async def test_stream_delivers_events_in_sequence_order(
    api_client: httpx.AsyncClient,
    app_session: AsyncSession,
    account: dict[str, Any],
) -> None:
    task_id = await _create_task(api_client)
    await _seed_events(app_session, task_id, ("A", "B", TERMINAL))
    response = await api_client.get(
        f"/api/stream/{task_id}",
        params={"token": account["token"]},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response.text)
    assert _kinds(frames) == ["A", "B", TERMINAL]
    assert [frame["id"] for frame in frames] == ["1", "2", "3"]


async def test_stream_resumes_after_last_event_id_without_replaying(
    api_client: httpx.AsyncClient,
    app_session: AsyncSession,
    account: dict[str, Any],
) -> None:
    """This is the property the whole ledger-backed design exists for.

    A client that saw event 2 and reconnected must receive 3 onward and nothing
    it has already rendered.
    """
    task_id = await _create_task(api_client)
    await _seed_events(app_session, task_id, ("A", "B", "C", "D", TERMINAL))
    response = await api_client.get(
        f"/api/stream/{task_id}",
        params={"token": account["token"]},
        headers={"Last-Event-ID": "2"},
    )
    frames = _frames(response.text)
    assert [frame["id"] for frame in frames] == ["3", "4", "5"]
    assert _kinds(frames) == ["C", "D", TERMINAL]


async def test_stream_survives_a_reconnect_that_reports_a_stale_id(
    api_client: httpx.AsyncClient,
    app_session: AsyncSession,
    account: dict[str, Any],
) -> None:
    """Replaying from zero is lossless, which is why a bad id is not an error."""
    task_id = await _create_task(api_client)
    await _seed_events(app_session, task_id, ("A", TERMINAL))
    response = await api_client.get(
        f"/api/stream/{task_id}",
        params={"token": account["token"]},
        headers={"Last-Event-ID": "garbage"},
    )
    assert [frame["id"] for frame in _frames(response.text)] == ["1", "2"]


async def test_stream_for_an_unknown_task_returns_404_not_an_empty_stream(
    api_client: httpx.AsyncClient,
    account: dict[str, Any],
) -> None:
    """A 200 with no events would look identical to a task that has not started."""
    response = await api_client.get(
        f"/api/stream/{uuid4()}",
        params={"token": account["token"]},
    )
    assert response.status_code == 404


async def test_stream_for_a_malformed_task_id_returns_422(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.get("/api/stream/not-a-uuid")
    assert response.status_code == 422
