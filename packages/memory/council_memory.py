"""Private process memory for the seven seats, scoped per task.

CLAUDE.md 6 puts long-range process memory in MemoBrain, and CLAUDE.md 3 requires
each seat to hold its own private state. Both are satisfied by a single rule
enforced here: an agent id is ``{task_id}:{seat}`` and nothing reads another
seat's id. Two seats on the same task cannot see each other's recall, and the
same seat on two tasks does not carry one task's memory into the other.

Process memory is not evidence. Nothing written here reaches the Evidence Graph;
that only happens through the ledger and the projector, per CLAUDE.md 5.3 and 6.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from packages.council.contracts import Seat
from packages.memory.contracts import Episode, MemoryAdapter

# How much recall a seat gets in one prompt. Small on purpose: recall competes
# with the evidence projection for the same context, and CLAUDE.md 6 wants the
# scientific skeleton preserved rather than the transcript replayed.
DEFAULT_RECALL_BUDGET = 2000


def agent_id(task_id: UUID, seat: Seat) -> str:
    """The one place a seat's private memory key is constructed."""
    return f"{task_id}:{seat.value}"


class CouncilMemory:
    """Seat-scoped façade over a :class:`MemoryAdapter`."""

    def __init__(
        self,
        adapter: MemoryAdapter,
        task_id: UUID,
        recall_budget: int = DEFAULT_RECALL_BUDGET,
    ) -> None:
        self._adapter = adapter
        self._task_id = task_id
        self._budget = recall_budget

    async def open(self, seats: tuple[Seat, ...], question: str) -> None:
        """Give every seat a private memory seeded with the task."""
        for seat in seats:
            await self._adapter.init_private_memory(
                agent_id(self._task_id, seat), question
            )

    async def remember(self, seat: Seat, kind: str, summary: str) -> None:
        await self._adapter.memorize_episode(
            agent_id(self._task_id, seat), Episode(kind=kind, summary=summary)
        )

    async def recall(self, seats: tuple[Seat, ...]) -> Mapping[Seat, str]:
        """Read each seat's own recall. A seat never receives another's."""
        return {
            seat: (
                await self._adapter.recall_private(
                    agent_id(self._task_id, seat), self._budget
                )
            ).text
            for seat in seats
        }

    async def snapshot(self, seats: tuple[Seat, ...]) -> dict[str, object]:
        """Capture every seat's memory so a paused task can resume.

        CLAUDE.md 10 requires snapshot, pause, resume, and replay for long tasks.
        A run that could not restore its process memory would resume with seven
        seats that had forgotten the debate.
        """
        return {
            seat.value: await self._adapter.save_snapshot(
                agent_id(self._task_id, seat)
            )
            for seat in seats
        }

    async def restore(self, snapshot: Mapping[str, object]) -> None:
        for name, state in snapshot.items():
            if not isinstance(state, dict):
                continue
            await self._adapter.load_snapshot(
                agent_id(self._task_id, Seat(name)), state
            )


__all__ = ["DEFAULT_RECALL_BUDGET", "CouncilMemory", "agent_id"]
