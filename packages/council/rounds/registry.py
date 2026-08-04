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
from uuid import NAMESPACE_URL, UUID, uuid5

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
from packages.evidence.adversarial_retrieval import adversarial_retrieval_queries
from packages.evidence.contracts import ClaimType, EvidenceEdgeType, EvidenceNodeType
from packages.evidence.dialectical_fold import DebateCapsule
from packages.evidence.lifecycle import QuarantinedNode, check_resurrection_conditions
from packages.evidence.source_diversity import SourceDiversityInput, check_diversity

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
ADVERSARIAL_RETRIEVAL_ATTEMPTED = "ADVERSARIAL_RETRIEVAL_ATTEMPTED"
# A status change on an already-quarantined node, not a new formal node type --
# design spec 7's Resurrect is "emit a status-change event", not "re-run the
# gate and rewrite the graph node" (see packages/evidence/lifecycle.py).
RESURRECTION_GRANTED = "RESURRECTION_GRANTED"
# A qualitative Evolution View trajectory marker (plan phase 5), not a formal
# node -- see ``_confidence_marker`` for what it carries and why.
CONFIDENCE_UPDATED = "CONFIDENCE_UPDATED"

# Deterministic derivation for a forked claim's node id. The orchestrator's
# idempotency keys must be stable across replay, and a random uuid4() written
# into a payload would collide with its own key on a resumed run -- so a
# fork's new Claim node is named from what produced it (task, challenged claim,
# seat, position) rather than randomly, mirroring packages/papers/packet.py's
# use of uuid5 for the same reason.
_FORK_NAMESPACE = uuid5(NAMESPACE_URL, "https://poliscope.internal/council/fork")


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

    async def acquire_uploaded(
        self, object_ids: tuple[UUID, ...]
    ) -> AcquisitionLike: ...


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
    def doi(self) -> str | None: ...

    @property
    def title(self) -> str: ...

    @property
    def evidence_level(self) -> str: ...

    @property
    def already_known(self) -> bool: ...

    # Feed packages.evidence.source_diversity.check_diversity. Defaulted to
    # empty/None on the dataclass implementation, so existing fixtures that
    # predate this field keep working once given matching defaults.
    @property
    def authors(self) -> tuple[str, ...]: ...

    @property
    def dataset_id(self) -> str | None: ...

    # Only set for an uploaded PDF's source (see AcquiredSource.object_id).
    @property
    def object_id(self) -> UUID | None: ...


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

    async def extract_uploaded(
        self, source_id: UUID, object_id: UUID
    ) -> FindingExtractionLike: ...


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
    # Nodes quarantined in an earlier run of this task, loaded from the ledger
    # by the worker (see apps/worker/jobs.py::_quarantined_nodes). Empty for a
    # task with nothing quarantined yet -- Resurrect then has nothing to do.
    quarantined: tuple[QuarantinedNode, ...] = ()
    # Object ids of PDFs the researcher uploaded for this task (loaded by the
    # worker from ResearchTaskModel.user_evidence["pdf_object_ids"] -- see
    # apps/worker/jobs.py::deliberate). Empty when nothing was uploaded, which
    # is the common case: most sources still arrive via DOI/free-text request.
    pdf_object_ids: tuple[UUID, ...] = ()
    # Plan phase 8.3: the human's advisory directional steer collected at the
    # BLINDSPOT_BOUNTY -> JOINT_MODELING checkpoint, or None outside that one
    # phase. Deliberately a dedicated field rather than another `carried`
    # entry: `carried` accumulates forward into every later phase's prompt
    # (see packages/council/deliberation.py::_user_prompt), and CLAUDE.md 4/8
    # requires this text to be visible in JOINT_MODELING's prompt only, never
    # treated as evidence a later phase's Evidence Gate/Claim/DissentCertificate
    # logic can read.
    guidance: str | None = None

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


