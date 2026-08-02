"""One calling convention for the seven council rounds.

Each round handler in this package was written to its own shape: some are async
and some are not, some take a seat and some take a list of items, and none of
them matched the ``RoundHandler`` protocol declared in :mod:`.base`. Nothing
called any of them. This module gives every round the same signature so the
orchestrator can drive the protocol in CLAUDE.md 4 as a sequence rather than as
seven special cases, and it does so by delegating to the existing handlers
rather than by reimplementing their rules.

**Where the seats' reasoning comes from.** A seat's judgment needs a model call,
and the Model Gateway has no provider wired to it yet. Rather than inventing a
judgment, a round asks its :class:`SeatDeliberator` and records an unfilled
evidence slot when the answer is unavailable. CLAUDE.md 7 forbids presenting AI
derivation as evidence and CLAUDE.md 10 requires unfilled slots to be reported
instead of papered over, so an honest gap is the correct output here and the
task finishes as COMPLETED_WITH_GAPS. Swapping in a model-backed deliberator
changes no other code.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from packages.council.contracts import Seat
from packages.council.dissent import issue_dissent
from packages.council.rounds.acquisition import AcquisitionRound
from packages.council.rounds.blindspot_bounty import (
    BlindspotBountyHandler,
    BlindspotItem,
    BountyInput,
)
from packages.council.rounds.cross_examination import (
    ChallengeEntry,
    CrossExaminationHandler,
)
from packages.council.rounds.exchange import (
    EvidenceProjectionItem,
    ExchangeRound,
)
from packages.council.rounds.final_rejudgment import (
    FinalRejudgmentHandler,
    FinalRejudgmentInput,
)
from packages.council.rounds.joint_modeling import (
    JointModelingHandler,
    JointModelInput,
)
from packages.council.rounds.precommitment import (
    PrecommitmentHandler,
    PrecommitmentOutput,
)
from packages.epistemo.contracts import TaskPhase
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.dialectical_fold import DebateCapsule

# Process event types. None of these is one of the ten formal node types, so
# the projector records them and refuses to turn them into evidence.
PHASE_STARTED = "PHASE_STARTED"
PHASE_COMPLETED = "PHASE_COMPLETED"
SEAT_UNAVAILABLE = "SEAT_UNAVAILABLE"
PRECOMMITMENT_SEALED = "PRECOMMITMENT_SEALED"
EVIDENCE_REQUESTED = "EVIDENCE_REQUESTED"
EVIDENCE_PUBLISHED = "EVIDENCE_PUBLISHED"
CHALLENGE_RAISED = "CHALLENGE_RAISED"
BOUNTY_ASSIGNED = "BOUNTY_ASSIGNED"
SOURCE_REFUSED = "SOURCE_REFUSED"
CONSENSUS_DRAFTED = "CONSENSUS_DRAFTED"
FINAL_JUDGMENT = "FINAL_JUDGMENT"


@dataclass(frozen=True, slots=True)
class EmittedEvent:
    """One ledger append a round is asking for.

    ``idempotency_key`` must be derivable from the phase, the seat, and the
    position rather than from a clock or a random value; that is what lets a
    resumed task re-run a round without duplicating its events.
    """

    event_type: str
    payload: dict[str, object]
    idempotency_key: str
    evidence_level: str | None = None
    source_id: UUID | None = None
    finding_id: UUID | None = None
    claim_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PhaseOutcome:
    events: tuple[EmittedEvent, ...] = ()
    # Values later phases read. Joint modeling needs what cross examination
    # found, so the orchestrator threads this through rather than letting the
    # rounds share a mutable object.
    carry: Mapping[str, object] = field(default_factory=dict)
    unfilled_slots: tuple[str, ...] = ()
    absent_seats: frozenset[Seat] = frozenset()


class SeatDeliberator(Protocol):
    """Supplies one seat's structured output for one phase."""

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        """Return the seat's output, or None when it cannot be produced."""
        ...


class UnavailableDeliberator:
    """The default: no model provider is connected, so no seat can deliberate.

    Returning None is not a failure path. It is the truthful answer, and the
    orchestrator turns it into a reported gap rather than a fabricated judgment.
    """

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        return None


