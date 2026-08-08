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

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from uuid import UUID

from packages.council.contracts import Seat
from packages.council.roles import ROLE_SPECS
from packages.council.rounds.registry import PhaseContext
from packages.epistemo.budget import BudgetExhausted, BudgetTracker
from packages.epistemo.contracts import TaskPhase
from packages.evidence.process_stream import ProcessCallback
from packages.models.contracts import (
    ModelClass,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    SchemaStatus,
    StreamEvent,
)

logger = logging.getLogger(__name__)

# A model call that has gone quiet (no reasoning delta, no error) emits a
# ``seat_working`` process event every interval so the live view can show
# "still working, waited N s" instead of an eternal silent "thinking…".
# The front end also keeps its own clock; this is the server-side source.
HEARTBEAT_INTERVAL_SECONDS = 15.0

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


# Per-phase standing instructions, injected into the system prompt ahead of
# "Current round: ...". Only phases whose rounds need a hard behavioural bound
# get an entry (YAGNI); the rest stay empty.
#
# ACQUISITION is the one phase where a seat generates free-form retrieval
# strings, and drift is real: a seat asked for evidence on adolescent
# self-harm once produced requests about power-project bid evaluation and
# nuclear-plant instrument reliability. The round retrieves whatever the
# seat asks for, so relevance has to be enforced where the requests are born.
PHASE_INSTRUCTIONS: dict[TaskPhase, str] = {
    TaskPhase.ACQUISITION: (
        "Evidence-retrieval constraints for this round:\n"
        "1. Every request must bear directly on the research question or a "
        "confirmed atomic claim; you must be able to state in one sentence how "
        "the requested work would bear on the claim.\n"
        "2. Fewer, stronger requests beat many weak ones: 1-3 requests per "
        "claim, never a scatter of tangentially related topics.\n"
        "3. Never drift outside the question's domain. A request from an "
        "unrelated field is invalid even if it shares a keyword.\n"
        "4. Each request must be a usable academic search string: a DOI "
        "(doi:...), an exact title, or a specific search phrase -- not an "
        "essay, not a research agenda, and no justification sentence attached "
        "to a DOI.\n"
        "5. An empty requests list is correct when you need no further "
        "evidence; never invent requests to fill the schema.\n"
    ),
}


# The language directive injected into every seat's system prompt (round-4
# language following): a Chinese question must be answered in Chinese, an
# English one in English. Deliberately part of the *system* prompt so it
# outranks any instruction the seat's own text might imply, and phrased as a
# hard MUST because extraction phases default to English otherwise.
OUTPUT_LANGUAGE_DIRECTIVES: dict[str, str] = {
    "zh-Hans": (
        "Output language: Simplified Chinese (简体中文). The researcher asked "
        "in Chinese, so you MUST write your reasoning, structured outputs, "
        "review text, and every judgment in Simplified Chinese."
    ),
    "zh-Hant": (
        "Output language: Traditional Chinese (繁體中文). The researcher asked "
        "in Traditional Chinese, so you MUST write your reasoning, structured "
        "outputs, review text, and every judgment in Traditional Chinese."
    ),
    "en": (
        "Output language: English. The researcher asked in English, so you "
        "MUST write your reasoning, structured outputs, review text, and "
        "every judgment in English."
    ),
}


