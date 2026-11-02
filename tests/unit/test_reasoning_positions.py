"""「辩题要给出正反方观点、各自证据与盲点」回归测试。

用户反馈（辩题「“女士优先”有助于/无助于性别平权？」）：最终结论只有一条
按六个环节归档的推理链，读者看不到正方和反方各自是怎么论证的；不同角度
得出不同结论时，也应当分别列出。

本文件锁住：

* 专家终审声明立场（stance），事件里带上它；没有声明时按终审原文的措辞
  推断，并在报告里注明是机械归组；
* 无论模型整合还是机械归档，结论都先给出「正反立场」：正方、反方必定
  出现（无人持有也要写明），每个不同角度各自一条，各带推理、依据与盲点；
* 交叉质询的质询进入目录：它是提出方的论据，也是正方的盲点；
* 立场之后才是逐步推理链，Markdown 导出顺序一致；
* 旧任务的推理图谱在读取时补上立场，不改写账本。
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import (
    FINAL_JUDGMENT,
    PhaseContext,
    run_final_rejudgment,
)
from packages.epistemo.contracts import TaskPhase
from packages.reports.contracts import FinalPaper
from packages.reports.paper_markdown import render_paper_markdown
from packages.reports.reasoning_map import (
    MODE_DETERMINISTIC,
    NO_HOLDER,
    ChallengeRow,
    JudgmentRow,
    build_catalog,
    catalog_prompt_lines,
    deterministic_map,
    infer_stance,
    map_from_payload,
    map_to_payload,
    parse_model_map,
)
from packages.reports.synthesis import _with_positions

QUESTION = "“女士优先”有助于还是无助于性别平权？"

CON_JUDGMENT = (
    "【终审】女士优先无助于性别平权。"
    "它以“女性需要照顾”为前提，属于善意性别歧视，强化了保护—依赖的角色分工。"
)
CONDITIONAL_JUDGMENT = (
    "女士优先是否有助于平权取决于情境：在仪式性场合它只是礼貌，"
    "在资源分配场合它会固化刻板印象。"
)
PRO_JUDGMENT = "女士优先有助于性别平权：它在公共场合补偿了女性承受的安全风险。"


def _judgments() -> tuple[JudgmentRow, ...]:
    return (
        JudgmentRow("measurement_scientist", CON_JUDGMENT, 0.7, stance="negative"),
        JudgmentRow("boundary_scientist", CONDITIONAL_JUDGMENT, 0.6),
        JudgmentRow("theory_builder", PRO_JUDGMENT, 0.4, stance="pro"),
    )


def _challenges() -> tuple[ChallengeRow, ...]:
    return (
        ChallengeRow(
            "adversarial_falsifier",
            "补偿论没有给出女性在公共场合风险更高的数据。",
            claim_statement="女士优先有助于性别平权",
            is_fatal=True,
        ),
        ChallengeRow(
            "measurement_scientist",
            "善意性别歧视量表测的是态度，不是礼让行为本身。",
            claim_statement="女士优先有助于性别平权",
        ),
    )


def _items() -> tuple:
    return build_catalog(
        findings=[{"finding_statement": "善意性别歧视与传统角色认同正相关。"}],
        blindspots=[{"statement": "缺少非西方文化样本。", "score": "0.8"}],
        judgments=_judgments(),
        challenges=_challenges(),
    )


# --- 立场的来源 ----------------------------------------------------------


def test_stance_is_read_from_the_judgment_wording_when_undeclared() -> None:
    assert infer_stance("女士优先无助于性别平权。") == "negative"
    assert infer_stance("该主张不成立。") == "negative"
    assert infer_stance("主张部分成立，只在仪式性场合。") == "conditional"
    assert infer_stance("现有证据不足以判断其方向。") == "undetermined"
    assert infer_stance("女士优先有助于性别平权。") == "affirmative"


async def test_a_declared_stance_travels_on_the_final_judgment_event() -> None:
    outputs: Mapping[Seat, Mapping[str, object]] = {
        Seat.MEASUREMENT_SCIENTIST: {
            "final_judgment": CON_JUDGMENT,
            "stance": "negative",
        },
        Seat.THEORY_BUILDER: {"final_judgment": PRO_JUDGMENT},
    }
    context = PhaseContext(
        task_id=uuid4(),
        phase=TaskPhase.FINAL_REJUDGMENT,
        seats=tuple(outputs),
        question=QUESTION,
        confirmed_claims=(),
        deliberator=_Scripted(outputs),  # type: ignore[arg-type]
    )
    outcome = await run_final_rejudgment(context)
    payloads = {
        event.payload["seat"]: event.payload
        for event in outcome.events
        if event.event_type == FINAL_JUDGMENT
    }
    assert payloads[Seat.MEASUREMENT_SCIENTIST.value]["stance"] == "negative"
    # No stance declared: the payload stays exactly what it was before.
    assert "stance" not in payloads[Seat.THEORY_BUILDER.value]


# --- 机械归档：正反立场 --------------------------------------------------


def test_the_mechanical_map_lays_the_judgments_out_as_a_debate() -> None:
    rmap = deterministic_map(_items())
    positions = {position.stance: position for position in rmap.positions}
    assert [position.stance for position in rmap.positions] == [
        "affirmative",
        "negative",
        "conditional",
    ]
    assert positions["affirmative"].label == "正方"
    assert positions["negative"].label == "反方"
    # Each side's reasoning is its holders' own verdicts, attributed.
    assert positions["negative"].holders == "测量与构念专家"
    assert positions["negative"].reasoning[0].startswith("测量与构念专家：")
    assert "取决于情境" in positions["conditional"].reasoning[0]
    # A challenge is the raiser's argument and the affirmative side's blind spot.
    assert any("量表" in line for line in positions["negative"].evidence)
    assert positions["affirmative"].blindspots[0].startswith("对抗性证伪者质询：")


def test_both_sides_appear_even_when_no_expert_holds_one() -> None:
    items = build_catalog(
        judgments=(JudgmentRow("causal_scientist", CON_JUDGMENT, stance="negative"),)
    )
    positions = {p.stance: p for p in deterministic_map(items).positions}
    assert set(positions) == {"affirmative", "negative"}
    assert positions["affirmative"].holders == NO_HOLDER
    assert positions["affirmative"].reasoning == ()


def test_challenges_enter_the_catalogue_fatal_first() -> None:
    items = _items()
    challenges = [item for item in items if item.kind == "challenge"]
    assert [item.id for item in challenges] == ["Q1", "Q2"]
    assert challenges[0].seat == "adversarial_falsifier"
    assert challenges[0].text.startswith("针对「女士优先有助于性别平权」：")
    prompt = "\n".join(catalog_prompt_lines(items))
    assert "[Q1]" in prompt and "(fatal)" in prompt
    assert "stance negative" in prompt


# --- 模型整合：正反立场 --------------------------------------------------


def _model_payload() -> dict[str, object]:
    return {
        "bottom_line": "现有材料更支持反方：女士优先难以推进实质平权。",
        "steps": [
            {"dimension": "evidence", "question": "证据", "answer": "仅有专家论证。"}
        ],
        "positions": [
            {
                "stance": "conditional",
                "label": "从文化情境看",
                "thesis": "仪式性场合无害，资源分配场合有害。",
                "reasoning": ["区分场合", "比较后果"],
                "refs": ["J2", "X9"],
            },
            {
                "stance": "con",
                "thesis": "女士优先强化保护—依赖分工。",
                "reasoning": [
                    "前提：女性需要照顾",
                    "推论：固化角色",
                    "结论：无助于平权",
                ],
                "evidence": ["善意性别歧视研究（F1）"],
                "blindspots": ["缺少非西方样本（B1）"],
                "holders": "测量与构念专家",
                "refs": ["J1", "F1", "B1"],
            },
        ],
    }


def test_the_model_debate_is_ordered_and_both_sides_are_kept() -> None:
    rmap = parse_model_map(_model_payload(), _items())
    assert [position.stance for position in rmap.positions] == [
        "affirmative",
        "negative",
        "conditional",
    ]
    affirmative, negative, angle = rmap.positions
    # The model left the affirmative out: it is shown, as argued by no one.
    assert affirmative.holders == NO_HOLDER
    assert negative.label == "反方"
    assert negative.reasoning == (
        "前提：女性需要照顾",
        "推论：固化角色",
        "结论：无助于平权",
    )
    assert angle.label == "从文化情境看"
    # Only catalogue ids survive.
    assert angle.refs == ("J2",)


def test_items_cited_by_a_position_are_not_filed_again() -> None:
    rmap = parse_model_map(_model_payload(), _items())
    filed = {ref for step in rmap.steps for ref in step.related}
    assert "F1" not in filed and "B1" not in filed


def test_positions_survive_the_stored_payload_round_trip() -> None:
    rmap = parse_model_map(_model_payload(), _items())
    restored = map_from_payload(map_to_payload(rmap))
    assert restored == rmap


# --- 导出与旧任务 --------------------------------------------------------


def test_markdown_puts_the_debate_before_the_chain() -> None:
    rmap = deterministic_map(_items())
    paper = FinalPaper(
        title=f"关于「{QUESTION}」的研究结论",
        abstract="",
        sections=(),
        references=(),
        limitations=(),
        investigation_process=(),
        reasoning_map=rmap,
    )
    text = render_paper_markdown(paper, uuid4(), QUESTION)
    order = [
        text.index("## 一句话回答"),
        text.index("## 正反立场"),
        text.index("### 正方："),
        text.index("### 反方："),
        text.index("## 推理链"),
    ]
    assert order == sorted(order)
    assert "机械归组" in text
    con = text.index("### 反方：")
    assert con < text.index("**推理**", con) < text.index("**依据**", con)


def test_a_stored_map_without_positions_gets_them_on_read() -> None:
    stored = map_to_payload(deterministic_map(_items()))
    del stored["positions"]
    upgraded = _with_positions({"reasoning_map": stored}, stored)
    rmap = map_from_payload(upgraded["reasoning_map"])
    assert rmap is not None
    assert rmap.positions_derived_on_read is True
    assert rmap.mode == MODE_DETERMINISTIC
    assert {position.stance for position in rmap.positions} >= {
        "affirmative",
        "negative",
    }


def test_a_stored_map_with_positions_is_left_alone() -> None:
    stored = map_to_payload(deterministic_map(_items()))
    payload = {"reasoning_map": stored}
    assert _with_positions(payload, stored) is payload


class _Scripted:
    def __init__(self, outputs: Mapping[Seat, Mapping[str, object]]) -> None:
        self._outputs = outputs
        self.last_error: str | None = None

    async def deliberate(
        self, seat: Seat, phase: TaskPhase, context: PhaseContext
    ) -> Mapping[str, object] | None:
        return self._outputs.get(seat)
