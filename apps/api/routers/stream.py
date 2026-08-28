"""Server-Sent Events for one research task.

The stream replays from the Scientific Event Ledger rather than from an
in-process queue. That is what makes reconnection lossless: the per-task
``sequence`` column is a total order, so a client that reports the last sequence
it saw receives exactly what it missed and nothing twice. An in-process queue
would drop everything emitted while the client was away, and would also make the
stream wrong as soon as a second API worker existed.

The event id sent on the wire is the sequence rather than the event UUID,
because ``Last-Event-ID`` has to be comparable with ``>`` for resume to work.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from apps.api.dependencies import AppState, get_state
from apps.api.schemas import SSEEvent
from packages.accounts.service import AuthService
from packages.epistemo.contracts import TaskStatus
from packages.evidence.process_stream import ProcessStreamRepository
from packages.evidence.sql_ledger import SqlEventLedger
from packages.kernel.contracts import FrozenDict
from packages.research.repository import ResearchRepository, TaskNotFound

router = APIRouter()

# How long to wait before polling the ledger again when nothing new arrived.
POLL_INTERVAL_SECONDS = 1.0

# Sent when a poll finds nothing, so an idle connection is not closed by a proxy
# and the client can tell "still working" apart from "server gone".
KEEPALIVE_FRAME = ": keep-alive\n\n"

# Reaching one of these ends the stream. Without it the client would poll a
# finished task forever.
TERMINAL_EVENT_TYPES = frozenset(
    {"TASK_COMPLETED", "TASK_COMPLETED_WITH_GAPS", "TASK_FAILED"}
)

# Task statuses after which no further event can ever arrive. The ledger
# stream's normal path ends when a terminal event is fetched, but a reconnect
# whose cursor has already passed that event would otherwise keep-alive a
# finished task forever (one DB poll a second, one held connection, per tab);
# CANCELLED tasks emit no terminal ledger event at all and need the same stop.
TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED.value,
        TaskStatus.COMPLETED_WITH_GAPS.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    }
)

# How many of the newest process rows a (re)connect replays. The process
# trace is token deltas and heartbeats, so a long run stores tens of
# thousands of rows; replaying all of them on every reconnect (a background
# tab thawing, a network hiccup, the EventSource retry after the stream
# closed) saturated the single API event loop and stalled every request --
# one client's reconnect could hang every other user. The live view only
# reads the recent slice (each seat's thinking slice resets on the next
# seat_deliberation, and the tool cards scroll), so the tail is the part
# that matters; a slice longer than this cap may start mid-text, which is an
# accepted cosmetic trade for bounded replay work.
MAX_PROCESS_REPLAY_ROWS = 5000


def parse_last_event_id(raw: str | None) -> int:
    """Interpret the resume header, tolerating a client that sends nonsense.

    A malformed header must not fail the request. Replaying from the beginning
    is wasteful but lossless, whereas rejecting the reconnect would strand a
    client that can no longer catch up by any means.
    """
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


async def _events(
    state: AppState,
    task_id: UUID,
    after_sequence: int,
    request: Request,
) -> AsyncIterator[str]:
    cursor = after_sequence
    while True:
        if await request.is_disconnected():
            return
        # A short lived session per poll rather than one held open for the whole
        # stream: a stream can last for the length of a research task, and a
        # connection idled for that long is a connection the pool cannot reuse.
        async with state.session_factory() as session:
            entries = await SqlEventLedger(session).list_since(task_id, cursor)
            if not entries:
                # Nothing after the cursor. A terminal task can never emit
                # another event -- this is the reconnect-after-terminal case
                # (the cursor already passed the terminal event) -- so end the
                # stream instead of keep-alive polling a finished task forever.
                # A deleted task is equally final.
                try:
                    task = await ResearchRepository(session).get_task(task_id)
                    terminal = task.status in TERMINAL_TASK_STATUSES
                except TaskNotFound:
                    terminal = True
                if terminal:
                    return
        if not entries:
            yield KEEPALIVE_FRAME
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue
        for entry in entries:
            cursor = entry.sequence
            yield SSEEvent(
                event_id=str(entry.sequence),
                task_id=str(task_id),
                kind=entry.event_type,
                workspace_version=entry.sequence,
                payload=FrozenDict(entry.payload),
            ).format_sse()
            if entry.event_type in TERMINAL_EVENT_TYPES:
                return


@router.get("/{task_id}")
async def stream_events(
    task_id: str,
    request: Request,
    token: str = "",
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Stream this task's events, resuming after ``Last-Event-ID`` if given.

    Authentication comes from the ``token`` query parameter, because
    EventSource cannot attach Authorization headers. That puts the bearer
    token in server logs' query strings -- a known, documented trade-off
    (README security section) -- and it is scoped to the caller: someone
    else's task reads as 404 either way.
    """
    state = get_state(request)
    try:
        parsed_task_id = UUID(task_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"malformed task id {task_id}",
        ) from error
    # Checked before the response starts, because once a 200 and the
    # text/event-stream header are on the wire there is no way to report 401
    # or 404.
    async with state.session_factory() as session:
        user = await AuthService(session).user_for_token(token)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        try:
            await ResearchRepository(session).get_task(parsed_task_id, user.id)
        except TaskNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown task {task_id}",
            ) from error
    return StreamingResponse(
        _events(
            state,
            parsed_task_id,
            parse_last_event_id(last_event_id),
            request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Without this a buffering reverse proxy holds the whole response
            # and the stream never reaches the browser.
            "X-Accel-Buffering": "no",
        },
    )


