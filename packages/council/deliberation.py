"""The bridge between a council seat and the Model Gateway.

CLAUDE.md 8 requires every model call to go through the gateway rather than
through a vendor SDK scattered across the seats, and CLAUDE.md 3 requires the
seven seats to share one runtime while differing in role specification, private
state, and questioning rules. Both are satisfied here: one class, seven prompts,
one gateway.

**What this deliberately does not do.** It does not invent an answer when the
gateway has none. A missing recording, a schema repair that failed, or an
exhausted budget all return ``None``, which the orchestrator turns into a
reported unfilled evidence slot. CLAUDE.md 10 requires exactly that -- an
unfillable slot is reported, never papered over -- and CLAUDE.md 7 forbids
passing an AI derivation off as evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress

from packages.council.contracts import Seat
from packages.council.roles import ROLE_SPECS
from packages.council.rounds.registry import PhaseContext
from packages.epistemo.budget import BudgetExhausted, BudgetTracker
from packages.epistemo.contracts import TaskPhase
from packages.models.contracts import (
    ModelClass,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    SchemaStatus,
)

# What each seat is for. These are the questioning rules that make seven seats
# different rather than seven copies; CLAUDE.md 3 forbids the latter.
SEAT_INSTRUCTIONS: dict[Seat, str] = {
    Seat.THEORY_BUILDER: (
        "Name the mechanism the claim presupposes and say what would have to be "
        "true of it. Prefer a mechanism that makes a risky prediction."
    ),
    Seat.CAUSAL_SCIENTIST: (
        "Identify confounding, reverse causation, and selection. State the "
        "identifying assumption each cited design needs and whether it holds."
    ),
    Seat.MEASUREMENT_SCIENTIST: (
        "Separate the construct from its operationalisation. Flag self-report, "
        "unvalidated instruments, and construct drift across studies."
    ),
    Seat.REPLICATION_SCIENTIST: (
        "Judge statistical precision, power, and replication history. Treat a "
        "non-significant result as uninformative, never as evidence of no effect."
    ),
    Seat.BOUNDARY_SCIENTIST: (
        "State the populations, periods, platforms, and contexts the finding "
        "does and does not apply to. Resist generalising a subgroup result."
    ),
    Seat.ADVERSARY_FALSIFIER: (
        "Attack the strongest version of the claim. Propose the observation "
        "that would falsify it and the alternative explanation that survives."
    ),
    Seat.EVIDENCE_AUDITOR: (
        "Check that every cited finding is anchored to retrievable source text, "
        "and that separate papers are not double counting one dataset."
    ),
}

# Blind evidence review (CLAUDE.md 7.4, design spec 7.9, mechanism 2 of 4):
# a seat judging evidence quality must not see the author's identity, the
# venue, or its citation count -- any of those can substitute a reputation
# halo for the method-quality judgment CLAUDE.md 3 assigns each seat. Nothing
# in the registry's ``carry={...}`` sites currently writes one of these keys
# (grep confirms it: only ``initial_judgments``, ``blocked_claim_ids``, and
# ``consensus_ready`` exist today), so this is a structural guard against a
# future round handler doing so by accident, not a fix for a live leak.
_BIBLIOGRAPHIC_IDENTITY_KEYS = frozenset(
    {
        "author",
        "authors",
        "journal",
        "venue",
        "publisher",
        "citation_count",
        "impact_factor",
        "h_index",
    }
)

# The structured output each round expects. The names match the keys the
# registry's runners read, so a schema change is visible in one place.
PHASE_OUTPUT_SCHEMAS: dict[TaskPhase, str] = {
    TaskPhase.PRECOMMITMENT: "PrecommitmentOutput",
    TaskPhase.ACQUISITION: "AcquisitionRequests",
    TaskPhase.EVIDENCE_EXCHANGE: "EvidenceProjection",
    TaskPhase.CROSS_EXAMINATION: "ChallengeSet",
    TaskPhase.BLINDSPOT_BOUNTY: "BlindspotNominations",
    TaskPhase.JOINT_MODELING: "JointModelContribution",
    TaskPhase.FINAL_REJUDGMENT: "FinalJudgment",
}

# Precommitment and final rejudgment are where a seat commits to a position, so
# they get the strongest model; the rest are extraction and can run cheaper.
PHASE_MODEL_CLASSES: dict[TaskPhase, ModelClass] = {
    TaskPhase.PRECOMMITMENT: ModelClass.STRONG_REASONING,
    TaskPhase.ACQUISITION: ModelClass.MEDIUM,
    TaskPhase.EVIDENCE_EXCHANGE: ModelClass.MEDIUM,
    TaskPhase.CROSS_EXAMINATION: ModelClass.STRONG_REASONING,
    TaskPhase.BLINDSPOT_BOUNTY: ModelClass.STRONG_REASONING,
    TaskPhase.JOINT_MODELING: ModelClass.MEDIUM,
    TaskPhase.FINAL_REJUDGMENT: ModelClass.STRONG_REASONING,
}


def _system_prompt(seat: Seat, phase: TaskPhase) -> str:
    spec = ROLE_SPECS[seat]
    return (
        f"You are the {spec.display_name} on a seven seat research council. "
        f"Your expertise: {', '.join(spec.expertise)}.\n"
        f"{SEAT_INSTRUCTIONS[seat]}\n"
        "Ground every judgment in a retrievable source. Say plainly when the "
        "evidence does not support an answer; an admitted gap is a correct "
        "answer and a confident guess is not.\n"
        f"Current round: {phase.value}. Reply only with the requested schema."
    )


def _user_prompt(seat: Seat, context: PhaseContext) -> str:
    lines = [f"Research question: {context.question}"]
    if context.confirmed_claims:
        joined = ", ".join(str(claim) for claim in context.confirmed_claims)
        lines.append(f"Confirmed atomic claims: {joined}")
    # This seat's own recall only. Handing a seat the whole council's memory
    # would collapse the seven private states CLAUDE.md 3 requires into one.
    private = context.recall.get(seat, "")
    if private:
        lines.append(f"Your private recall: {private}")
    for key in sorted(context.carried):
        if key.lower() in _BIBLIOGRAPHIC_IDENTITY_KEYS:
            # Blind evidence review: never render this, whatever produced it.
            continue
        lines.append(f"{key}: {context.carried[key]!r}")
    return "\n".join(lines)


class GatewayDeliberator:
    """Produces one seat's structured output for one phase, via the gateway.

    Stateless per call. The gateway handles retries, cost accounting, and the
    audit row; this class only decides what to ask and what to do when the
    answer is unusable.
    """

    def __init__(
        self,
        gateway: ModelGateway,
        budget: BudgetTracker | None = None,
    ) -> None:
        self._gateway = gateway
        self._budget = budget

    def _request(self, seat: Seat, phase: TaskPhase, ctx: PhaseContext) -> ModelRequest:
        return ModelRequest(
            task_id=ctx.task_id,
            actor=seat.value,
            purpose=phase.value,
            model_class=PHASE_MODEL_CLASSES.get(phase, ModelClass.MEDIUM),
            messages=(
                ModelMessage(role="system", content=_system_prompt(seat, phase)),
                ModelMessage(role="user", content=_user_prompt(seat, ctx)),
            ),
            output_schema=PHASE_OUTPUT_SCHEMAS.get(phase, "SeatOutput"),
            evidence_refs=ctx.confirmed_claims,
        )

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        if phase not in PHASE_OUTPUT_SCHEMAS:
            return None
        # Built outside the try on purpose. A malformed request is our bug, not a
        # provider outage, and letting it surface as an absent seat would hide a
        # defect behind the same "no answer" the honest-gap path uses.
        request = self._request(seat, phase, context)
        try:
            result = await self._gateway.invoke(request)
        except Exception:
            # A seat that cannot be reached is an absent seat, not a failed task.
            # CLAUDE.md 10 requires the run to degrade rather than abort, and the
            # orchestrator already records the absence on the stream.
            return None

        if self._budget is not None:
            # The spend already happened, so exhaustion is recorded rather than
            # raised: the next phase sees the empty budget and stops, and
            # discarding this answer would throw away work already paid for.
            with suppress(BudgetExhausted):
                self._budget.consume_model_cost(result.cost_usd)

        if result.schema_status is SchemaStatus.QUARANTINED:
            # CLAUDE.md 10: structured output that could not be repaired is
            # quarantined and must not reach the formal graph.
            return None
        return dict(result.payload)


def deliberator_for(
    gateway: ModelGateway | None,
    budget: BudgetTracker | None = None,
) -> GatewayDeliberator | None:
    """Return a deliberator, or None when no provider is configured.

    None is the honest answer for a deployment with no model provider; the
    orchestrator's default then reports every seat as unavailable rather than
    pretending the council met.
    """
    return None if gateway is None else GatewayDeliberator(gateway, budget)


__all__ = [
    "PHASE_MODEL_CLASSES",
    "PHASE_OUTPUT_SCHEMAS",
    "SEAT_INSTRUCTIONS",
    "GatewayDeliberator",
    "deliberator_for",
]
