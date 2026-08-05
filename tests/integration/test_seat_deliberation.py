"""The seat-to-Model-Gateway bridge, exercised through the whole pipeline.

CLAUDE.md 8 requires every model call to go through the gateway, and CLAUDE.md 10
requires every call to be recorded with its latency, cost, and retries. Both are
checked here against the real ``model_calls`` table rather than against a mock,
because an audit trail that only exists in a test double audits nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

import fitz  # type: ignore[import-untyped]
import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import JobResult, _gateway_for_task_config, run_task
from packages.council.contracts import Seat
from packages.council.deliberation import (
    PHASE_OUTPUT_SCHEMAS,
    GatewayDeliberator,
    deliberator_for,
)
from packages.council.rounds.registry import (
    MODEL_REASONING_CAPTURED,
    SEAT_UNAVAILABLE,
    SeatDeliberator,
)
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.models import GraphNodeModel, ScientificEventModel
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import (
    ModelClass,
    ModelGateway,
    ModelRequest,
    ModelResult,
    SchemaStatus,
)
from packages.models.models import ModelCallModel
from packages.papers.object_store import PrivateObjectStore
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED, ResearchRepository
from packages.research.service import ResearchService
from packages.tools.contracts import ToolGateway, ToolRequest, ToolResult
from packages.tools.fulltext_fetcher import FullTextFetcher

QUESTION = "Does adolescent social media use cause depressive symptoms?"
SHARED_COHORT_DOI = "10.1234/shared-cohort"
SHARED_COHORT_QUOTE = (
    "We found a significant association between screen time and anxiety."
)


class _ScriptedGateway:
    """A gateway that answers every phase with usable, deterministic output.

    Deterministic because the pipeline replays: an answer that varied per call
    would turn an idempotency bug into an intermittent one.
    """

    def __init__(
        self,
        claim_id: UUID,
        blindspot_id: UUID,
        schema_status: SchemaStatus = SchemaStatus.OK,
        reasoning: str | None = None,
    ) -> None:
        self.calls: list[ModelRequest] = []
        self._claim_id = claim_id
        self._blindspot_id = blindspot_id
        self._schema_status = schema_status
        # When set, every call pretends the vendor returned this chain of
        # thought (as DeepSeek does in thinking mode) -- for the reasoning
        # event tests, not for the structured-output tests above.
        self._reasoning = reasoning

    def _payload(self, request: ModelRequest) -> dict[str, object]:
        if request.output_schema == "StudyFindingExtraction":
            # A system-level call from FindingExtractor, not one of the seven
            # seats' phase requests -- request.purpose is "finding_extraction",
            # which is not a TaskPhase value, so this must be checked before
            # the TaskPhase(...) conversion below.
            return {
                "study_question": QUESTION,
                "population": "adolescents",
                "design": "cross_sectional",
                "exposure_variable": "screen_time",
                "outcome_variable": "anxiety",
                "analysis_method": "linear regression",
                "finding_statement": "Screen time correlates with anxiety.",
                "origin": "SOURCE_TEXT",
                "effect_direction": "positive",
                "exact_quote": SHARED_COHORT_QUOTE,
                "author_conclusions": ["Screen time matters."],
                "author_limitations": ["Self-reported."],
                "data_availability": "restricted",
                "code_availability": "unavailable",
                "preregistration": "not_reported",
                "method_quality": {
                    "directness": 0.8,
                    "design_quality": 0.75,
                    "measurement_quality": 0.7,
                    "precision": 0.65,
                    "replicability": 0.6,
                    "external_validity": 0.55,
                },
            }
        phase = TaskPhase(request.purpose)
        seat = request.actor
        if phase is TaskPhase.PRECOMMITMENT:
            return {
                "initial_judgment": f"{seat}: correlational support only",
                "confidence": 0.4,
                "update_condition": "a preregistered cohort study",
            }
        if phase is TaskPhase.ACQUISITION:
            # A DOI rather than free text: the adapters resolve DOIs, and a
            # request nobody can resolve is correctly reported as a gap.
            return {"requests": [f"doi {SHARED_COHORT_DOI}"]}
        if phase is TaskPhase.CROSS_EXAMINATION:
            return {
                "challenges": [
                    {
                        "claim_id": str(self._claim_id),
                        "statement": f"{seat} disputes the exposure measure",
                        "is_fatal": False,
                    }
                ]
            }
        if phase is TaskPhase.BLINDSPOT_BOUNTY:
            return {
                "blindspots": [
                    {
                        "id": str(self._blindspot_id),
                        "statement": "Self-reported screen time is misremembered.",
                        "impact": "0.9",
                        "uncertainty": "0.8",
                        "investigability": "0.7",
                        "novelty": "0.6",
                        "normalized_cost": "0.2",
                    }
                ]
            }
        if phase is TaskPhase.JOINT_MODELING:
            return {
                "strongest_opposition_refs": [str(self._claim_id)],
                "falsification_conditions": ["A null effect in a preregistered RCT."],
                "boundary_conditions": ["Western adolescent samples only."],
                "unresolved_conflicts": ["Effect direction across sexes."],
            }
        if phase is TaskPhase.FINAL_REJUDGMENT:
            if seat == Seat.ADVERSARY_FALSIFIER.value:
                # Exactly one dissenting seat, so a full run exercises the
                # DissentCertificate path (CLAUDE.md 16 acceptance criterion
                # 10) without turning every other seat's judgment into a
                # dissent too.
                return {
                    "final_judgment": (
                        f"{seat}: I dissent -- the causal claim overreaches "
                        "the cross-sectional evidence."
                    )
                }
            return {"final_judgment": f"{seat}: narrowed, not withdrawn"}
        return {}

    async def invoke(self, request: ModelRequest) -> ModelResult:
        self.calls.append(request)
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(self._payload(request)),
            input_tokens=100,
            output_tokens=50,
            cost_usd=Decimal("0"),
            latency_ms=12,
            retries=0,
            schema_status=self._schema_status,
            reasoning=self._reasoning,
        )


class _BrokenGateway:
    """Every call fails. A provider outage must degrade, not abort."""

    async def invoke(self, request: ModelRequest) -> ModelResult:
        raise RuntimeError("provider unreachable")


class _StubProvider:
    """A tool gateway that resolves any DOI, so acquisition can succeed.

    Also answers the Unpaywall lookup FindingExtractor makes for each freshly
    acquired source, so the full run can reach Level A rather than stopping at
    the metadata-only Level B that acquisition alone produces.
    """

    async def execute(self, request: ToolRequest) -> ToolResult:
        doi = str(request.arguments["doi"])
        if request.tool_name == "unpaywall":
            return ToolResult(
                call_id=uuid4(),
                payload=FrozenDict(
                    {
                        "url": f"https://example.test/{doi}.pdf",
                        "oa_status": "gold",
                        "oa_version": "publishedVersion",
                    }
                ),
                latency_ms=2,
                retries=0,
                error_code=None,
            )
        return ToolResult(
            call_id=uuid4(),
            payload=FrozenDict(
                {
                    "id": f"https://openalex.org/{doi}",
                    "title": f"Study of {doi}",
                    "authors": ("A. Researcher",),
                    "year": 2021,
                    "type": "journal-article",
                    "retracted": False,
                }
            ),
            latency_ms=2,
            retries=0,
            error_code=None,
        )


def _fake_fulltext_fetcher() -> FullTextFetcher:
    """A FullTextFetcher backed by an in-memory PDF containing the exact quote
    _ScriptedGateway claims, so FindingExtractor's citation check succeeds
    without any real network access."""
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), SHARED_COHORT_QUOTE)
    content = bytes(document.tobytes())

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FullTextFetcher(client=client)


async def _seed_queued_task(
    sessions: async_sessionmaker[AsyncSession],
    model_config: dict[str, object] | None = None,
) -> tuple[UUID, UUID]:
    task_id = uuid4()
    claim_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="deliberation_test",
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=50,
                user_evidence={},
                model_config=model_config,
            )
        )
        await session.flush()
        session.add(
            AtomicClaimModel(
                id=claim_id,
                task_id=task_id,
                statement="Heavy use predicts higher depressive symptom scores.",
                claim_type="correlational",
                scope={"population": "adolescents"},
                falsification_condition="A preregistered cohort finds a null effect.",
                status=CLAIM_CONFIRMED,
                created_by="deliberation_test",
            )
        )
        await session.commit()
    return task_id, claim_id


async def _event_types(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[str]:
    async with sessions() as session:
        result = await session.execute(
            select(ScientificEventModel.event_type).where(
                ScientificEventModel.task_id == task_id
            )
        )
        return list(result.scalars())


async def _run_to_completion(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    *,
    deliberator: SeatDeliberator | None = None,
    gateway: ModelGateway | None = None,
    tools: ToolGateway | None = None,
    fulltext_fetcher: FullTextFetcher | None = None,
    object_store: PrivateObjectStore | None = None,
) -> JobResult:
    """Run a task past the JOINT_MODELING checkpoint to a terminal status.

    Plan phase 8.2 made the BLINDSPOT_BOUNTY -> JOINT_MODELING checkpoint
    unconditional: every task's first ``run_task`` call now halts at
    ``AWAITING_COUNCIL_INPUT`` rather than running all eight phases. These
    tests predate that checkpoint and want the full-protocol outcome, so an
    empty guidance submission stands in for the deliberate "no intervention"
    CLAUDE.md 4/8 requires, exactly as ``test_council_checkpoint_flow.py``
    exercises it -- the second ``run_task`` call resumes from JOINT_MODELING
    and its report aggregates both passes (see
    ``CouncilOrchestrator.run``'s ``resume_from`` handling), so callers can
    keep asserting against the whole run.
    """
    first = await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=deliberator,
        gateway=gateway,
        tools=tools,
        fulltext_fetcher=fulltext_fetcher,
        object_store=object_store,
    )
    if first.run.final_status != TaskStatus.AWAITING_COUNCIL_INPUT:
        return first
    async with app_sessions() as session:
        service = ResearchService(ResearchRepository(session))
        await service.submit_council_guidance(task_id, "")
        await session.commit()
    return await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        deliberator=deliberator,
        gateway=gateway,
        tools=tools,
        fulltext_fetcher=fulltext_fetcher,
        object_store=object_store,
    )


def test_no_gateway_means_no_deliberator() -> None:
    """A deployment with no provider must not silently get a fake one."""
    assert deliberator_for(None) is None
    assert isinstance(deliberator_for(_BrokenGateway()), GatewayDeliberator)


def test_every_deliberating_phase_has_an_output_schema() -> None:
    """A phase with no schema would ask the model for unstructured prose.

    Reporting is excluded on purpose: it reads the graph rather than asking the
    seats anything.
    """
    expected = {phase for phase in TaskPhase if phase is not TaskPhase.REPORTING}
    assert set(PHASE_OUTPUT_SCHEMAS) == expected


async def test_a_full_council_run_reports_no_gaps(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The no-gaps path must be reachable, or COMPLETED_WITH_GAPS means nothing."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_to_completion(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        tools=_StubProvider(),
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )

    assert result.run.unfilled_slots == ()
    assert result.run.absent_seats == frozenset()
    assert result.run.final_status == TaskStatus.COMPLETED
    assert SEAT_UNAVAILABLE not in await _event_types(app_sessions, task_id)

    # _ScriptedGateway's JOINT_MODELING answer already supplies non-empty
    # boundary_conditions/unresolved_conflicts, so CLAUDE.md 5.2's Dialectical
    # Fold must actually have produced a DebateCapsule node on the evidence
    # graph -- not just a CONSENSUS_DRAFTED process event.
    async with app_sessions() as session:
        capsule_nodes = (
            await session.execute(
                select(GraphNodeModel).where(
                    GraphNodeModel.task_id == task_id,
                    GraphNodeModel.node_type
                    == EvidenceNodeType.DEBATE_CAPSULE.value,
                )
            )
        ).scalars().all()
    assert len(capsule_nodes) == 1
    assert capsule_nodes[0].status == "active"

    # _ScriptedGateway's FINAL_REJUDGMENT answer makes exactly one seat
    # (ADVERSARY_FALSIFIER) dissent, so CLAUDE.md 16 acceptance criterion 10
    # must actually have produced a DissentCertificate node on the evidence
    # graph, not just a FINAL_JUDGMENT process event with has_dissent=True.
    async with app_sessions() as session:
        dissent_nodes = (
            await session.execute(
                select(GraphNodeModel).where(
                    GraphNodeModel.task_id == task_id,
                    GraphNodeModel.node_type
                    == EvidenceNodeType.DISSENT_CERTIFICATE.value,
                )
            )
        ).scalars().all()
    assert len(dissent_nodes) == 1
    assert dissent_nodes[0].status == "active"
    assert dissent_nodes[0].payload["author"] == Seat.ADVERSARY_FALSIFIER.value


async def test_every_model_call_is_audited(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 10: latency, cost, and retries are recorded for every call."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    await _run_to_completion(app_sessions, projector_sessions, task_id, gateway=gateway)

    async with app_sessions() as session:
        audited = await session.scalar(
            select(func.count())
            .select_from(ModelCallModel)
            .where(ModelCallModel.task_id == task_id)
        )
    # Seven seats across seven deliberating rounds; reporting asks no one.
    assert len(gateway.calls) == 7 * 7
    assert audited == len(gateway.calls)


async def test_the_seven_seats_are_asked_different_questions(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 3 forbids seven copies of one agent sharing one prompt."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    await run_task(app_sessions, projector_sessions, task_id, gateway=gateway)

    systems = {
        request.actor: request.messages[0].content
        for request in gateway.calls
        if TaskPhase(request.purpose) is TaskPhase.PRECOMMITMENT
    }
    assert set(systems) == {seat.value for seat in Seat}
    assert len(set(systems.values())) == 7


async def test_quarantined_output_never_becomes_a_seat_judgment(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 10: unrepairable structured output is isolated, not admitted."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(
        claim_id, uuid4(), schema_status=SchemaStatus.QUARANTINED
    )

    result = await _run_to_completion(
        app_sessions, projector_sessions, task_id, gateway=gateway
    )

    assert result.run.absent_seats == frozenset(Seat)
    assert result.run.final_status == TaskStatus.COMPLETED_WITH_GAPS
    # The calls still happened and are still audited; only the output was refused.
    assert len(gateway.calls) == 7 * 7


async def test_captured_reasoning_becomes_a_process_only_ledger_event(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 5.1/11: a seat's raw chain of thought is recorded on the
    ledger as process material and must never become evidence-graph input.

    The event carries the seat, the phase, and the vendor's verbatim text; the
    projector's allowlist marks it ``process_only`` and no node of that kind
    appears in ``graph_nodes``.
    """
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(
        claim_id, uuid4(), reasoning="Exposure measurement is self-report, "
        "so reverse causation cannot be excluded."
    )

    await _run_to_completion(
        app_sessions, projector_sessions, task_id, gateway=gateway
    )

    async with app_sessions() as session:
        events = (
            await session.execute(
                select(ScientificEventModel).where(
                    ScientificEventModel.task_id == task_id,
                    ScientificEventModel.event_type == MODEL_REASONING_CAPTURED,
                )
            )
        ).scalars().all()
    assert events, "a reasoning event must be recorded for every seat call"
    assert len(events) == 7 * 7  # seven seats across the seven deliberating rounds
    first = events[0]
    assert first.status == "process_only"
    assert first.payload["seat"] in {seat.value for seat in Seat}
    assert first.payload["phase"] in {phase.value for phase in TaskPhase}
    assert "self-report" in str(first.payload["reasoning"])
    assert first.payload["char_count"] == len(first.payload["reasoning"])

    # The projector saw the event and refused to turn it into a graph node --
    # there is no node whose payload is this reasoning text.
    async with app_sessions() as session:
        graph_nodes = (
            await session.execute(
                select(GraphNodeModel).where(
                    GraphNodeModel.task_id == task_id
                )
            )
        ).scalars().all()
    assert all(
        "self-report" not in str(node.payload.get("reasoning", ""))
        for node in graph_nodes
    )


async def test_task_model_config_builds_a_per_task_gateway(
    monkeypatch: pytest.MonkeyPatch,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A task with its own model configuration runs against the researcher's
    endpoint, not the worker process's gateway -- and the per-task gateway is
    built from exactly what the task row stores."""
    task_id, claim_id = await _seed_queued_task(
        app_sessions,
        model_config={
            "base_url": "https://api.researcher.example",
            "api_key": "sk-researcher-secret",
            "model_name": "my-own-model",
        },
    )
    scripted = _ScriptedGateway(claim_id, uuid4())
    captured: dict[str, object] = {}

    class _PerTaskGateway:
        """Stands in for OpenAICompatibleModelGateway: records the config it
        was built with, then delegates to the scripted answerer."""

        def __init__(self, config: Mapping[str, object]) -> None:
            captured["config"] = dict(config)

        async def invoke(self, request: ModelRequest) -> ModelResult:
            return await scripted.invoke(request)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "apps.worker.jobs._gateway_for_task_config", _PerTaskGateway
    )

    result = await _run_to_completion(
        app_sessions, projector_sessions, task_id, gateway=scripted
    )

    # No tools are configured here, so the run honestly reports the
    # acquisition gaps -- the point is that no round failed and the per-task
    # gateway carried every seat call.
    assert result.run.final_status == TaskStatus.COMPLETED_WITH_GAPS
    assert result.run.failures == ()
    assert captured["config"]["api_key"] == "sk-researcher-secret"
    assert captured["config"]["base_url"] == "https://api.researcher.example"
    # Every seat call went through the per-task gateway (which forwards to the
    # scripted answerer) -- the worker really did run on the task's endpoint.
    assert len(scripted.calls) == 7 * 7


async def test_a_provider_outage_degrades_the_run_instead_of_failing_it(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """CLAUDE.md 10: one seat's failure must not abort the whole task."""
    task_id, _ = await _seed_queued_task(app_sessions)

    result = await _run_to_completion(
        app_sessions, projector_sessions, task_id, gateway=_BrokenGateway()
    )

    assert result.run.failures == ()
    assert result.run.absent_seats == frozenset(Seat)
    assert result.run.final_status == TaskStatus.COMPLETED_WITH_GAPS


async def test_the_prompt_carries_the_question_and_the_confirmed_claims(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A seat asked without the question would be deliberating about nothing."""
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    await run_task(app_sessions, projector_sessions, task_id, gateway=gateway)

    user_messages = [request.messages[1].content for request in gateway.calls]
    assert all(QUESTION in message for message in user_messages)
    assert all(str(claim_id) in message for message in user_messages)
    assert all(claim_id in request.evidence_refs for request in gateway.calls)


async def test_a_seat_is_given_its_own_recall_and_no_one_elses(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Private memory that never reaches a prompt is decoration.

    CLAUDE.md 6 puts process memory in MemoBrain so later rounds can use it, and
    CLAUDE.md 3 keeps it private. Both only mean something at the prompt: the
    later rounds must carry recall, and it must be the asking seat's own.
    """
    task_id, claim_id = await _seed_queued_task(app_sessions)
    gateway = _ScriptedGateway(claim_id, uuid4())

    await _run_to_completion(app_sessions, projector_sessions, task_id, gateway=gateway)

    later = [
        request
        for request in gateway.calls
        if TaskPhase(request.purpose) is TaskPhase.FINAL_REJUDGMENT
    ]
    assert later, "final rejudgment must have asked the seats"
    for request in later:
        prompt = request.messages[1].content
        assert "Your private recall:" in prompt
        # The recall is seeded with the question and grown one line per round,
        # so a seat that had been handed the council's shared memory would show
        # seven times as many phase markers as it ran.
        assert prompt.count(TaskPhase.PRECOMMITMENT.value) <= 2

    first = [
        request
        for request in gateway.calls
        if TaskPhase(request.purpose) is TaskPhase.PRECOMMITMENT
    ]
    assert all(
        "Your private recall:" in request.messages[1].content for request in first
    )


def test_per_task_gateway_resolves_the_model_name_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task's own model_name wins; then the deployment's configured model;
    then the honest default of ``deepseek-chat``."""
    monkeypatch.delenv("POLISCOPE_MODEL_NAME", raising=False)

    gateway = _gateway_for_task_config(
        {"base_url": "https://api.example", "api_key": "k"}
    )
    assert gateway._config.model_names[ModelClass.MEDIUM] == "deepseek-chat"

    monkeypatch.setenv("POLISCOPE_MODEL_NAME", "env-configured-model")
    gateway = _gateway_for_task_config(
        {"base_url": "https://api.example", "api_key": "k"}
    )
    assert gateway._config.model_names[ModelClass.STRONG_REASONING] == "env-configured-model"

    gateway = _gateway_for_task_config(
        {
            "base_url": "https://api.example",
            "api_key": "k",
            "model_name": "researcher-model",
        }
    )
    assert gateway._config.model_names[ModelClass.LIGHTWEIGHT] == "researcher-model"


def test_a_scripted_payload_matches_what_the_rounds_read() -> None:
    """Guards the seam between the prompt schema names and the runners' keys.

    The gateway returns free-form JSON, so nothing but this check stops a
    renamed key from silently producing an empty round.
    """
    gateway = _ScriptedGateway(uuid4(), uuid4())
    seen: dict[TaskPhase, Mapping[str, object]] = {}
    for phase in PHASE_OUTPUT_SCHEMAS:
        request = ModelRequest(
            task_id=uuid4(),
            actor=Seat.THEORY_BUILDER.value,
            purpose=phase.value,
            model_class="medium",
            messages=(),
            output_schema=PHASE_OUTPUT_SCHEMAS[phase],
            evidence_refs=(),
        )
        seen[phase] = gateway._payload(request)

    assert "initial_judgment" in seen[TaskPhase.PRECOMMITMENT]
    assert "requests" in seen[TaskPhase.ACQUISITION]
    assert "challenges" in seen[TaskPhase.CROSS_EXAMINATION]
    assert "blindspots" in seen[TaskPhase.BLINDSPOT_BOUNTY]
    assert "falsification_conditions" in seen[TaskPhase.JOINT_MODELING]
    assert "final_judgment" in seen[TaskPhase.FINAL_REJUDGMENT]