async def _process_events(
    state: AppState,
    task_id: UUID,
    request: Request,
) -> AsyncIterator[str]:
    """Stream the process trace for one task.

    Deliberately *not* replay-guaranteed, unlike the ledger stream above: the
    trace is live noise (token deltas, tool calls), written to
    ``process_stream`` by the worker and read here. A reconnecting client
    re-reads the newest ``MAX_PROCESS_REPLAY_ROWS`` rows and deduplicates by
    ``seq``; the ledger's ``Last-Event-ID`` resume semantics deliberately do
    not apply. The stream ends when the task reaches a terminal status and
    everything already flushed has been sent -- the client is expected to
    close this connection itself on TASK_COMPLETED from the ledger stream.
    """
    # seq starts at 0 and list_since means "> after_seq", so "from the
    # beginning" is -1, not 0 -- a cursor of 0 would silently drop the very
    # first event of every task. The (re)connect replay is then bounded to
    # the newest MAX_PROCESS_REPLAY_ROWS rows: replaying the whole trace of a
    # long task on every reconnect stalled the API for every user.
    cursor = -1
    async with state.session_factory() as session:
        latest = await ProcessStreamRepository(session).latest_seq(task_id)
    if latest > MAX_PROCESS_REPLAY_ROWS:
        cursor = latest - MAX_PROCESS_REPLAY_ROWS
    while True:
        if await request.is_disconnected():
            return
        async with state.session_factory() as session:
            rows = await ProcessStreamRepository(session).list_since(
                task_id, cursor
            )
            status = None
            try:
                task = await ResearchRepository(session).get_task(task_id)
                status = task.status
            except TaskNotFound:
                status = TaskStatus.FAILED.value
        for row in rows:
            cursor = row.seq
            body = json.dumps(
                {"seq": row.seq, "kind": row.kind, "payload": row.payload},
                ensure_ascii=False,
            )
            yield f"event: process\nid: p{row.seq}\ndata: {body}\n\n"
        if status in TERMINAL_TASK_STATUSES:
            return
        yield KEEPALIVE_FRAME
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


@router.get("/{task_id}/process")
async def stream_process(
    task_id: str,
    request: Request,
    token: str = "",
) -> StreamingResponse:
    """Stream this task's process trace (live view), same auth as the ledger
    stream: bearer token in the query string (EventSource cannot set headers),
    scoped to the caller, 404 for someone else's task."""
    state = get_state(request)
    try:
        parsed_task_id = UUID(task_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"malformed task id {task_id}",
        ) from error
    async with state.session_factory() as session:
        user = await AuthService(session).user_for_token(token)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        try:
            await ResearchRepository(session).get_task(parsed_task_id, user.id)
        except TaskNotFound as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown task {task_id}",
            ) from error
    return StreamingResponse(
        _process_events(state, parsed_task_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
