"""Output-language detection and prompt injection (round-4 language following).

A researcher who asks in Chinese must get Chinese reasoning, judgments, and
reports back; an English question, English. Detection is a simple CJK heuristic
(no new dependency); the directive is injected into every seat's system prompt
so it outranks the seat's own English default.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.deliberation import OUTPUT_LANGUAGE_DIRECTIVES, _system_prompt
from packages.council.rounds.registry import PhaseContext
from packages.epistemo.contracts import TaskPhase
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus
from packages.research.language import detect_output_language

SIMPLIFIED = "中国大陆地区青少年自杀率和学习成绩是否具有显著关系？"
TRADITIONAL = "中國大陸地區青少年自殺率與學習成績是否具有顯著關係？"
ENGLISH = "Does adolescent social media use cause depressive symptoms?"
MIXED = "研究 screen time 和 depression 的关系"


def test_detect_simplified_chinese() -> None:
    assert detect_output_language(SIMPLIFIED) == "zh-Hans"


def test_detect_traditional_chinese() -> None:
    assert detect_output_language(TRADITIONAL) == "zh-Hant"


def test_detect_english() -> None:
    assert detect_output_language(ENGLISH) == "en"


def test_detect_mixed_question_is_chinese() -> None:
    # A question that mixes English terms into a Chinese sentence still gets
    # answered in Chinese: that is the language the researcher wrote in.
    assert detect_output_language(MIXED) == "zh-Hans"


def test_detect_empty_and_ascii_only() -> None:
    assert detect_output_language("") == "en"
    assert detect_output_language("123 abc !?") == "en"


def test_language_directive_is_injected_into_system_prompt() -> None:
    prompt = _system_prompt(Seat.THEORY_BUILDER, TaskPhase.PRECOMMITMENT, "zh-Hans")
    assert "Simplified Chinese" in prompt
    assert "MUST" in prompt

    prompt_en = _system_prompt(Seat.THEORY_BUILDER, TaskPhase.PRECOMMITMENT, "en")
    assert "Output language: English" in prompt_en
    assert "Simplified Chinese" not in prompt_en


def test_unknown_language_falls_back_to_english() -> None:
    prompt = _system_prompt(Seat.THEORY_BUILDER, TaskPhase.PRECOMMITMENT, "fr")
    assert "Output language: English" in prompt


def test_acquisition_phase_injects_retrieval_boundaries() -> None:
    """Round-5 relevance governance: a seat generates free-form retrieval
    strings in ACQUISITION and once drifted into unrelated fields (power
    projects, nuclear instrumentation) for an adolescent mental-health
    question. The phase instruction must be present in exactly that phase's
    system prompt, and nowhere else."""
    acquisition = _system_prompt(Seat.THEORY_BUILDER, TaskPhase.ACQUISITION)
    assert "Evidence-retrieval constraints for this round" in acquisition
    assert "Never drift outside the question's domain" in acquisition
    assert "Fewer, stronger requests beat many weak ones" in acquisition
    # And not in any other phase.
    for phase in (
        TaskPhase.PRECOMMITMENT,
        TaskPhase.CROSS_EXAMINATION,
        TaskPhase.FINAL_REJUDGMENT,
    ):
        prompt = _system_prompt(Seat.THEORY_BUILDER, phase)
        assert "Evidence-retrieval constraints" not in prompt


def test_generic_baseline_prompt_gets_the_same_boundary() -> None:
    """The Fixed Multi-Agent Debate baseline must obey the same retrieval
    boundary or the ablation would be confounded by relevance drift."""
    from packages.council.deliberation import generic_system_prompt

    acquisition = generic_system_prompt(
        Seat.THEORY_BUILDER, TaskPhase.ACQUISITION
    )
    assert "Evidence-retrieval constraints for this round" in acquisition
    precommitment = generic_system_prompt(
        Seat.THEORY_BUILDER, TaskPhase.PRECOMMITMENT
    )
    assert "Evidence-retrieval constraints" not in precommitment


class _CapturingDeliberator:
    """Records the system prompts it was asked with (integration-style check)."""

    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    async def invoke(self, request: ModelRequest) -> ModelResult:
        self.system_prompts.append(
            next(
                m.content
                for m in request.messages
                if m.role == "system"
            )
        )
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict({"requests": ["10.1234/example"]}),
            input_tokens=10,
            output_tokens=5,
            cost_usd=Decimal("0"),
            latency_ms=1,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


async def test_deliberator_passes_output_language_into_prompt() -> None:
    """The PhaseContext's language reaches the system prompt verbatim."""
    from packages.council.deliberation import GatewayDeliberator

    gateway = _CapturingDeliberator()
    deliberator = GatewayDeliberator(gateway)
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.ACQUISITION,
        seats=(Seat.THEORY_BUILDER,),
        question=SIMPLIFIED,
        confirmed_claims=(),
        deliberator=deliberator,
        carried={},
        recall={},
        output_language="zh-Hans",
    )

    result = await deliberator.deliberate(Seat.THEORY_BUILDER, TaskPhase.ACQUISITION, context)  # noqa: E501

    assert result is not None
    assert gateway.system_prompts
    assert "Simplified Chinese" in gateway.system_prompts[0]


def test_directives_cover_all_supported_languages() -> None:
    assert set(OUTPUT_LANGUAGE_DIRECTIVES) == {"zh-Hans", "zh-Hant", "en"}