def _system_prompt(seat: Seat, phase: TaskPhase, output_language: str = "en") -> str:
    spec = ROLE_SPECS[seat]
    directive = OUTPUT_LANGUAGE_DIRECTIVES.get(output_language, OUTPUT_LANGUAGE_DIRECTIVES["en"])  # noqa: E501
    phase_instruction = PHASE_INSTRUCTIONS.get(phase, "")
    return (
        f"You are the {spec.display_name} on a seven seat research council. "
        f"Your expertise: {', '.join(spec.expertise)}.\n"
        f"{SEAT_INSTRUCTIONS[seat]}\n"
        "Ground every judgment in a retrievable source. Say plainly when the "
        "evidence does not support an answer; an admitted gap is a correct "
        "answer and a confident guess is not.\n"
        f"{directive}\n"
        f"{phase_instruction}"
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
        if key == "knowledge_base_context":
            # Rendered with its own dedicated block below -- the generic
            # {key}: {value!r} line would dump raw dicts into the prompt.
            continue
        lines.append(f"{key}: {context.carried[key]!r}")
    # Knowledge-base retrieval hits, carried forward from the acquisition
    # round (registry's run_acquisition). Explicitly labelled as non-evidence
    # process context -- the researcher's own documents may suggest what to
    # look at, but a hit list is not a citation, and the Evidence Gate never
    # reads it (it lives in `carried`, not in any event payload). The value
    # is a tuple of dicts, or of FrozenDicts once a checkpoint has frozen it;
    # both are Mappings, so one rendering path covers both shapes.
    kb_hits = context.carried.get("knowledge_base_context")
    if isinstance(kb_hits, (list, tuple)):
        for raw in kb_hits:
            if not isinstance(raw, Mapping):
                continue
            title = str(raw.get("document_title", "?"))
            snippet = str(raw.get("snippet", ""))
            if snippet:
                lines.append(
                    f"【研究者知识库检索命中（非正式证据，来源文档：{title}）】"
                    f"{snippet}"
                )
    # The researcher's enabled skills, resolved by the worker from the task's
    # skill_ids. A skill is an instruction, not a citation: it tells the
    # scientists what methods or lenses the researcher wants applied, so it is
    # labelled explicitly as non-evidence process context -- it never supports
    # or refutes a claim, and the Evidence Gate never reads it (it lives on
    # the PhaseContext, not in any event payload).
    for name, markdown in context.researcher_skills:
        if markdown.strip():
            lines.append(
                f"【研究者提供的技能指令（非正式证据，来源：{name}）】{markdown}"
            )
    # Round-7 paper-review tasks: the machine's reading of the uploaded paper
    # (see packages/papers/understanding.py), injected so the seven seats
    # critique the paper's actual claims and evidence. Explicitly labelled as
    # non-evidence process context, exactly like the knowledge-base hits and
    # skills above: the paper's own extracted text is the Level A evidence
    # (acquired via acquire_uploaded), this summary only orients the seats.
    if context.paper_understanding:
        understanding = context.paper_understanding
        lines.append(
            "【论文理解（研究者上传论文的机器摘要，非正式证据；"
            "论文全文已按 Level A 进入证据图）】"
        )
        paper_title = understanding.get("title")
        if isinstance(paper_title, str) and paper_title:
            lines.append(f"标题：{paper_title}")
        paper_question = understanding.get("research_question")
        if isinstance(paper_question, str) and paper_question:
            lines.append(f"论文研究问题：{paper_question}")
        main_claims = understanding.get("main_claims")
        if isinstance(main_claims, (list, tuple)):
            for claim in main_claims:
                if not isinstance(claim, Mapping):
                    continue
                statement = str(claim.get("statement", "?"))
                support = claim.get("supporting_evidence")
                if isinstance(support, (list, tuple)) and support:
                    evidence = "；".join(str(item) for item in support)
                    lines.append(f"- 观点：{statement}（论文佐证：{evidence}）")
                else:
                    lines.append(f"- 观点：{statement}（论文未提供可辨识佐证）")
        unverifiable = understanding.get("unverifiable")
        if isinstance(unverifiable, (list, tuple)) and unverifiable:
            lines.append(
                "无法从文本核验的部分：" + "；".join(str(item) for item in unverifiable)
            )
        if understanding.get("truncated") is True:
            lines.append("（注意：上传文本过长已被截断，分析可能未覆盖全文）")
    # Plan phase 8.3: the human's advisory steer from the BLINDSPOT_BOUNTY ->
    # JOINT_MODELING checkpoint, rendered only in this one phase and clearly
    # labelled as non-scientific. CLAUDE.md 4/8 forbid a human vote from
    # deciding scientific truth, so this line must never be mistaken for an
    # eighth seat's judgment, and it is never written into `carried`/
    # `outcome.carry`, so it cannot leak into any later phase's prompt either.
    if context.phase is TaskPhase.JOINT_MODELING and context.guidance:
        lines.append(f"[研究者方向性备注，非科学判断]: {context.guidance}")
    return "\n".join(lines)


def generic_system_prompt(
    seat: Seat, phase: TaskPhase, output_language: str = "en"
) -> str:
    """One undifferentiated researcher voice, with no seat identity at all.

    Used by :func:`packages.evaluation.harness.generic_debate_deliberator` to
    build the "Fixed Multi-Agent Debate" baseline from design spec 11.3: several
    copies of the same generic agent debating, rather than seven role-specialised
    seats. Deliberately does not read ``ROLE_SPECS`` or ``SEAT_INSTRUCTIONS`` --
    the whole point of this baseline is the *absence* of CLAUDE.md 3's per-seat
    specialization, so every seat must receive an identical prompt. The
    language directive is still applied (same language-following contract as
    the real council), via the shared directive table.
    """
    directive = OUTPUT_LANGUAGE_DIRECTIVES.get(
        output_language, OUTPUT_LANGUAGE_DIRECTIVES["en"]
    )
    phase_instruction = PHASE_INSTRUCTIONS.get(phase, "")
    return (
        "You are a research assistant debating a contested empirical question "
        "alongside several other copies of yourself. No individual area of "
        "expertise is assigned to you; weigh the question from every angle.\n"
        "Ground every judgment in a retrievable source. Say plainly when the "
        "evidence does not support an answer; an admitted gap is a correct "
        "answer and a confident guess is not.\n"
        f"{directive}\n"
        f"{phase_instruction}"
        f"Current round: {phase.value}. Reply only with the requested schema."
    )


SystemPromptBuilder = Callable[[Seat, TaskPhase, str], str]
UserPromptBuilder = Callable[[Seat, PhaseContext], str]

# Receives a seat's raw chain of thought once a model call captured one, so
# the caller can surface it (the ledger's process-only MODEL_REASONING_CAPTURED
# event). Optional: evaluation baselines and tests can omit it.
ReasoningCallback = Callable[[UUID, Seat, TaskPhase, str], Awaitable[None]]


class GatewayDeliberator:
    """Produces one seat's structured output for one phase, via the gateway.

    Stateless per call. The gateway handles retries, cost accounting, and the
    audit row; this class only decides what to ask and what to do when the
    answer is unusable.

    ``system_prompt``/``user_prompt`` default to the real council's per-seat
    specialised prompts. They are injectable so the evaluation harness can reuse
    every other piece of this class -- request shaping, budget consumption,
    schema-quarantine handling -- for baselines that must NOT specialise by
    seat, instead of re-implementing that machinery a second time.
    """

    def __init__(
        self,
        gateway: ModelGateway,
        budget: BudgetTracker | None = None,
        *,
        system_prompt: SystemPromptBuilder = _system_prompt,
        user_prompt: UserPromptBuilder = _user_prompt,
        on_reasoning: ReasoningCallback | None = None,
        on_process: ProcessCallback | None = None,
        on_flush: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._gateway = gateway
        self._budget = budget
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt
        self._on_reasoning = on_reasoning
        self._on_process = on_process
        self._on_flush = on_flush
        # Tokens since the last flush of the process stream: the relay batches
        # so the live view does not pay a database write per token.
        self._since_flush = 0
        # Why the most recent deliberate() call came back None. Read by the
        # round collector so a SEAT_UNAVAILABLE event reports the real reason
        # (provider outage, 401, quarantined schema) instead of a fixed
        # "no model provider" string that is wrong half the time.
        self.last_error: str | None = None

    def _request(self, seat: Seat, phase: TaskPhase, ctx: PhaseContext) -> ModelRequest:
        return ModelRequest(
            task_id=ctx.task_id,
            actor=seat.value,
            purpose=phase.value,
            model_class=PHASE_MODEL_CLASSES.get(phase, ModelClass.MEDIUM),
            messages=(
                ModelMessage(
                    role="system",
                    content=self._system_prompt(seat, phase, ctx.output_language),
                ),
                ModelMessage(role="user", content=self._user_prompt(seat, ctx)),
            ),
            output_schema=PHASE_OUTPUT_SCHEMAS.get(phase, "SeatOutput"),
            evidence_refs=ctx.confirmed_claims,
        )

    async def _relay(
        self,
        event: StreamEvent,
        seat: Seat,
        phase: TaskPhase,
    ) -> None:
        """Relay one streamed delta to the process writer, batched.

        ``kind`` maps onto the live view's vocabulary: reasoning deltas are
        the vendor's chain of thought, token deltas the structured answer
        taking shape. Every ~40 deltas the writer is flushed so the browser
        sees progress roughly as it happens, not all at the end.

        Every row carries the seat and phase it belongs to. The live view
        attributes a thinking slice to its scientist by these fields --
        without them the deltas are un-attributable noise and every seat
        renders as "no output yet" even while the model is streaming.
        """
        if self._on_process is None:
            return
        if event.kind == "done":
            self._on_process(
                "model_done",
                {"seat": seat.value, "phase": phase.value},
            )
            return
        if not event.text:
            return
        self._on_process(
            "model_reasoning" if event.kind == "reasoning" else "model_token",
            {
                "text": event.text,
                "seat": seat.value,
                "phase": phase.value,
            },
        )
        self._since_flush += 1
        if self._since_flush >= 40 and self._on_flush is not None:
            self._since_flush = 0
            try:
                await self._on_flush()
            except Exception:
                logger.warning(
                    "process stream flush failed mid-stream",
                    exc_info=True,
                )

    def _start_heartbeat(
        self,
        seat: Seat,
        phase: TaskPhase,
    ) -> asyncio.Task[None] | None:
        """Begin periodic ``seat_working`` events for an in-flight model call.

        ``None`` when no process sink is wired (tests, one-shot runs), which
        keeps the heartbeat purely a live-view feature.
        """
        if self._on_process is None:
            return None
        return asyncio.create_task(self._heartbeat(seat, phase))

    async def _stop_heartbeat(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _heartbeat(self, seat: Seat, phase: TaskPhase) -> None:
        started = time.monotonic()
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            elapsed = int(time.monotonic() - started)
            assert self._on_process is not None
            self._on_process(
                "seat_working",
                {"seat": seat.value, "phase": phase.value, "elapsed": elapsed},
            )

    async def deliberate(
        self,
        seat: Seat,
        phase: TaskPhase,
        context: PhaseContext,
    ) -> Mapping[str, object] | None:
        self.last_error = None
        if phase not in PHASE_OUTPUT_SCHEMAS:
            self.last_error = f"no output schema for phase {phase.value}"
            return None
        # Built outside the try on purpose. A malformed request is our bug, not a
        # provider outage, and letting it surface as an absent seat would hide a
        # defect behind the same "no answer" the honest-gap path uses.
        request = self._request(seat, phase, context)
        if self._on_process is not None:
            # The live view anchors on stage transitions, so a seat's turn is
            # announced before the model starts, not after it answers.
            self._on_process(
                "seat_deliberation",
                {"seat": seat.value, "phase": phase.value},
            )
        heartbeat: asyncio.Task[None] | None = None
        try:
            heartbeat = self._start_heartbeat(seat, phase)
            if self._on_process is not None:
                # Streaming is an optimisation over invoke, never a
                # replacement: a streamed attempt that fails anywhere (vendor
                # hiccup, schema rejection) falls back to the non-streaming
                # call, which owns retries and repair. The deltas already
                # relayed remain on the wire as an honest partial trace.
                streaming = getattr(self._gateway, "stream_invoke", None)
                if streaming is not None:
                    async def relay_for_seat(event: StreamEvent) -> None:
                        await self._relay(event, seat, phase)

                    try:
                        result = await streaming(request, relay_for_seat)
                    except Exception:
                        logger.warning(
                            "streaming call failed for %s/%s, falling back "
                            "to non-streaming invoke",
                            seat.value,
                            phase.value,
                            exc_info=True,
                        )
                        result = await self._gateway.invoke(request)
                        # The fallback succeeded but never streamed a
                        # ``model_done`` (that event is emitted only by the
                        # stream's success path), so the live view would keep
                        # this seat on "thinking…" forever. Emit the same
                        # event the stream would have sent (see _relay).
                        if self._on_process is not None:
                            self._on_process(
                                "model_done",
                                {"seat": seat.value, "phase": phase.value},
                            )
                    finally:
                        self._since_flush = 0
                else:
                    result = await self._gateway.invoke(request)
            else:
                result = await self._gateway.invoke(request)
        except Exception as error:
            # A seat that cannot be reached is an absent seat, not a failed task.
            # CLAUDE.md 10 requires the run to degrade rather than abort, and the
            # orchestrator already records the absence on the stream. The reason
            # is kept (bounded) so the absence event says what actually broke --
            # a wrong base_url must show up as a connection error, not as the
            # generic "no model provider" message.
            self.last_error = str(error)[:300] or error.__class__.__name__
            return None
        finally:
            await self._stop_heartbeat(heartbeat)

        if self._budget is not None:
            # The spend already happened, so exhaustion is recorded rather than
            # raised: the next phase sees the empty budget and stops, and
            # discarding this answer would throw away work already paid for.
            with suppress(BudgetExhausted):
                self._budget.consume_model_cost(result.cost_usd)

        if self._on_reasoning is not None and result.reasoning:
            # The chain of thought is auxiliary process material. Recorded
            # before the quarantine check on purpose: a quarantined result
            # (schema repair failed) still had real thinking behind it, and
            # showing that thinking is honest. A failure to record it must not
            # sink the seat's structured output or fail the round.
            try:
                await self._on_reasoning(
                    context.task_id, seat, phase, result.reasoning
                )
            except Exception:
                logger.warning(
                    "failed to record reasoning for %s/%s/%s",
                    context.task_id,
                    seat,
                    phase,
                    exc_info=True,
                )

        if result.schema_status is SchemaStatus.QUARANTINED:
            # CLAUDE.md 10: structured output that could not be repaired is
            # quarantined and must not reach the formal graph.
            self.last_error = "structured output quarantined after schema repair failed"
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
    "PHASE_INSTRUCTIONS",
    "PHASE_MODEL_CLASSES",
    "PHASE_OUTPUT_SCHEMAS",
    "SEAT_INSTRUCTIONS",
    "GatewayDeliberator",
    "SystemPromptBuilder",
    "UserPromptBuilder",
    "deliberator_for",
    "generic_system_prompt",
]
