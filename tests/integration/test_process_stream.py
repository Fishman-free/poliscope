"""Tests for the process trace (live view) data layer.

Unit tests must not touch a database (conftest discipline), and the writer's
per-task sequencing is exactly the kind of behaviour that needs the real
Postgres -- ``(task_id, seq)`` uniqueness, FK enforcement, and cross-connection
visibility of the writer's commits. These run against the seeded task like
the ledger tests do, with the writer on the ``app_sessions`` factory because
it must own its own transaction boundary.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.evidence.process_stream import (
    ProcessStreamRepository,
    ProcessStreamWriter,
)


async def _commit_seeded_task(app_session: AsyncSession) -> None:
    """The seeded-task fixture only flushes; the writer runs on its own
    connection, so the parent task row must be committed for its FK to
    resolve (and for the writer's writes to be visible here afterwards)."""
    await app_session.commit()


async def test_writer_flush_assigns_per_task_sequences(
    app_session: AsyncSession,
    app_sessions: async_sessionmaker[AsyncSession],
    seeded_task: UUID,
) -> None:
    await _commit_seeded_task(app_session)
    writer = ProcessStreamWriter(app_sessions, seeded_task, flush_at=2)
    writer.emit("model_token", {"text": "a"})
    writer.emit("model_token", {"text": "b"})
    writer.emit("tool_call", {"query": "q"})
    await writer.flush()

    repo = ProcessStreamRepository(app_session)
    # seq starts at 0 and list_since means "> after_seq": from the beginning
    # is -1, and a resume after seq 0 must see only seq 2's event.
    rows = await repo.list_since(seeded_task, -1)
    assert [(row.seq, row.kind) for row in rows] == [
        (0, "model_token"),
        (1, "model_token"),
        (2, "tool_call"),
    ]
    await app_session.rollback()


async def test_writer_close_flushes_remaining_buffer(
    app_session: AsyncSession,
    app_sessions: async_sessionmaker[AsyncSession],
    seeded_task: UUID,
) -> None:
    await _commit_seeded_task(app_session)
    writer = ProcessStreamWriter(app_sessions, seeded_task, flush_at=100)
    writer.emit("model_reasoning", {"text": "thinking…"})
    await writer.close()

    repo = ProcessStreamRepository(app_session)
    rows = await repo.list_since(seeded_task, -1)
    assert [(row.seq, row.kind) for row in rows] == [(0, "model_reasoning")]
    await app_session.rollback()


async def test_list_since_resumes_after_given_seq(
    app_session: AsyncSession,
    app_sessions: async_sessionmaker[AsyncSession],
    seeded_task: UUID,
) -> None:
    await _commit_seeded_task(app_session)
    writer = ProcessStreamWriter(app_sessions, seeded_task)
    for index in range(3):
        writer.emit("model_token", {"text": str(index)})
    await writer.close()

    repo = ProcessStreamRepository(app_session)
    assert [row.seq for row in await repo.list_since(seeded_task, -1)] == [0, 1, 2]
    assert [row.seq for row in await repo.list_since(seeded_task, 1)] == [2]
    assert await repo.list_since(UUID(int=0), -1) == []
    await app_session.rollback()
