"""Collective executive memory: the council's shared cognitive control layer.

This is EpistemoBrain's contribution over upstream MemoBrain (design doc 1/6/9):
MemoBrain compresses *one* agent's tool-call history; the council needs a shared
memory that tracks *structured cognitive events* -- who proposed what, who
challenged whom, which disputes are unresolved, and which blindspots nobody has
touched -- so it can answer "what should we investigate next" rather than only
"what did we just do".

It is a *materialised view over the ledger*, not a second source of truth:
every entry here is reduced from an admitted scientific event, and the index can
be rebuilt from the ledger at any time (idempotent, replay-safe). It never
writes the Evidence Graph -- that stays the projector's exclusive job
(CLAUDE.md 5.3) -- and it never leaks a seat's private reasoning into a
collective view (CLAUDE.md 3/11: only the structured action, the evidence, and
the challenge reach the collective).

The output is a :class:`CollectiveView`: a plain-text, role-agnostic summary of
the cognitive frontier, consumed by ``perspective_recall`` to build each seat's
role-specific projection, plus a deterministic :func:`division_of_labor` that
turns a ranked blindspot into a seven-seat assignment (design doc 6's
"FormCoalition", not "SelectAgent").
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from packages.council.contracts import ALL_SEATS, Seat

# Event types this index reduces. Only structured cognitive events are indexed;
# process noise (PHASE_STARTED, MODEL_REASONING_CAPTURED, tool traces) is
# deliberately ignored -- the collective holds the *scientific* record.
CLAIM_EVENT = "Claim"
CHALLENGE_EVENT = "CHALLENGE_RAISED"
EVIDENCE_EVENT = "EVIDENCE_PUBLISHED"
BLINDSPOT_EVENT = "Blindspot"
DISSENT_EVENT = "DissentCertificate"
CONSENSUS_EVENT = "CONSENSUS_DRAFTED"
CAPSULE_EVENT = "DebateCapsule"


@dataclass(frozen=True, slots=True)
class CollectiveEvent:
    """One reduced cognitive event the collective memory indexes."""

    event_type: str
    payload: dict[str, object]


@dataclass
class CollectiveView:
    """The cognitive frontier, rendered for recall.

    ``summary`` is a plain-language account of what the council holds; ``claims``
    are the still-active claims with their support/challenge tallies; ``blindspots``
    are the open gaps; ``unresolved`` names the disputes that are not settled.
    """

    summary: str
    active_claims: tuple[str, ...] = ()
    blindspots: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    dissents: tuple[str, ...] = ()


class CollectiveMemory:
    """Shared index over the ledger's structured cognitive events."""

    def __init__(self) -> None:
        self._claims: dict[str, dict[str, object]] = {}
        self._challenges: list[dict[str, object]] = []
        self._published: list[dict[str, object]] = []
        self._blindspots: dict[str, dict[str, object]] = {}
        self._dissents: list[dict[str, object]] = []
        self._consensus: str | None = None
        self._investigated: set[str] = set()

    # -- ingestion --------------------------------------------------------

    def absorb(self, events: object) -> None:
        """Reduce an iterable of (event_type, payload) pairs into the index.

        ``events`` may be a tuple of :class:`CollectiveEvent`, a tuple of
        ``(str, dict)`` pairs, or a tuple of objects exposing ``event_type`` /
        ``payload`` (the registry's ``EmittedEvent``). Idempotent by shape: a
        replayed event that has already been indexed simply updates the same
        slot, matching the ledger's own replay semantics.
        """
        for item in _as_events(events):
            self._absorb_one(item.event_type, item.payload)

    def _absorb_one(self, event_type: str, payload: dict[str, object]) -> None:
        if event_type == CLAIM_EVENT:
            claim_id = str(payload.get("node_id") or payload.get("claim_id") or "")
            if not claim_id:
                return
            statement = str(payload.get("statement", ""))
            claim_type = str(payload.get("claim_type", ""))
            status = str(payload.get("status", "proposed"))
            self._claims[claim_id] = {
                "statement": statement,
                "claim_type": claim_type,
                "status": status,
            }
        elif event_type == CHALLENGE_EVENT:
            self._challenges.append(
                {
                    "challenger": payload.get("seat"),
                    "target": payload.get("claim_id"),
                    "statement": payload.get("statement"),
                }
            )
        elif event_type == EVIDENCE_EVENT:
            self._published.append(
                {
                    "seat": payload.get("seat"),
                    "items": payload.get("items", ()),
                }
            )
        elif event_type == BLINDSPOT_EVENT:
            blindspot_id = str(payload.get("node_id") or "")
            if not blindspot_id:
                return
            statement = str(payload.get("statement", ""))
            kind = str(payload.get("kind", ""))
            # A bounty-generated blindspot is "under investigation" once it has
            # been assigned; a nominated one is still open.
            self._blindspots[blindspot_id] = {
                "statement": statement,
                "kind": kind,
            }
        elif event_type == DISSENT_EVENT:
            self._dissents.append(
                {
                    "seat": payload.get("seat"),
                    "statement": payload.get("statement"),
                    "target": payload.get("target_id"),
                }
            )
        elif event_type == CONSENSUS_EVENT:
            text = payload.get("conditional_consensus")
            if isinstance(text, str) and text:
                self._consensus = text
        elif event_type == CAPSULE_EVENT:
            # A DebateCapsule names the unresolved conflicts explicitly; keep
            # them so the collective view can say which disputes are open.
            conflicts = payload.get("unresolved_conflicts")
            if isinstance(conflicts, (list, tuple)):
                for conflict in conflicts:
                    if isinstance(conflict, str):
                        self._challenges.append(
                            {
                                "challenger": "capsule",
                                "target": None,
                                "statement": conflict,
                            }
                        )

    # -- queries ----------------------------------------------------------

    def view(self) -> CollectiveView:
        """Render the cognitive frontier as a CollectiveView."""
        active = [
            f"{data['statement']}"
            for data in self._claims.values()
            if data.get("status") not in ("withdrawn", "quarantined")
        ]
        blindspots = [
            f"{data['statement']}"
            for data in self._blindspots.values()
        ]
        unresolved = [
            f"{item['statement']}"
            for item in self._challenges
            if item.get("statement")
        ]
        dissents = [
            f"{item['statement']}"
            for item in self._dissents
            if item.get("statement")
        ]
        parts: list[str] = []
        if active:
            parts.append("活跃主张：" + "；".join(active))
        if blindspots:
            parts.append("待调查盲点：" + "；".join(blindspots))
        if unresolved:
            parts.append("未解决争议：" + "；".join(unresolved))
        if dissents:
            parts.append("保留的异议：" + "；".join(dissents))
        if self._consensus:
            parts.append("已形成的条件化共识：" + self._consensus)
        return CollectiveView(
            summary=" ".join(parts),
            active_claims=tuple(active),
            blindspots=tuple(blindspots),
            unresolved=tuple(unresolved),
            dissents=tuple(dissents),
        )

    def has_consensus(self) -> bool:
        return self._consensus is not None

    def evidence_snapshot(self) -> list[dict[str, object]]:
        """The cognitive frontier as a type-tagged evidence snapshot.

        This is the input to ``perspective_recall`` (design doc 7): each seat
        ranks the *same* frontier by its own ``evidence_sort_weights``, so the
        causal scientist sees causal claims first while the measurement
        scientist sees measurement claims first -- one shared fact base, seven
        distinct cognitive cuts. Claim type is the ``type`` key, matching the
        weight vocabulary in packages/memory/projection.py.
        """
        items: list[dict[str, object]] = []
        for claim_id, data in self._claims.items():
            claim_type = str(data.get("claim_type", "correlational"))
            items.append(
                {
                    "type": claim_type,
                    "id": claim_id,
                    "statement": data.get("statement", ""),
                }
            )
        for blindspot_id, data in self._blindspots.items():
            items.append(
                {
                    "type": "blindspot",
                    "id": blindspot_id,
                    "statement": data.get("statement", ""),
                }
            )
        for dissent in self._dissents:
            items.append(
                {
                    "type": "contradiction",
                    "id": dissent.get("target"),
                    "statement": dissent.get("statement", ""),
                }
            )
        return items

    # -- epistemic routing (design doc 6) --------------------------------

    def division_of_labor(
        self, blindspots: tuple[dict[str, object], ...]
    ) -> tuple[dict[str, object], ...]:
        """Assign every seat a complementary task for one blindspot.

        Design doc 6 replaces "SelectAgent" with "FormCoalition": a blindspot is
        not handed to a single best scientist; the whole council investigates it
        from seven complementary angles. The task text is deterministic -- each
        seat's angle is fixed by its role -- so a replay yields the same table.
        """
        assignments: list[dict[str, object]] = []
        for blindspot in blindspots:
            statement = str(blindspot.get("statement", ""))
            blindspot_id = str(blindspot.get("blindspot_id", ""))
            for seat in _ordered_seats():
                assignments.append(
                    {
                        "blindspot_id": blindspot_id,
                        "statement": statement,
                        "seat": seat.value,
                        "task": _SEAT_TASK_TEMPLATES[seat].format(statement=statement),
                    }
                )
        return tuple(assignments)