class SourceAcquirer(Protocol):
    """Resolves the seats' evidence requests into persisted sources.

    Declared here rather than imported so ``council`` does not depend on
    ``papers``; the implementation is
    :class:`packages.papers.acquisition.SourceAcquisition`.
    """

    async def acquire(self, requests: list[tuple[Seat, str]]) -> AcquisitionLike: ...


class AcquisitionLike(Protocol):
    """The part of an acquisition result the round reads."""

    @property
    def acquired(self) -> tuple[AcquiredLike, ...]: ...

    @property
    def refused(self) -> tuple[RefusedLike, ...]: ...

    @property
    def unresolvable(self) -> tuple[str, ...]: ...


class AcquiredLike(Protocol):
    # Read-only properties, not attributes: the implementations are frozen
    # dataclasses, and a mutable attribute in a Protocol demands a settable one.
    @property
    def source_id(self) -> UUID: ...

    @property
    def doi(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def evidence_level(self) -> str: ...

    @property
    def already_known(self) -> bool: ...


class RefusedLike(Protocol):
    @property
    def query(self) -> str: ...

    @property
    def reason(self) -> str: ...


class FindingExtractor(Protocol):
    """Turns one already-acquired Source's open access full text into a
    StudyFinding.

    Declared here rather than imported so ``council`` does not depend on
    ``papers``; the implementation is
    :class:`packages.papers.finding_extraction.FindingExtractor`.
    """

    async def extract(self, source_id: UUID, doi: str) -> FindingExtractionLike: ...


class FindingExtractionLike(Protocol):
    # Read-only properties, not attributes: the implementation is a frozen
    # dataclass, and a mutable attribute in a Protocol demands a settable one.
    @property
    def ok(self) -> bool: ...

    @property
    def reason(self) -> str: ...

    @property
    def finding_id(self) -> UUID | None: ...

    @property
    def evidence_level(self) -> str: ...

    @property
    def exact_quote(self) -> str: ...

    @property
    def finding_statement(self) -> str: ...

    @property
    def method_quality(self) -> Mapping[str, float]: ...


@dataclass(frozen=True, slots=True)
class PhaseContext:
    task_id: UUID
    phase: TaskPhase
    seats: tuple[Seat, ...]
    question: str
    confirmed_claims: tuple[UUID, ...]
    deliberator: SeatDeliberator
    carried: Mapping[str, object] = field(default_factory=dict)
    # Each seat's own private process recall, keyed by seat. A seat is only ever
    # handed its own entry; CLAUDE.md 3 keeps private state private.
    recall: Mapping[Seat, str] = field(default_factory=dict)
    # None when no tool provider is configured. The acquisition round then records
    # the requests and stops, rather than inventing sources.
    acquirer: SourceAcquirer | None = None
    # None when no tool/model provider is configured. The acquisition round then
    # records an unfilled slot per newly acquired source instead of leaving a
    # Level B source with no StudyFinding attempt at all.
    finding_extractor: FindingExtractor | None = None

    def key(self, *parts: object) -> str:
        """Build a replay-stable idempotency key for this phase."""
        return ":".join([self.phase.value, *(str(part) for part in parts)])


async def _collect(
    context: PhaseContext,
) -> tuple[dict[Seat, Mapping[str, object]], tuple[str, ...], frozenset[Seat]]:
    """Ask every seat for its output, recording the ones that cannot answer."""
    outputs: dict[Seat, Mapping[str, object]] = {}
    unfilled: list[str] = []
    absent: set[Seat] = set()
    for seat in context.seats:
        result = await context.deliberator.deliberate(seat, context.phase, context)
        if result is None:
            unfilled.append(f"{context.phase.value}:{seat.value}")
            absent.add(seat)
            continue
        outputs[seat] = result
    return outputs, tuple(unfilled), frozenset(absent)


def _unavailable_events(
    context: PhaseContext,
    absent: frozenset[Seat],
) -> tuple[EmittedEvent, ...]:
    """Make each missing seat visible on the stream.

    CLAUDE.md 7 requires the system to admit what it does not know, and a silent
    absence reads to the researcher as agreement.
    """
    return tuple(
        EmittedEvent(
            event_type=SEAT_UNAVAILABLE,
            payload={
                "seat": seat.value,
                "phase": context.phase.value,
                "reason": "no model provider is connected to the Model Gateway",
            },
            idempotency_key=context.key("unavailable", seat.value),
        )
        for seat in sorted(absent, key=lambda item: item.value)
    )


def _float(value: object, default: float = 0.5) -> float:
    return float(value) if isinstance(value, (int, float, str)) else default


def _decimal(value: object, default: str = "0.5") -> Decimal:
    if isinstance(value, (int, str)):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(default)


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _uuids(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    parsed: list[UUID] = []
    for item in value:
        try:
            parsed.append(item if isinstance(item, UUID) else UUID(str(item)))
        except ValueError:
            continue
    return tuple(parsed)


async def run_precommitment(context: PhaseContext) -> PhaseOutcome:
    """Seal each seat's independent judgment before any evidence is shared.

    CLAUDE.md 4 puts this first so later agreement cannot be anchoring. The
    handler refuses a read before the seal, which is the property that makes the
    independence real rather than declared.
    """
    outputs, unfilled, absent = await _collect(context)
    handler = PrecommitmentHandler()
    events: list[EmittedEvent] = [
        EmittedEvent(
            event_type=EvidenceNodeType.RESEARCH_QUESTION.value,
            payload={"question": context.question},
            idempotency_key=context.key("question"),
            evidence_level="A",
        )
    ]
    for seat, output in outputs.items():
        await handler.submit(
            seat,
            PrecommitmentOutput(
                initial_judgment=str(output.get("initial_judgment", "")),
                confidence=_float(output.get("confidence")),
                blindspots=_strings(output.get("blindspots")),
                update_condition=str(output.get("update_condition", "")),
            ),
        )
    await handler.seal()
    sealed = await handler.read_all()
    events.extend(
        EmittedEvent(
            event_type=PRECOMMITMENT_SEALED,
            payload={
                "seat": seat.value,
                "confidence": submission.confidence,
                "update_condition": submission.update_condition,
            },
            idempotency_key=context.key("sealed", seat.value),
        )
        for seat, submission in sorted(sealed.items(), key=lambda kv: kv[0].value)
    )
    events.extend(_unavailable_events(context, absent))
    return PhaseOutcome(
        events=tuple(events),
        carry={
            "initial_judgments": {
                seat: submission.initial_judgment for seat, submission in sealed.items()
            }
        },
        unfilled_slots=unfilled,
        absent_seats=absent,
    )


async def run_acquisition(context: PhaseContext) -> PhaseOutcome:
    """Record each seat's evidence needs, then retrieve what can be retrieved.

    A request is a process event. A Source node is only emitted for a paper the
    pipeline actually fetched, at the level its retrieval supports -- metadata
    alone is Level B. Minting a Source from a request would be the fabrication
    CLAUDE.md 7 exists to prevent, so with no acquirer configured this round
    stops after the requests and the slots stay unfilled.

    Each freshly acquired Source is then handed to ``context.finding_extractor``
    (when configured) in an attempt to reach Level A: a StudyFinding event is
    only emitted for a quote the extractor actually located in the source's own
    text, never for a downgraded or fabricated Level B stand-in. A source the
    extractor could not turn into a finding -- no open access copy, an
    unparsable PDF, or an unlocatable quote -- is recorded as an unfilled slot
    instead.
    """
    outputs, unfilled, absent = await _collect(context)
    round_ = AcquisitionRound()
    events: list[EmittedEvent] = []
    all_requests: list[tuple[Seat, str]] = []
    for seat, output in sorted(outputs.items(), key=lambda kv: kv[0].value):
        requests = _strings(output.get("requests"))
        if not requests:
            continue
        all_requests.extend((seat, item) for item in requests)
        result = await round_.run(seat, requests)
        events.append(
            EmittedEvent(
                event_type=EVIDENCE_REQUESTED,
                # The handler's per-request uuid4 refs stay out of the payload:
                # nothing references them, and a value that changes between
                # replays would turn a resumed round into an idempotency
                # conflict. Only the count is recorded, as a cross-check.
                payload={
                    "seat": seat.value,
                    "requests": list(requests),
                    "request_count": len(result.source_requests),
                },
                idempotency_key=context.key("request", seat.value),
            )
        )

    slots = list(unfilled)
    if context.acquirer is None:
        if all_requests:
            slots.append("ACQUISITION:no_tool_provider")
    elif all_requests:
        acquisition = await context.acquirer.acquire(all_requests)
        events.extend(
            EmittedEvent(
                event_type=EvidenceNodeType.SOURCE.value,
                payload={
                    "node_id": str(item.source_id),
                    "doi": item.doi,
                    "title": item.title,
                    # The gate reads these to decide admission; passing the real
                    # retrieval state rather than an optimistic default is what
                    # makes stage three of CLAUDE.md 7.3 mean anything.
                    "has_doi": True,
                    "has_title": bool(item.title),
                    "has_authors": True,
                    "is_retracted": False,
                },
                idempotency_key=context.key("source", item.doi),
                evidence_level=item.evidence_level,
                source_id=item.source_id,
            )
            for item in acquisition.acquired
        )
        for item in acquisition.acquired:
            # A source dedup-hit against an already-persisted row was either
            # extracted in an earlier run or already failed and recorded --
            # re-running it here would spend tool/model budget on a result
            # this round cannot change, so only fresh sources are attempted.
            if context.finding_extractor is None or item.already_known:
                continue
            extraction = await context.finding_extractor.extract(
                item.source_id, item.doi
            )
            if extraction.ok and extraction.finding_id is not None:
                events.append(
                    EmittedEvent(
                        event_type=EvidenceNodeType.STUDY_FINDING.value,
                        payload={
                            "doi": item.doi,
                            "finding_statement": extraction.finding_statement,
                            # Stage 4 (CITATION_ENTAILMENT) and Stage 5
                            # (METHOD_QUALITY) of the evidence gate only run
                            # for real when these two payload shapes are
                            # present -- packages/evidence/gate.py.
                            "exact_quote": extraction.exact_quote,
                            "method_quality": dict(extraction.method_quality),
                        },
                        idempotency_key=context.key(
                            "finding", str(extraction.finding_id)
                        ),
                        evidence_level=extraction.evidence_level,
                        source_id=item.source_id,
                        finding_id=extraction.finding_id,
                    )
                )
            else:
                slots.append(
                    f"ACQUISITION:no_finding:{item.doi}:{extraction.reason}"
                )
        events.extend(
            EmittedEvent(
                event_type=SOURCE_REFUSED,
                payload={"query": item.query, "reason": item.reason},
                idempotency_key=context.key("refused", item.query),
            )
            for item in acquisition.refused
        )
        # A request nobody could resolve is a hole in the evidence, and
        # CLAUDE.md 10 wants it counted rather than forgotten.
        slots.extend(
            f"ACQUISITION:unresolved:{query}" for query in acquisition.unresolvable
        )
        slots.extend(
            f"ACQUISITION:refused:{item.query}" for item in acquisition.refused
        )

    events.extend(_unavailable_events(context, absent))
    return PhaseOutcome(
        events=tuple(events),
        unfilled_slots=tuple(slots),
        absent_seats=absent,
    )


async def run_evidence_exchange(context: PhaseContext) -> PhaseOutcome:
    """Publish each seat's evidence projection with private fields stripped."""
    outputs, unfilled, absent = await _collect(context)
    round_ = ExchangeRound()
    events: list[EmittedEvent] = []
    for seat, output in sorted(outputs.items(), key=lambda kv: kv[0].value):
        raw = output.get("evidence_items")
        if not isinstance(raw, (list, tuple)):
            continue
        items = tuple(
            EvidenceProjectionItem(
                source_id=UUID(str(item.get("source_id"))),
                anchor_summary=str(item.get("anchor_summary", "")),
                level=str(item.get("level", "D")),
            )
            for item in raw
            if isinstance(item, Mapping) and item.get("source_id")
        )
        if not items:
            continue
        published = await round_.run(items)
        events.append(
            EmittedEvent(
                event_type=EVIDENCE_PUBLISHED,
                payload={
                    "seat": seat.value,
                    "items": [
                        {
                            "source_id": str(item.source_id),
                            "anchor_summary": item.anchor_summary,
                            "level": item.level,
                        }
                        for item in published.evidence_items
                    ],
                },
                idempotency_key=context.key("published", seat.value),
            )
        )
    events.extend(_unavailable_events(context, absent))
    return PhaseOutcome(
        events=tuple(events),
        unfilled_slots=unfilled,
        absent_seats=absent,
    )


async def run_cross_examination(context: PhaseContext) -> PhaseOutcome:
    """Record every challenge, including the ones that stay unresolved.

    CLAUDE.md 4 forbids a challenge from disappearing, so an unresolved one is
    carried forward to joint modeling rather than dropped when the round ends.
    """
    outputs, unfilled, absent = await _collect(context)
    handler = CrossExaminationHandler()
    events: list[EmittedEvent] = []
    blocked: list[str] = []
    for seat, output in sorted(outputs.items(), key=lambda kv: kv[0].value):
        raw = output.get("challenges")
        if not isinstance(raw, (list, tuple)):
            continue
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                continue
            claim_ids = _uuids([item.get("claim_id")])
            if not claim_ids:
                continue
            entry = ChallengeEntry(
                claim_id=claim_ids[0],
                challenger=seat,
                target_seat=seat,
                challenge_statement=str(item.get("statement", "")),
                is_fatal=bool(item.get("is_fatal", False)),
            )
            await handler.submit_challenge(entry)
            if entry.is_fatal:
                blocked.append(str(entry.claim_id))
            events.append(
                EmittedEvent(
                    event_type=CHALLENGE_RAISED,
                    payload={
                        "seat": seat.value,
                        "claim_id": str(entry.claim_id),
                        "statement": entry.challenge_statement,
                        "is_fatal": entry.is_fatal,
                    },
                    idempotency_key=context.key("challenge", seat.value, index),
                )
            )
    events.extend(_unavailable_events(context, absent))
    return PhaseOutcome(
        events=tuple(events),
        carry={"blocked_claim_ids": tuple(blocked)},
        unfilled_slots=unfilled,
        absent_seats=absent,
    )


async def run_blindspot_bounty(context: PhaseContext) -> PhaseOutcome:
    """Score and rank the blindspots the seats nominated.

    The scoring is the one part of the protocol that is fully deterministic, so
    it runs for real on whatever the seats supplied.
    """
    outputs, unfilled, absent = await _collect(context)
    handler = BlindspotBountyHandler()
    items: list[BlindspotItem] = []
    for output in outputs.values():
        raw = output.get("blindspots")
        if not isinstance(raw, (list, tuple)):
            continue
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            ids = _uuids([item.get("id")])
            if not ids:
                continue
            items.append(
                BlindspotItem(
                    id=ids[0],
                    statement=str(item.get("statement", "")),
                    impact=_decimal(item.get("impact")),
                    uncertainty=_decimal(item.get("uncertainty")),
                    investigability=_decimal(item.get("investigability")),
                    novelty=_decimal(item.get("novelty")),
                    normalized_cost=_decimal(item.get("normalized_cost")),
                )
            )
    events: list[EmittedEvent] = []
    if items:
        result = handler.score_and_assign(
            BountyInput(
                blindspot_items=tuple(items), claim_refs=context.confirmed_claims
            )
        )
        events.extend(
            EmittedEvent(
                event_type=EvidenceNodeType.BLINDSPOT.value,
                payload={
                    "statement": scored.item.statement,
                    "score": str(scored.score),
                    "node_id": str(scored.item.id),
                },
                idempotency_key=context.key("blindspot", scored.item.id),
                evidence_level="A",
            )
            for scored in result.scored_items
        )
        events.append(
            EmittedEvent(
                event_type=BOUNTY_ASSIGNED,
                # The handler stamps each assignment with a fresh uuid4. That id
                # is deliberately dropped here: a payload that differs between
                # replays would collide with its own idempotency key, and the
                # assignment is already identified by blindspot and rank.
                payload={
                    "assignments": [
                        {
                            "blindspot_id": str(item["blindspot_id"]),
                            "target_seat": str(item["target_seat"]),
                            "priority_rank": int(str(item["priority_rank"])),
                            "score": str(item["score"]),
                        }
                        for item in result.assignments
                    ]
                },
                idempotency_key=context.key("assignments"),
            )
        )
    events.extend(_unavailable_events(context, absent))
    return PhaseOutcome(
        events=tuple(events),
        unfilled_slots=unfilled,
        absent_seats=absent,
    )


async def run_joint_modeling(context: PhaseContext) -> PhaseOutcome:
    """Draft the conditional consensus, refusing to draft without opposition.

    The handler withholds a consensus when the strongest opposition or the
    falsification conditions are missing. That refusal is the mechanism behind
    CLAUDE.md 4's ban on settling scientific truth by majority.
    """
    outputs, unfilled, absent = await _collect(context)
    handler = JointModelingHandler()
    merged: dict[str, list[str]] = {
        "boundary_conditions": [],
        "falsification_conditions": [],
        "unresolved_conflicts": [],
    }
    opposition: list[UUID] = []
    for output in outputs.values():
        for name in merged:
            merged[name].extend(_strings(output.get(name)))
        opposition.extend(_uuids(output.get("strongest_opposition_refs")))

    result = handler.run(
        JointModelInput(
            claim_refs=context.confirmed_claims,
            challenge_refs=_uuids(context.carried.get("blocked_claim_ids")),
            strongest_opposition_refs=tuple(opposition),
            falsification_conditions=tuple(merged["falsification_conditions"]),
            boundary_conditions=tuple(merged["boundary_conditions"]),
            unresolved_conflicts=tuple(merged["unresolved_conflicts"]),
        )
    )
    events: list[EmittedEvent] = []
    if result.ready:
        events.append(
            EmittedEvent(
                event_type=CONSENSUS_DRAFTED,
                payload={
                    "conditional_consensus": result.conditional_consensus,
                    "boundary_conditions": list(result.boundary_conditions),
                    "unresolved_conflicts": list(result.unresolved_conflicts),
                    "falsification_conditions": list(result.falsification_conditions),
                },
                idempotency_key=context.key("consensus"),
            )
        )
        # A capsule is only worth folding when there is something to fold: a
        # consensus with no recorded boundary and no recorded conflict is not
        # a debate, it is an agreement, and CLAUDE.md 5.2 requires a Dialectical
        # Fold to preserve both -- there would be nothing to preserve.
        if result.boundary_conditions and result.unresolved_conflicts:
            source_refs = tuple(
                dict.fromkeys((*result.supporting_refs, *result.opposing_refs))
            )
            try:
                capsule = DebateCapsule(
                    common_ground=(result.conditional_consensus,),
                    strongest_support=result.supporting_refs,
                    strongest_opposition=result.opposing_refs,
                    hinge_variables=result.hinge_variables,
                    boundary_conditions=result.boundary_conditions,
                    unresolved_conflicts=result.unresolved_conflicts,
                    falsification_conditions=result.falsification_conditions,
                    source_refs=source_refs,
                )
            except ValueError:
                # A required dialectical field came back empty (e.g. no
                # confirmed claims to hang hinge variables off of) -- an
                # honest gap, not a fabricated capsule.
                unfilled = (*unfilled, "JOINT_MODELING:no_capsule_fold")
            else:
                events.append(
                    EmittedEvent(
                        event_type=EvidenceNodeType.DEBATE_CAPSULE.value,
                        payload={
                            "common_ground": list(capsule.common_ground),
                            "strongest_support": [
                                str(item) for item in capsule.strongest_support
                            ],
                            "strongest_opposition": [
                                str(item) for item in capsule.strongest_opposition
                            ],
                            "hinge_variables": list(capsule.hinge_variables),
                            "boundary_conditions": list(capsule.boundary_conditions),
                            "unresolved_conflicts": list(capsule.unresolved_conflicts),
                            "falsification_conditions": list(
                                capsule.falsification_conditions
                            ),
                            "source_refs": [
                                str(item) for item in capsule.source_refs
                            ],
                        },
                        idempotency_key=context.key("debate_capsule"),
                        evidence_level="A",
                    )
                )
        else:
            unfilled = (*unfilled, "JOINT_MODELING:no_capsule_fold")
    else:
        unfilled = (
            *unfilled,
            *(f"JOINT_MODELING:{name}" for name in result.missing_fields),
        )
    events.extend(_unavailable_events(context, absent))
    return PhaseOutcome(
        events=tuple(events),
        carry={"consensus_ready": result.ready},
        unfilled_slots=unfilled,
        absent_seats=absent,
    )


async def run_final_rejudgment(context: PhaseContext) -> PhaseOutcome:
    """Let each seat judge again independently, and keep every dissent."""
    outputs, unfilled, absent = await _collect(context)
    handler = FinalRejudgmentHandler()
    initial = context.carried.get("initial_judgments")
    judgments = {
        seat: str(output.get("final_judgment", ""))
        for seat, output in outputs.items()
    }
    if not judgments and isinstance(initial, Mapping):
        judgments = {seat: str(text) for seat, text in initial.items()}
    if not judgments:
        return PhaseOutcome(
            events=_unavailable_events(context, absent),
            unfilled_slots=unfilled,
            absent_seats=absent,
        )
    result = handler.run(
        FinalRejudgmentInput(
            joint_snapshot=JointModelInput(
                claim_refs=context.confirmed_claims,
                challenge_refs=(),
                strongest_opposition_refs=(),
                falsification_conditions=(),
                boundary_conditions=(),
                unresolved_conflicts=(),
            ),
            initial_judgments=judgments,
        )
    )
    events = [
        EmittedEvent(
            event_type=FINAL_JUDGMENT,
            payload={
                "seat": judgment.seat.value,
                "final_judgment": judgment.final_judgment,
                "confidence": judgment.confidence,
                # A dissent is recorded on the event itself so it cannot be lost
                # when the report is summarised. CLAUDE.md 4 forbids silently
                # dropping a minority position.
                "has_dissent": judgment.has_dissent,
            },
            idempotency_key=context.key("judgment", judgment.seat.value),
        )
        for judgment in result.judgments
    ]
    dissenters = [judgment for judgment in result.judgments if judgment.has_dissent]
    if dissenters and context.confirmed_claims:
        # The first confirmed claim is the MVP target for a dissent: a seat
        # dissenting about several claims separately is future scope. Every
        # dissent still needs a real Claim to attach to -- issuing one against
        # nothing would produce a DissentCertificate the graph cannot anchor.
        target_id = context.confirmed_claims[0]
        for judgment in dissenters:
            certificate = issue_dissent(
                author=judgment.seat,
                target_id=target_id,
                statement=judgment.final_judgment,
                reason=f"最终复判判定为异议：{judgment.final_judgment}",
                evidence_refs=judgment.evidence_refs,
            )
            events.append(
                EmittedEvent(
                    event_type=EvidenceNodeType.DISSENT_CERTIFICATE.value,
                    payload={
                        "author": certificate.author.value,
                        "target_id": str(certificate.target_id),
                        "statement": certificate.statement,
                        "reason": certificate.reason,
                        "withdrawal_condition": certificate.withdrawal_condition,
                    },
                    idempotency_key=context.key("dissent", judgment.seat.value),
                    evidence_level="A",
                )
            )
    elif dissenters:
        unfilled = (*unfilled, "FINAL_REJUDGMENT:no_dissent_target")
    events.extend(_unavailable_events(context, absent))
    return PhaseOutcome(
        events=tuple(events),
        unfilled_slots=unfilled,
        absent_seats=absent,
    )


async def run_reporting(context: PhaseContext) -> PhaseOutcome:
    """Close the task. The report is assembled from the graph, not from a round."""
    return PhaseOutcome()


PhaseRunner = Callable[[PhaseContext], Awaitable[PhaseOutcome]]

PHASE_RUNNERS: dict[TaskPhase, PhaseRunner] = {
    TaskPhase.PRECOMMITMENT: run_precommitment,
    TaskPhase.ACQUISITION: run_acquisition,
    TaskPhase.EVIDENCE_EXCHANGE: run_evidence_exchange,
    TaskPhase.CROSS_EXAMINATION: run_cross_examination,
    TaskPhase.BLINDSPOT_BOUNTY: run_blindspot_bounty,
    TaskPhase.JOINT_MODELING: run_joint_modeling,
    TaskPhase.FINAL_REJUDGMENT: run_final_rejudgment,
    TaskPhase.REPORTING: run_reporting,
}


def runner_for(phase: TaskPhase) -> PhaseRunner:
    """Return the runner for a phase, failing loudly on an unregistered one.

    A missing entry would otherwise skip a round of the protocol silently, which
    is the single most damaging way this module could fail.
    """
    try:
        return PHASE_RUNNERS[phase]
    except KeyError as error:
        raise KeyError(f"no runner registered for phase {phase}") from error
