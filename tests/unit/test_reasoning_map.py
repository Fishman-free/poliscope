"""Unit tests for the reasoning-map summary (packages/reports/reasoning_map.py).

The map exists because the summary used to print the council's outputs as
parallel lists -- dozens of blindspots, boundaries and conflicts with nothing
tying one to the conclusion it threatens. These tests pin the properties that
fix it: every item is filed under one step of a fixed reasoning spine, the
model can cite only catalogue ids that exist, nothing is silently dropped,
and the Markdown export keeps each conclusion beside its limits.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.reports.contracts import FinalPaper, SynthesisOutcome
from packages.reports.paper_markdown import render_paper_markdown
from packages.reports.reasoning_map import (
    DIMENSION_KEYS,
    MODE_DETERMINISTIC,
    MODE_MODEL,
    JudgmentRow,
    build_catalog,
    catalog_prompt_lines,
    classify,
    deterministic_map,
    headline_of,
    map_from_payload,
    map_to_payload,
    parse_model_map,
    verdict_sentence,
)
from packages.reports.service import BriefNode, ResearchBrief
from packages.reports.synthesis import (
    SYNTHESIS_DONE,
    SYNTHESIS_STARTED,
    _build_map_prompt,
    _fallback_integrated_paper,
    _paper_payload_dict,
    _SynthesisProgress,
    paper_payload_to_dataclass,
)
from packages.research.repository import CLAIM_CONFIRMED, StoredClaim

CAUSAL_JUDGMENT = (
    "【本席（因果与识别方向）最终重判】\n\n一、对两条登记主张的裁定\n"
    "- 因果主张：不成立，应予阻断。反对理由按强度排序：命题层级与证据层级不匹配。"
)
MEASUREMENT_JUDGMENT = (
    "本席以测量科学家身份裁定：**【因果主张】不成立——证据不支持；"
    "【关联主张】仅以弱化版本存活。**\n\n一、构念与操作化的分离"
)


def _judgments() -> tuple[JudgmentRow, ...]:
    return (
        JudgmentRow("causal_scientist", CAUSAL_JUDGMENT, 0.7, has_dissent=True),
        JudgmentRow("measurement_scientist", MEASUREMENT_JUDGMENT, 0.78),
    )


def _consensus() -> dict[str, object]:
    return {
        "conditional_consensus": (
            "综合结论以 2 项已有主张为前提；其适用范围受 2 项边界条件约束"
        ),
        "boundary_conditions": [
            "测量与操作化边界：暴露端为自评单题时长，其信度与效度未被报告。",
            "人群与地理边界：负向信号主要来自英语国家队列，集中于青春期女生。",
        ],
        "unresolved_conflicts": [
            "【自评 vs 客观日志的分歧无法归因】若客观日志下关联消失，"
            "现有证据无法区分两种解释。判定需要 MTMM 式多方法设计。",
        ],
        "falsification_conditions": [
            "【证伪『遗传混杂』】双生子设计中时长与症状的关联在同卵组内消失。",
        ],
    }


def _catalog() -> tuple:
    return build_catalog(
        findings=[
            {"finding_statement": "一项纵向队列研究显示弱相关。", "doi": "10.1/a"}
        ],
        blindspots=[
            {
                "statement": "【发表偏倚从未被诊断】大样本极小效应区间最易制造伪显著。",
                "score": "0.70",
            },
            {"statement": "边界缺陷：登记主张未编码性别边界。", "score": "0.74"},
        ],
        # A dissent certificate restates its author's judgment verbatim.
        dissents=[{"statement": CAUSAL_JUDGMENT, "author": "causal_scientist"}],
        consensus=_consensus(),
        judgments=_judgments(),
    )


# ------------------------------------------------------------------ text


def test_headline_keeps_a_self_explanatory_tag() -> None:
    text = "【缺经验基线：r≈0.04 未与『环境相关』比较】整场争论在反复裁定效应是否小。"
    assert headline_of(text) == "缺经验基线：r≈0.04 未与『环境相关』比较"


def test_headline_extends_a_bare_label_tag_with_its_first_sentence() -> None:
    text = "【方法学声明】本席无检索工具；结论均须核验。后文略。"
    headline = headline_of(text)
    assert headline.startswith("方法学声明：")
    assert "须核验" in headline
    # Seat jargon is softened for the reader; the full text keeps it.
    assert "本席" not in headline


def test_verdict_sentence_skips_headings_and_finds_the_verdict() -> None:
    assert verdict_sentence(CAUSAL_JUDGMENT).startswith("因果主张：不成立")
    assert "弱化版本存活" in verdict_sentence(MEASUREMENT_JUDGMENT)


def test_verdict_sentence_joins_a_sentence_ending_on_a_colon() -> None:
    text = (
        "【边界科学家·终审判断】\n"
        "本席对该问句给出否定性可回答性判定：\n1) 因果主张——不成立。"
    )
    assert verdict_sentence(text).endswith("因果主张——不成立。")


# ------------------------------------------------------------- catalogue


def test_catalog_numbers_by_kind_ranks_blindspots_and_drops_restatements() -> None:
    items = _catalog()
    ids = [item.id for item in items]
    assert ids[:2] == ["J1", "J2"]
    assert "F1" in ids and "L2" in ids and "C1" in ids and "T1" in ids
    blindspots = [item for item in items if item.kind == "blindspot"]
    # Highest bounty score first.
    assert blindspots[0].score == "0.74"
    # The dissent certificate repeats J1 word for word -> one item, not two.
    assert not [item for item in items if item.kind == "dissent"]
    assert items[0].has_dissent is True


def test_classify_files_by_named_dimension_then_by_kind() -> None:
    items = {item.id: item for item in _catalog()}
    assert classify(items["L1"]) == "measurement"  # 自评 / 口径
    assert classify(items["L2"]) == "boundary"
    assert classify(items["T1"]) == "causality"  # 遗传混杂
    assert classify(items["F1"]) in DIMENSION_KEYS


def test_prompt_lines_carry_ids_and_headlines_not_full_texts() -> None:
    items = _catalog()
    prompt = "\n".join(catalog_prompt_lines(items))
    for item in items:
        assert f"[{item.id}]" in prompt
    # Headlines only: C1's second sentence stays out of the prompt.
    assert "MTMM" not in prompt


# ------------------------------------------------------ deterministic map


def _placements(rmap) -> list[str]:
    placed: list[str] = list(rmap.unassigned)
    for step in rmap.steps:
        placed.extend(step.refs)
        placed.extend(step.related)
    return placed


def test_deterministic_map_files_every_item_exactly_once() -> None:
    items = _catalog()
    rmap = deterministic_map(items)
    placed = _placements(rmap)
    non_judgment = [item.id for item in items if item.kind != "judgment"]
    for item_id in non_judgment:
        assert placed.count(item_id) == 1, item_id
    assert rmap.mode == MODE_DETERMINISTIC
    # Steps follow the spine.
    order = [DIMENSION_KEYS.index(step.dimension) for step in rmap.steps]
    assert order == sorted(order)


def test_deterministic_answers_are_attributed_and_keep_dissent() -> None:
    rmap = deterministic_map(_catalog())
    causal = next(step for step in rmap.steps if step.dimension == "causality")
    assert causal.answer.startswith("因果推断专家：")
    assert "（保留异议）" in causal.answer
    # The doubt hangs under the conclusion it threatens.
    measurement = next(step for step in rmap.steps if step.dimension == "measurement")
    assert any("自评" in line for line in measurement.limits)


def test_deterministic_map_never_promotes_the_template_consensus() -> None:
    rmap = deterministic_map(
        _catalog(),
        bottom_line="综合结论以 2 项已有主张为前提；目前仍有 3 处分歧尚未解决",
    )
    assert rmap.bottom_line == ""
    real = deterministic_map(_catalog(), bottom_line="证据指向弱相关，因果方向未决。")
    assert real.bottom_line == "证据指向弱相关，因果方向未决。"


# -------------------------------------------------------------- model map


def _model_payload() -> dict[str, object]:
    return {
        "bottom_line": "现有证据不支持因果表述，只剩极小、未校正的负向关联。",
        "verdicts": [
            {
                "claim": "因果主张",
                "verdict": "not_supported",
                "reason": "无外生识别。",
                "refs": ["J1"],
            },
            {
                "claim": "关联主张",
                "verdict": "PARTIALLY SUPPORTED",
                "reason": "效应极小。",
            },
        ],
        "steps": [
            {
                "dimension": "causality",
                "question": "时长能否被读成原因？",
                "answer": "不能：个体间相关回答不了个体内问题。",
                "unknowns": ["遗传混杂未被排除"],
                "refs": ["J1", "T1", "Z99"],
            },
            {
                "dimension": "measurement",
                "question": "测到的是使用本身吗？",
                "answer": "主要是自评时长。",
                "limits": ["仅限自评时长"],
                "refs": ["l1"],
            },
        ],
        "cruxes": [
            {
                "question": "日志下关联是否消失？",
                "positions": [
                    {"holders": "测量与构念专家", "view": "关联多由自评生成"}
                ],
                "decider": "预注册的日志 + 量表研究",
                "refs": ["C1"],
            }
        ],
        "next_steps": [{"action": "做发表偏倚诊断", "refs": ["B2"]}],
    }


def test_model_map_drops_unknown_refs_and_orders_steps_by_spine() -> None:
    items = _catalog()
    rmap = parse_model_map(_model_payload(), items)
    assert rmap.mode == MODE_MODEL
    assert [step.dimension for step in rmap.steps] == ["measurement", "causality"]
    causal = rmap.steps[1]
    assert "Z99" not in causal.refs
    assert causal.refs == ("J1", "T1")
    # Refs are normalised ("l1" -> "L1").
    assert rmap.steps[0].refs == ("L1",)
    assert rmap.verdicts[1].verdict == "partially_supported"


def test_model_map_files_uncited_items_so_nothing_is_dropped() -> None:
    items = _catalog()
    rmap = parse_model_map(_model_payload(), items)
    cited = {"J1", "T1", "L1", "C1", "B2"}
    placed = set(_placements(rmap))
    for item in items:
        if item.kind == "judgment" or item.id in cited:
            continue
        assert item.id in placed, item.id


@pytest.mark.parametrize(
    "broken",
    [
        {"steps": [{"dimension": "causality", "question": "q", "answer": "a"}]},
        {"bottom_line": "x", "steps": [{"dimension": "causality", "question": "q"}]},
    ],
)
def test_model_map_without_bottom_line_or_steps_raises(
    broken: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        parse_model_map(broken, _catalog())


def test_map_payload_round_trips() -> None:
    rmap = parse_model_map(_model_payload(), _catalog())
    restored = map_from_payload(map_to_payload(rmap))
    assert restored == rmap
    assert map_from_payload("not a map") is None


# ------------------------------------------------------ paper and export


def _brief(question: str = "社交媒体使用时长是否降低青少年心理健康？") -> ResearchBrief:
    placeholder = StoredClaim(
        claim_id=uuid4(),
        statement=f"因果主张：{question}",
        claim_type="causal",
        scope={},
        falsification_condition="随机实验无效应",
        status=CLAIM_CONFIRMED,
    )
    real = StoredClaim(
        claim_id=uuid4(),
        statement="使用时长与抑郁症状呈弱正相关",
        claim_type="correlational",
        scope={},
        falsification_condition="无显著相关",
        status=CLAIM_CONFIRMED,
    )
    return ResearchBrief(
        task_id=uuid4(),
        question=question,
        status="COMPLETED",
        confirmed_claims=(placeholder, real),
        findings=(
            BriefNode(
                node_id=uuid4(),
                node_type="StudyFinding",
                status="active",
                payload={
                    "finding_statement": "一项纵向队列研究显示弱相关。",
                    "doi": "10.1/a",
                },
            ),
        ),
        paper_count=3,
        independent_cluster_count=2,
        limitations=("模型置信度不等于统计不确定性。",),
    )


def test_fallback_paper_is_a_deterministic_map_with_derived_prose() -> None:
    brief = _brief()
    paper = _fallback_integrated_paper(
        brief, _consensus(), brief.question, _judgments()
    )
    assert isinstance(paper, FinalPaper)
    assert paper.reasoning_map is not None
    assert paper.reasoning_map.mode == MODE_DETERMINISTIC
    assert paper.sections and paper.sections[0].heading.startswith("1. ")
    # Real claims are named with a Chinese type; scaffolding never headlines.
    assert "使用时长与抑郁症状呈弱正相关（类型：相关）" in paper.abstract
    assert "因果主张：" not in paper.abstract
    assert "correlational" not in paper.abstract
    # The template consensus string never reaches the reader.
    assert "综合结论以" not in paper.abstract
    assert paper.references[0].doi == "10.1/a"


def test_stored_paper_parses_back_with_its_map() -> None:
    brief = _brief()
    paper = _fallback_integrated_paper(
        brief, _consensus(), brief.question, _judgments()
    )
    restored = paper_payload_to_dataclass(_paper_payload_dict(paper))
    assert isinstance(restored, FinalPaper)
    assert restored.reasoning_map == paper.reasoning_map


def test_markdown_keeps_each_conclusion_beside_its_limits() -> None:
    brief = _brief()
    paper = _fallback_integrated_paper(
        brief, _consensus(), brief.question, _judgments()
    )
    text = render_paper_markdown(paper, uuid4(), brief.question)
    order = [
        text.index("## 一句话回答"),
        text.index("## 推理链"),
        text.index("## 局限"),
        text.index("## 附录"),
    ]
    assert order == sorted(order)
    assert "未经模型整合" in text
    step = text.index("测量：测到的是我们真正关心的东西吗？")
    assert step < text.index("**边界与反例**", step) < text.index("## 局限")
    assert "### 专家终审原文" in text


def test_map_prompt_is_compact_and_skips_placeholder_claims() -> None:
    brief = _brief()
    items = _catalog()
    prompt = _build_map_prompt(brief.question, brief, items)
    assert "使用时长与抑郁症状呈弱正相关" in prompt
    assert "因果主张：社交媒体" not in prompt
    assert "[B1]" in prompt and "[J2]" in prompt
    for dimension in DIMENSION_KEYS:
        assert dimension in prompt


async def test_synthesis_progress_announces_start_and_outcome() -> None:
    rows: list[tuple[str, dict[str, object]]] = []
    flushes: list[int] = []

    async def flush() -> None:
        flushes.append(1)

    progress = _SynthesisProgress(
        lambda kind, payload: rows.append((kind, payload)), flush
    )
    await progress.start("model")
    await progress.finish(SynthesisOutcome(available=True, fallback=True))
    assert rows[0] == (SYNTHESIS_STARTED, {"mode": "model"})
    assert rows[-1][0] == SYNTHESIS_DONE
    assert rows[-1][1]["outcome"] == "deterministic"
    assert len(flushes) == 2


async def test_synthesis_progress_survives_a_broken_flush() -> None:
    async def broken() -> None:
        raise RuntimeError("database gone")

    progress = _SynthesisProgress(lambda kind, payload: None, broken)
    await progress.start("model")
    await progress.finish(SynthesisOutcome(available=True))