_SEAT_TASK_TEMPLATES: dict[Seat, str] = {
    Seat.THEORY_BUILDER: "判断哪些理论依赖这一盲点的前提，并指出可区分预测",
    Seat.CAUSAL_SCIENTIST: "分析这一盲点若成立，会把因果效应推向哪个方向",
    Seat.MEASUREMENT_SCIENTIST: "比较不同研究对相关构念的操作化，定位测量差异",
    Seat.REPLICATION_SCIENTIST: "核对该盲点涉及的证据是否来自独立样本与独立设计",
    Seat.BOUNDARY_SCIENTIST: "检查这一盲点在不同人群、国家、时期间是否不同",
    Seat.ADVERSARY_FALSIFIER: "寻找即使这一盲点不成立、原结论依然成立的证据",
    Seat.EVIDENCE_AUDITOR: "核验支持与反驳这一盲点的原文、DOI 与数据独立性",
}


def _ordered_seats() -> tuple[Seat, ...]:
    return tuple(sorted(ALL_SEATS, key=lambda seat: seat.value))


def _as_events(events: object) -> tuple[CollectiveEvent, ...]:
    """Normalise the several event shapes callers may pass into CollectiveEvent."""
    if events is None or not isinstance(events, Iterable):
        return ()
    out: list[CollectiveEvent] = []
    for item in events:
        if isinstance(item, CollectiveEvent):
            out.append(item)
            continue
        if isinstance(item, tuple) and len(item) == 2:
            out.append(
                CollectiveEvent(event_type=str(item[0]), payload=dict(item[1]))
            )
            continue
        event_type = getattr(item, "event_type", None)
        payload = getattr(item, "payload", None)
        if event_type is not None and payload is not None:
            out.append(
                CollectiveEvent(event_type=str(event_type), payload=dict(payload))
            )
    return tuple(out)


__all__ = [
    "CLAIM_EVENT",
    "CHALLENGE_EVENT",
    "EVIDENCE_EVENT",
    "BLINDSPOT_EVENT",
    "DISSENT_EVENT",
    "CONSENSUS_EVENT",
    "CAPSULE_EVENT",
    "CollectiveEvent",
    "CollectiveMemory",
    "CollectiveView",
]