def _confidence_marker(
    context: PhaseContext,
    claim_id: UUID,
    note: str,
    *key_parts: object,
) -> EmittedEvent:
    """One Evolution View trajectory point for ``claim_id`` at this phase.

    Design spec 8's Evolution View (plan phase 5) could previously only plot
    the sparse, incidental events a round happened to emit (a fork, a
    challenge, a dissent) -- most phase boundaries left no trace at all for a
    claim that was not itself challenged, so the view could not draw a
    continuous per-claim trajectory. This adds one qualitative marker per
    phase boundary that plausibly shifts a claim's standing (EVIDENCE_
    EXCHANGE, CROSS_EXAMINATION, JOINT_MODELING, FINAL_REJUDGMENT), so every
    confirmed claim gets a point at each of those four phases even when
    nothing else about it changed.

    ``note`` is deliberately a plain-language sentence, never a number.
    CLAUDE.md 16 forbids substituting a model's confidence for statistical
    uncertainty, and a fabricated "+0.07 confidence" delta would be exactly
    that -- there is no model in this MVP that computes a real confidence
    delta for a claim. The honest content of this marker is only "something
    happened to this claim's standing at this phase, and here is what", not
    "the probability changed by this much".
    """
    return EmittedEvent(
        event_type=CONFIDENCE_UPDATED,
        payload={"phase": context.phase.value, "confidence_delta_note": note},
        idempotency_key=context.key("confidence_updated", *key_parts),
        claim_id=claim_id,
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
            # Keys must be plain strings, not ``Seat`` members: this dict is
            # serialised into ``CouncilCheckpoint.carried`` (a
            # ``FrozenDict[str, object]``) whenever the run halts at the
            # BLINDSPOT_BOUNTY -> JOINT_MODELING checkpoint, and FrozenDict
            # rejects any key whose exact type is not ``str`` -- including a
            # StrEnum member, which is a ``str`` subclass but not ``str``
            # itself. ``run_final_rejudgment`` converts back to ``Seat`` on
            # read (see below).
            "initial_judgments": {
                seat.value: submission.initial_judgment
                for seat, submission in sealed.items()
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

    Adversarial retrieval (design spec 7.9, mechanism 4 of 4): alongside
    whatever the seats actually asked for, this round also appends six
    reverse-search-intent queries per confirmed claim, attributed to the
    adversarial falsifier seat, so acquisition itself is not structurally
    biased toward finding only what already supports the judgment on the
    table. See ``packages.evidence.adversarial_retrieval`` for the honest
    scope note on what these queries can and cannot do today.
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

    # Adversarial retrieval (design spec 7.9, README known gaps item 5):
    # generated from confirmed claims alone, so it runs whether or not any
    # seat asked for anything this round -- the whole point is that
    # acquisition is not solely steered by what the seats already believe is
    # worth looking for.
    adversarial_queries: list[str] = []
    for claim_id in context.confirmed_claims:
        adversarial_queries.extend(adversarial_retrieval_queries(claim_id))
    if adversarial_queries:
        all_requests.extend(
            (Seat.ADVERSARY_FALSIFIER, query) for query in adversarial_queries
        )
        events.append(
            EmittedEvent(
                event_type=EVIDENCE_REQUESTED,
                payload={
                    "seat": Seat.ADVERSARY_FALSIFIER.value,
                    "requests": adversarial_queries,
                    "request_count": len(adversarial_queries),
                    "kind": "adversarial_retrieval",
                },
                idempotency_key=context.key("adversarial_request"),
            )
        )

    adversarial_query_set = frozenset(adversarial_queries)

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
                    "has_doi": item.doi is not None,
                    "has_title": bool(item.title),
                    "has_authors": True,
                    "is_retracted": False,
                    # Carried alongside the DB row (SourceModel.authors/dataset_id,
                    # which production lineage detection reads directly -- see
                    # apps/api/routers/workspace.py) so a DB-less event stream, such
                    # as packages.evaluation.harness.EvalLedger, can compute the same
                    # SAME_DATASET/SAME_RESEARCH_TEAM lineage from the event payload
                    # alone.
                    "authors": list(item.authors),
                    "dataset_id": item.dataset_id,
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
            if item.doi is None:
                # Unreachable in practice: every AcquiredSource on this branch
                # comes from acquire()'s DOI/free-text path, which always sets
                # a real doi -- only acquire_uploaded's results carry None.
                # The check exists because AcquiredLike.doi widened to
                # str | None for that upload branch, and mypy cannot see the
                # two branches never mix.
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
        # CLAUDE.md 10 wants it counted rather than forgotten -- except for
        # the adversarial-retrieval queries generated above. Those are
        # inherently free-text (no DOI substring for CandidatePool.add to
        # resolve), so acquisition tries them against the free, keyless search
        # adapters (packages.evidence.adversarial_retrieval's scope note) --
        # some resolve, some don't, depending on whether OpenAlex/Crossref/
        # Semantic Scholar actually index a matching paper. Either way this is
        # a constant, system-wide adapter-coverage fact rather than a
        # task-specific evidentiary hole: counting a miss here would make
        # TaskStatus.COMPLETED_WITH_GAPS permanent for any task that reaches
        # this round with confirmed claims, defeating the point of that
        # status. They stay visible instead through the dedicated event below.
        slots.extend(
            f"ACQUISITION:unresolved:{query}"
            for query in acquisition.unresolvable
            if query not in adversarial_query_set
        )
        slots.extend(
            f"ACQUISITION:refused:{item.query}"
            for item in acquisition.refused
            if item.query not in adversarial_query_set
        )

        if adversarial_query_set:
            unresolved_adversarial = sum(
                1
                for query in acquisition.unresolvable
                if query in adversarial_query_set
            )
            refused_adversarial = sum(
                1
                for item in acquisition.refused
                if item.query in adversarial_query_set
            )
            attempted = len(adversarial_query_set)
            events.append(
                EmittedEvent(
                    event_type=ADVERSARIAL_RETRIEVAL_ATTEMPTED,
                    payload={
                        "attempted": attempted,
                        "unresolved_count": unresolved_adversarial,
                        "refused_count": refused_adversarial,
                        "resolved_count": (
                            attempted
                            - unresolved_adversarial
                            - refused_adversarial
                        ),
                    },
                    idempotency_key=context.key("adversarial_outcome"),
                )
            )
        # Source diversity constraint (design spec 7.9, README known gaps
        # item 5): task-scoped, not per-claim -- see
        # packages/evidence/source_diversity.py for why. Runs over every
        # source acquired for this task so far, cache hits included, so a
        # source fetched in an earlier run still counts against a later one.
        diversity = check_diversity(
            [
                SourceDiversityInput(
                    source_id=item.source_id,
                    dataset_id=item.dataset_id,
                    authors=item.authors,
                )
                for item in acquisition.acquired
            ]
        )
        if diversity is not None:
            events.append(
                EmittedEvent(
                    event_type=EvidenceNodeType.BLINDSPOT.value,
                    payload={
                        "statement": diversity.reason,
                        "source_refs": [str(sid) for sid in diversity.source_ids],
                        "kind": "source_diversity",
                    },
                    idempotency_key=context.key("diversity_blindspot"),
                    evidence_level="A",
                )
            )

    if context.acquirer is not None and context.pdf_object_ids:
        # A separate pass rather than folding into the DOI/free-text branch
        # above: an upload has no doi to dedup or idempotency-key on (that
        # branch keys on context.key("source", item.doi), which every
        # doi=None upload would collide on), and it has no discovery step to
        # share -- acquire_uploaded skips straight to persistence.
        uploaded = await context.acquirer.acquire_uploaded(context.pdf_object_ids)
        events.extend(
            EmittedEvent(
                event_type=EvidenceNodeType.SOURCE.value,
                payload={
                    "node_id": str(item.source_id),
                    "doi": item.doi,
                    "title": item.title,
                    "has_doi": item.doi is not None,
                    "has_title": bool(item.title),
                    # Unlike a DOI lookup, acquisition alone never learns an
                    # uploaded PDF's authors -- that has to wait for
                    # FindingExtractor.extract_uploaded to read the text, and
                    # even then only dataset_id gets written back today. This
                    # reports the real, usually-empty state rather than the
                    # DOI branch's hardcoded True, per CLAUDE.md 7.
                    "has_authors": bool(item.authors),
                    "is_retracted": False,
                    "authors": list(item.authors),
                    "dataset_id": item.dataset_id,
                    "object_id": str(item.object_id) if item.object_id else None,
                },
                idempotency_key=context.key("uploaded_source", str(item.object_id)),
                evidence_level=item.evidence_level,
                source_id=item.source_id,
            )
            for item in uploaded.acquired
        )
        for item in uploaded.acquired:
            if (
                context.finding_extractor is None
                or item.already_known
                or item.object_id is None
            ):
                continue
            extraction = await context.finding_extractor.extract_uploaded(
                item.source_id, item.object_id
            )
            if extraction.ok and extraction.finding_id is not None:
                events.append(
                    EmittedEvent(
                        event_type=EvidenceNodeType.STUDY_FINDING.value,
                        payload={
                            "doi": None,
                            "finding_statement": extraction.finding_statement,
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
                    f"ACQUISITION:no_finding:upload:{item.object_id}:{extraction.reason}"
                )
    elif context.pdf_object_ids:
        slots.append("ACQUISITION:no_tool_provider_uploaded")

    events.extend(_unavailable_events(context, absent))
    return PhaseOutcome(
        events=tuple(events),
        unfilled_slots=tuple(slots),
        absent_seats=absent,
    )


def _resurrection_events(
    context: PhaseContext,
    seat: Seat,
    output: Mapping[str, object],
) -> tuple[tuple[EmittedEvent, ...], tuple[str, ...]]:
    """Resurrect (design spec 5, mechanism 3 of 3): does this seat's newly
    published evidence satisfy a quarantined node's resurrection condition?

    A seat asks by naming the quarantined node id and the evidence it is
    citing; malformed or unresolvable requests are reported, not dropped
    silently (CLAUDE.md 7). Matching ``check_resurrection_conditions`` is the
    same MVP predicate ``LifecycleService.resurrect`` uses -- see
    ``packages/evidence/lifecycle.py`` for why this stays a plain non-empty
    check rather than a claim-matching model.
    """
    if not context.quarantined:
        return (), ()
    raw = output.get("resurrection_requests")
    if not isinstance(raw, (list, tuple)):
        return (), ()
    by_id = {node.node_id: node for node in context.quarantined}
    events: list[EmittedEvent] = []
    slots: list[str] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        raw_node_id = item.get("node_id")
        try:
            node_id = UUID(str(raw_node_id))
        except (ValueError, TypeError):
            slots.append(f"EVIDENCE_EXCHANGE:resurrection_malformed:{raw_node_id}")
            continue
        node = by_id.get(node_id)
        if node is None:
            slots.append(f"EVIDENCE_EXCHANGE:resurrection_unknown_node:{node_id}")
            continue
        raw_refs = item.get("evidence_refs")
        evidence_refs = tuple(
            UUID(str(ref))
            for ref in (raw_refs if isinstance(raw_refs, (list, tuple)) else ())
        )
        if not check_resurrection_conditions(node, evidence_refs):
            slots.append(f"EVIDENCE_EXCHANGE:resurrection_condition_not_met:{node_id}")
            continue
        events.append(
            EmittedEvent(
                event_type=RESURRECTION_GRANTED,
                payload={
                    "node_id": str(node_id),
                    "seat": seat.value,
                    "evidence_refs": [str(ref) for ref in evidence_refs],
                    "resurrection_condition": node.resurrection_condition,
                },
                idempotency_key=context.key("resurrection", node_id),
            )
        )
    return tuple(events), tuple(slots)


async def run_evidence_exchange(context: PhaseContext) -> PhaseOutcome:
    """Publish each seat's evidence projection with private fields stripped."""
    outputs, unfilled, absent = await _collect(context)
    round_ = ExchangeRound()
    events: list[EmittedEvent] = []
    slots: list[str] = list(unfilled)
    published_item_count = 0
    for seat, output in sorted(outputs.items(), key=lambda kv: kv[0].value):
        raw = output.get("evidence_items")
        if isinstance(raw, (list, tuple)):
            items = tuple(
                EvidenceProjectionItem(
                    source_id=UUID(str(item.get("source_id"))),
                    anchor_summary=str(item.get("anchor_summary", "")),
                    level=str(item.get("level", "D")),
                )
                for item in raw
                if isinstance(item, Mapping) and item.get("source_id")
            )
            if items:
                published = await round_.run(items)
                published_item_count += len(published.evidence_items)
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
        resurrection_events, resurrection_slots = _resurrection_events(
            context, seat, output
        )
        events.extend(resurrection_events)
        slots.extend(resurrection_slots)
    events.extend(_unavailable_events(context, absent))
    if published_item_count:
        # A marker per confirmed claim, not per published item: this round has
        # no claim_id on an evidence item (EvidenceProjectionItem carries a
        # source_id, not a claim reference), so the honest scope is "evidence
        # moved in this round" against every claim the task is about, not a
        # fabricated one-to-one mapping this round cannot support.
        note = f"证据交换阶段新增 {published_item_count} 条已发布证据条目。"
        events.extend(
            _confidence_marker(context, claim_id, note, claim_id)
            for claim_id in context.confirmed_claims
        )
    return PhaseOutcome(
        events=tuple(events),
        unfilled_slots=tuple(slots),
        absent_seats=absent,
    )


def _fork_events(
    context: PhaseContext,
    seat: Seat,
    claim_id: UUID,
    index: int,
    fork: Mapping[str, object],
) -> tuple[EmittedEvent, ...]:
    """Fork (design spec 5, mechanism 2 of 3): a challenge that cannot be
    reconciled by ``QUALIFY`` produces a parallel ``Claim`` node instead of
    silently overwriting or dropping the disagreement (CLAUDE.md 4's "异议不得
    被静默删除"). No new edge type is introduced -- the existing ``CONTRADICTS``
    edge already says exactly this (YAGNI, per the plan).

    Two events, not one: ``_write_edges`` refuses an edge whose target node was
    never admitted, and the original claim being forked from may never have
    been projected as its own ``Claim`` node by any other round. The anchor
    event is keyed only by ``claim_id`` so repeated forks against the same
    claim do not try to create it twice; it is a plain upsert either way
    (``_upsert_node`` in packages/evidence/sql_projector.py), so a duplicate
    attempt would be harmless even without that key.

    The new claim's id is derived, not random (see ``_FORK_NAMESPACE``), so a
    resumed run reaches the same id rather than minting a second fork node for
    the same disagreement.

    CLAUDE.md 5.2 requires a ``Claim`` to carry ``claim_type`` and ``scope``.
    This round has no database access to the original claim's real ``scope``
    (that lives on ``atomic_claims``, reachable only from the worker/repository
    layer) -- rather than guessing at it, the anchor's ``scope`` is left an
    explicit empty object, honestly saying "not recorded here" instead of
    fabricating one (CLAUDE.md 7). The fork's own scope can be self-reported by
    the seat, same as ``statement``.

    ``claim_type``/``study_design`` -- deviation disclosed per CLAUDE.md 17:
    the plan for this mechanism assumed an upstream causal-language/design
    signal already exists on the source ``StudyFinding``. It does not --
    ``packages.papers.finding_extraction`` only extracts ``method_quality``
    (six 0-1 quality dimensions), never a design-type classification, and
    nothing in the design spec defines one either. Rather than inventing a
    classifier this MVP has no model for, the forked claim's ``claim_type``
    and ``study_design`` are self-reported by the challenging seat inside this
    same ``fork`` mapping -- exactly the precedent already set by ``statement``
    and ``scope`` above, and justified the same way ``run_cross_examination``
    already justifies self-reported ``is_fatal``: no independent computation
    exists, so the seat making the claim states it, and an unrecognised or
    absent value honestly falls back to ``correlational`` rather than being
    guessed as ``causal`` (CLAUDE.md 7). The anchor -- the pre-existing claim
    being forked *from*, not asserted here -- keeps the same ``correlational``
    placeholder as before; its real type, like its real scope, lives in
    ``atomic_claims`` and is not fabricated here.
    """
    statement = str(fork.get("statement", ""))
    if not statement:
        return ()
    new_claim_id = uuid5(
        _FORK_NAMESPACE,
        f"{context.task_id}␟{claim_id}␟{seat.value}␟{index}",
    )
    raw_scope = fork.get("scope")
    scope = raw_scope if isinstance(raw_scope, Mapping) else {}
    claim_type = _self_reported_claim_type(fork.get("claim_type"))
    study_design = str(fork.get("study_design", ""))
    anchor = EmittedEvent(
        event_type=EvidenceNodeType.CLAIM.value,
        payload={"claim_type": ClaimType.CORRELATIONAL.value, "scope": {}},
        idempotency_key=context.key("claim_anchor", claim_id),
        claim_id=claim_id,
    )
    forked_payload: dict[str, object] = {
        "claim_type": claim_type.value,
        "scope": dict(scope),
        "statement": statement,
        "falsification_condition": str(
            fork.get("falsification_condition", "")
        ),
        "edges": [
            {
                "type": EvidenceEdgeType.CONTRADICTS.value,
                "target": str(claim_id),
            }
        ],
    }
    if study_design:
        forked_payload["study_design"] = study_design
    forked = EmittedEvent(
        event_type=EvidenceNodeType.CLAIM.value,
        payload=forked_payload,
        idempotency_key=context.key("fork", seat.value, index),
        claim_id=new_claim_id,
    )
    return (anchor, forked)


def _self_reported_claim_type(raw: object) -> ClaimType:
    """Parse the seat's self-reported ``claim_type``, defaulting to
    ``CORRELATIONAL`` for anything missing or unrecognised.

    An honest default (CLAUDE.md 7): understating a claim as correlational
    when it was meant as causal loses a check ``score_causal_overclaim`` would
    otherwise run, but guessing ``causal`` for an unparseable value would
    fabricate the one signal that check depends on.
    """
    if isinstance(raw, str):
        try:
            return ClaimType(raw)
        except ValueError:
            return ClaimType.CORRELATIONAL
    return ClaimType.CORRELATIONAL


async def run_cross_examination(context: PhaseContext) -> PhaseOutcome:
    """Record every challenge, including the ones that stay unresolved.

    CLAUDE.md 4 forbids a challenge from disappearing, so an unresolved one is
    carried forward to joint modeling rather than dropped when the round ends.
    A fatal challenge that also names a ``fork`` (self-reported by the seat,
    mirroring how ``is_fatal`` itself is self-reported -- this MVP has no
    claim-statement-comparison model to compute either independently) additionally
    forks a parallel Claim node instead of just blocking the original; see
    ``_fork_events``.
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
            severity = "致命" if entry.is_fatal else "非致命"
            events.append(
                _confidence_marker(
                    context,
                    entry.claim_id,
                    f"遭到 {seat.value} 的{severity}质询：{entry.challenge_statement}",
                    "challenge", seat.value, index,
                )
            )
            fork = item.get("fork")
            if entry.is_fatal and isinstance(fork, Mapping):
                fork_events = _fork_events(context, seat, entry.claim_id, index, fork)
                events.extend(fork_events)
                if len(fork_events) == 2:
                    forked_claim_id = fork_events[1].claim_id
                    if forked_claim_id is not None:
                        events.append(
                            _confidence_marker(
                                context,
                                forked_claim_id,
                                f"作为对 {entry.claim_id} 的分支主张被提出。",
                                "fork_confidence", seat.value, index,
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
                    "kind": "bounty",
                    # Carried alongside the collapsed score so the Blindspot
                    # Radar (design spec 8.5: impact x investigability,
                    # uncertainty as point size) can plot the real
                    # dimensions the seats nominated instead of re-deriving
                    # them from one scalar it cannot invert.
                    "impact": str(scored.item.impact),
                    "uncertainty": str(scored.item.uncertainty),
                    "investigability": str(scored.item.investigability),
                    "novelty": str(scored.item.novelty),
                    "normalized_cost": str(scored.item.normalized_cost),
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
        note = "联合建模阶段：已形成条件化共识。"
        events.extend(
            _confidence_marker(context, claim_id, note, claim_id)
            for claim_id in context.confirmed_claims
        )
        events.append(
            EmittedEvent(
                event_type=CONSENSUS_DRAFTED,
                payload={
                    "conditional_consensus": result.conditional_consensus,
                    "boundary_conditions": list(result.boundary_conditions),
                    "unresolved_conflicts": list(result.unresolved_conflicts),
                    "falsification_conditions": list(result.falsification_conditions),
                    # Merge (design spec 5, mechanism 3 of 3), cut down to
                    # "record the candidate" rather than "execute the merge":
                    # every unresolved conflict this round produced is, in this
                    # MVP's coarse model, a candidate the researcher may choose
                    # to reconcile by hand (CLAUDE.md 8, "研究者控制方向"). No
                    # code here merges anything -- that stays a human decision
                    # made in the frontend.
                    "merge_candidates": list(result.unresolved_conflicts),
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
        # Read back with str keys (see run_precommitment's carry comment) and
        # convert to Seat here, where FinalRejudgmentInput needs it.
        judgments = {
            Seat(str(seat_value)): str(text) for seat_value, text in initial.items()
        }
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
    if context.confirmed_claims:
        note = (
            f"最终复判阶段：{len(result.judgments)} 位科学家给出最终判断，"
            f"其中 {len(dissenters)} 位保留异议。"
        )
        events.extend(
            _confidence_marker(context, claim_id, note, claim_id)
            for claim_id in context.confirmed_claims
        )
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
