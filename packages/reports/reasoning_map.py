"""Reasoning map: the final summary as one connected chain of argument.

The council's outputs reach the summary as parallel, unlinked lists -- dozens
of blindspots, boundary conditions, unresolved conflicts and falsification
tests, plus seven final judgments -- and the evidence graph rarely carries an
edge tying one of them to the conclusion it threatens. Printed as lists they
read as fragments (a production summary repeated ~180 such lines twice). This
module files every one of them under a fixed spine of six questions every
empirical claim has to survive -- evidence → measurement → robustness →
causality → boundary → mechanism -- so a reader walks one argument from "what
do we have" to "what would settle it", and each doubt sits under the
conclusion it doubts.

The spine is the seat roster read as a reasoning order: each step is owned by
the seat whose expertise it is (CLAUDE.md 3). It is a presentation order, not
a vote -- no step aggregates the seats into a verdict (CLAUDE.md 4).

Two producers fill the same shape:

* :func:`parse_model_map` -- the synthesizer's organisation. The model gets a
  compact numbered catalogue and answers with short text plus catalogue ids;
  every id is checked against the catalogue, so the map can cite only what
  the council produced (an unknown id is dropped, never rendered).
* :func:`deterministic_map` -- the mechanical filing used when no model is
  available, the call fails, or a pre-map paper is reorganised on read. Items
  are filed by keyword; each step's answer quotes the owning seat's own
  verdict sentence, attributed.

Either way every catalogue item ends up somewhere -- a step's citations, a
step's related items, or ``unassigned`` -- so organising the summary never
silently drops a minority position (CLAUDE.md 4, 5.3). Pure functions only:
the database loaders live in :mod:`packages.reports.synthesis`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from packages.reports.contracts import (
    ClaimVerdict,
    Crux,
    CruxPosition,
    MapItem,
    NextStep,
    PaperReference,
    PaperSection,
    ReasoningMap,
    ReasoningStep,
)

MAP_VERSION = 1
MODE_MODEL = "model"
MODE_DETERMINISTIC = "deterministic"


@dataclass(frozen=True, slots=True)
class Dimension:
    key: str
    question: str
    seats: tuple[str, ...]
    keywords: tuple[str, ...]


# The spine. Questions are the reader-facing default (the model may rephrase
# them for the topic); keywords file an item whose text names the dimension.
DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "evidence",
        "证据基础：手里的证据从哪来、靠得住吗？",
        ("evidence_auditor",),
        (
            "来源", "核验", "doi", "原文", "引用", "同一数据", "同一队列",
            "数据集", "重复计", "独立证据", "审计", "文献", "检索", "记忆候选",
            "二手", "综述", "溯源",
        ),
    ),
    Dimension(
        "measurement",
        "测量：测到的是我们真正关心的东西吗？",
        ("measurement_scientist",),
        (
            "测量", "自评", "自我报告", "日志", "量表", "构念", "操作化", "信度",
            "效度", "测量误差", "不变性", "共同方法", "口径", "问卷", "诊断标准",
        ),
    ),
    Dimension(
        "robustness",
        "稳健性：关联是否真实存在、有多大？",
        ("replication_scientist",),
        (
            "效应量", "效应", "r≈", "发表偏倚", "复现", "可重复", "元分析",
            "显著", "伪精度", "多重比较", "样本量", "统计", "置信区间",
            "合并估计", "规格曲线", "预注册", "功效", "噪声", "基线", "p-curve",
        ),
    ),
    Dimension(
        "causality",
        "因果：能否从关联推到因果？",
        ("causal_scientist",),
        (
            "因果", "混杂", "反向", "识别", "外生", "随机", "实验", "did",
            "双重差分", "断点", "工具变量", "遗传", "内生", "个体内", "within",
            "时序", "交叉滞后", "停用", "rollout", "反事实", "准实验",
        ),
    ),
    Dimension(
        "boundary",
        "适用边界：结论对谁、在什么条件下成立？",
        ("boundary_scientist",),
        (
            "年龄", "性别", "女生", "男生", "文化", "国家", "国别", "地区",
            "平台", "时期", "亚组", "边界", "外推", "情境", "城乡", "人群",
            "适用", "发育", "群体", "社会经济",
        ),
    ),
    Dimension(
        "mechanism",
        "机制与替代解释：还有什么能解释这些结果？",
        ("theory_builder", "adversarial_falsifier"),
        (
            "机制", "理论", "中介", "调节", "路径", "替代解释", "竞争", "假说",
            "社会比较", "睡眠", "置换", "解释", "前因",
        ),
    ),
)
DIMENSION_KEYS: tuple[str, ...] = tuple(dimension.key for dimension in DIMENSIONS)
_DIMENSION_BY_KEY = {dimension.key: dimension for dimension in DIMENSIONS}

VERDICTS: tuple[str, ...] = (
    "supported",
    "partially_supported",
    "not_supported",
    "undetermined",
)
VERDICT_LABELS: dict[str, str] = {
    "supported": "成立",
    "partially_supported": "部分成立",
    "not_supported": "不成立",
    "undetermined": "尚无定论",
}

# Kind -> catalogue id prefix, in catalogue order.
KIND_PREFIX: dict[str, str] = {
    "judgment": "J",
    "finding": "F",
    "blindspot": "B",
    "boundary": "L",
    "conflict": "C",
    "test": "T",
    "dissent": "D",
    "study": "S",
}
KIND_LABELS: dict[str, str] = {
    "judgment": "专家终审",
    "finding": "研究发现",
    "blindspot": "盲点",
    "boundary": "适用边界",
    "conflict": "未决分歧",
    "test": "可证伪检验",
    "dissent": "异议",
    "study": "判别研究",
}
_KIND_LABELS_EN: dict[str, str] = {
    "judgment": "Independent final positions of the seven experts",
    "finding": "Admitted findings (each bound to a source)",
    "blindspot": "Blindspots (ranked by impact)",
    "boundary": "Boundary conditions",
    "conflict": "Unresolved conflicts",
    "test": "Falsification tests",
    "dissent": "Recorded dissents",
    "study": "Discriminating study suggestions",
}
# Where an item lands when its text names no dimension.
_KIND_DEFAULT_DIMENSION: dict[str, str] = {
    "finding": "evidence",
    "blindspot": "mechanism",
    "boundary": "boundary",
    "conflict": "mechanism",
    "test": "causality",
    "dissent": "mechanism",
    "study": "causality",
}
# Prompt budget per kind: the model sees headlines only (judgments excerpted),
# which is what keeps the synthesis call far below the gateway's deadline.
_PROMPT_CAPS: dict[str, int] = {
    "judgment": 7,
    "finding": 30,
    "blindspot": 30,
    "boundary": 30,
    "conflict": 30,
    "test": 24,
    "dissent": 10,
    "study": 10,
}
_TEXT_LIMITS: dict[str, int] = {"judgment": 6000}
_DEFAULT_TEXT_LIMIT = 1200
_HEADLINE_LIMIT = 90
# A 【tag】 at least this long reads as a sentence on its own.
_SELF_EXPLANATORY_TAG = 18
_BULLET_LIMIT = 200
_ANSWER_LIMIT = 600
_SHOWN_PER_FIELD = 3

SEAT_DISPLAY_NAMES: dict[str, str] = {
    "theory_builder": "理论建构者",
    "causal_scientist": "因果推断专家",
    "measurement_scientist": "测量与构念专家",
    "replication_scientist": "统计与复现专家",
    "boundary_scientist": "边界与情境专家",
    "adversarial_falsifier": "对抗性证伪者",
    "evidence_auditor": "证据与溯源审计员",
}

NO_OWNER_ANSWER = "（负责这一环节的专家未给出终审结论；以下为归入此环节的条目。）"

# Template consensus strings joint modeling writes when no seat produced a
# synthesis (packages/council/rounds/joint_modeling.py): counts, not content.
_TEMPLATE_CONSENSUS_PREFIXES = ("综合结论以", "conditional on ")


def seat_display(seat: object) -> str:
    text = "" if seat is None else str(seat)
    return SEAT_DISPLAY_NAMES.get(text, text)


def meaningful_consensus(text: object) -> str:
    """The conditioned consensus if it says something, else ``""``."""
    if not isinstance(text, str):
        return ""
    stripped = text.strip()
    if not stripped or stripped.startswith(_TEMPLATE_CONSENSUS_PREFIXES):
        return ""
    return stripped


# --------------------------------------------------------------------- text

_MD_EMPHASIS = re.compile(r"\*\*|__|`")
_INLINE_SPACE = re.compile(r"[ \t　]+")
_TAG = re.compile(r"^[【\[]([^】\]\n]{2,80})[】\]]")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])|\n+")
_HEADING_PREFIX = re.compile(
    r"^(#+\s*|[一二三四五六七八九十]+[、.．]\s*|\(?\d+[、.．)）]\s*|[-·•]\s*)"
)
_SENTENCE_END = ("。", "！", "？", "!", "?", "；", ";")
_VERDICT_MARKERS = (
    "不成立", "成立", "不支持", "支持", "否定", "存活", "反对", "裁定",
    "判定", "结论",
)
_DEDUPE_STRIP = re.compile(r"[\s\W_]+", re.UNICODE)


def _clean(text: str) -> str:
    text = _MD_EMPHASIS.sub("", text)
    lines = (_INLINE_SPACE.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _sentences(text: str) -> list[str]:
    return [
        part.strip()
        for part in _SENTENCE_SPLIT.split(_clean(text))
        if part and part.strip()
    ]


_READER_VOICE = (("本席", "该专家"), ("七席", "七位专家"), ("各席", "各位专家"))


def _reader_voice(text: str) -> str:
    # Seats write "本席" (this seat) and "七席"; a reader sees the expert's
    # name beside the line, so the seat jargon is softened in the one-line
    # form only -- the full text keeps the seat's exact words.
    for jargon, plain in _READER_VOICE:
        text = text.replace(jargon, plain)
    return text


def headline_of(text: str, limit: int = _HEADLINE_LIMIT) -> str:
    """The one-line form of an item: its 【tag】 when the tag says enough,
    the tag plus its first sentence when the tag is a bare label
    ("【方法学声明】"), else the first sentence."""
    cleaned = _clean(text)
    if not cleaned:
        return ""
    tag = _TAG.match(cleaned)
    if tag:
        label = tag.group(1).strip()
        rest = _sentences(cleaned[tag.end():])
        if len(label) >= _SELF_EXPLANATORY_TAG or not rest:
            return _reader_voice(_clip(label, limit))
        return _reader_voice(_clip(f"{label}：{rest[0]}", limit))
    sentences = _sentences(cleaned)
    return _reader_voice(_clip(sentences[0] if sentences else cleaned, limit))


def _is_heading(sentence: str) -> bool:
    """A section heading inside a judgment ("一、总判断", "【终审判决】")."""
    return len(sentence) < 30 and not sentence.endswith(_SENTENCE_END)


def verdict_sentence(text: str, limit: int = 160) -> str:
    """The sentence of a final judgment that states its verdict.

    Seats open their judgments with headings and preambles; the deterministic
    map quotes the first sentence carrying a verdict marker instead, joined
    with the next sentence when it ends on a colon ("…判定：" + "因果主张不成立。").
    """
    candidates = [
        stripped
        for sentence in _sentences(text)
        if (stripped := _HEADING_PREFIX.sub("", sentence).strip())
        and not _is_heading(stripped)
    ]
    if not candidates:
        return _clip(_clean(text), limit)
    index = next(
        (
            position
            for position, sentence in enumerate(candidates)
            if any(marker in sentence for marker in _VERDICT_MARKERS)
        ),
        0,
    )
    chosen = candidates[index]
    if chosen.endswith(("：", ":")) and index + 1 < len(candidates):
        chosen = chosen + candidates[index + 1]
    return _reader_voice(_clip(chosen, limit))


def _dedupe_key(text: str) -> str:
    return _DEDUPE_STRIP.sub("", text.lower())[:160]


def _score_value(score: object) -> Decimal:
    try:
        return Decimal(str(score))
    except (InvalidOperation, ValueError):
        return Decimal("-1")


# ----------------------------------------------------------------- catalogue


@dataclass(frozen=True, slots=True)
class JudgmentRow:
    """One seat's FINAL_JUDGMENT, as the catalogue needs it."""

    seat: str
    text: str
    confidence: object = None
    has_dissent: bool = False


def _statement(payload: Mapping[str, object]) -> str:
    for key in ("finding_statement", "statement", "summary", "question"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _strings(value: object) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def build_catalog(
    *,
    findings: Sequence[Mapping[str, object]] = (),
    blindspots: Sequence[Mapping[str, object]] = (),
    dissents: Sequence[Mapping[str, object]] = (),
    studies: Sequence[Mapping[str, object]] = (),
    consensus: Mapping[str, object] | None = None,
    judgments: Sequence[JudgmentRow] = (),
) -> tuple[MapItem, ...]:
    """Number every council output once, in a stable order.

    Inputs are node payloads (findings, blindspots, dissents, discriminating
    studies), the CONSENSUS_DRAFTED payload, and the final judgments. Exact
    and near-exact repeats collapse into the first occurrence -- a dissent
    certificate restates its author's judgment verbatim, and seats often
    nominate the same boundary in the same words. Blindspots are ranked by
    their bounty score so the strongest doubt in every step comes first.
    """
    items: list[MapItem] = []
    seen: set[str] = set()
    counters: dict[str, int] = defaultdict(int)

    def add(
        kind: str,
        text: str,
        *,
        headline: str | None = None,
        seat: str | None = None,
        score: object = None,
        has_dissent: bool = False,
    ) -> None:
        cleaned = _clean(text)
        if not cleaned:
            return
        key = _dedupe_key(cleaned)
        if not key or key in seen:
            return
        seen.add(key)
        counters[kind] += 1
        items.append(
            MapItem(
                id=f"{KIND_PREFIX[kind]}{counters[kind]}",
                kind=kind,
                headline=headline or headline_of(cleaned),
                text=_clip(cleaned, _TEXT_LIMITS.get(kind, _DEFAULT_TEXT_LIMIT)),
                seat=seat,
                score=None if score is None else str(score),
                has_dissent=has_dissent,
            )
        )

    for row in judgments:
        add(
            "judgment",
            row.text,
            headline=verdict_sentence(row.text, 120),
            seat=row.seat,
            score=row.confidence,
            has_dissent=row.has_dissent,
        )
    for payload in findings:
        add("finding", _statement(payload))
    ranked = sorted(
        blindspots,
        key=lambda payload: _score_value(payload.get("score")),
        reverse=True,
    )
    for payload in ranked:
        add("blindspot", _statement(payload), score=payload.get("score"))
    consensus = consensus or {}
    for text in _strings(consensus.get("boundary_conditions")):
        add("boundary", text)
    for text in _strings(consensus.get("unresolved_conflicts")):
        add("conflict", text)
    for text in _strings(consensus.get("falsification_conditions")):
        add("test", text)
    for payload in dissents:
        author = payload.get("author")
        add(
            "dissent",
            _statement(payload),
            seat=author if isinstance(author, str) else None,
            has_dissent=True,
        )
    for payload in studies:
        add("study", _statement(payload))
    return tuple(items)


def classify(item: MapItem) -> str:
    """The spine step an item belongs to, by the dimension its text names.

    Headline hits weigh three times body hits; a tie, or no hit at all, falls
    back to the kind's natural step (a boundary condition is a boundary
    question unless it plainly names something else).
    """
    head = item.headline.lower()
    body = item.text.lower()
    scores = {
        dimension.key: sum(
            3 * head.count(keyword) + body.count(keyword)
            for keyword in dimension.keywords
        )
        for dimension in DIMENSIONS
    }
    default = _KIND_DEFAULT_DIMENSION.get(item.kind, "mechanism")
    best = max(scores.values())
    if best == 0:
        return default
    leaders = [key for key in DIMENSION_KEYS if scores[key] == best]
    return default if default in leaders else leaders[0]


def catalog_prompt_lines(items: Sequence[MapItem]) -> list[str]:
    """The catalogue as the synthesizer sees it: id + headline per item.

    Judgments are excerpted (they carry the verdicts); every other kind is a
    one-line headline, capped per kind. Items past the cap stay in the
    catalogue and are filed mechanically -- the prompt says how many exist.
    """
    lines: list[str] = []
    for kind, label in _KIND_LABELS_EN.items():
        group = [item for item in items if item.kind == kind]
        if not group:
            continue
        shown = group[: _PROMPT_CAPS[kind]]
        suffix = (
            f"; first {len(shown)} shown" if len(shown) < len(group) else ""
        )
        lines.append(f"### {label} ({len(group)} total{suffix})")
        for item in shown:
            if kind == "judgment":
                dissent = ", keeps a dissent" if item.has_dissent else ""
                lines.append(
                    f"[{item.id}] {seat_display(item.seat)}"
                    f" (confidence {item.score or 'not reported'}{dissent}): "
                    f"{_clip(item.text, 900)}"
                )
            else:
                lines.append(f"[{item.id}] {item.headline}")
        lines.append("")
    return lines


# --------------------------------------------------------------- model path


def _text(value: object, limit: int = _ANSWER_LIMIT) -> str:
    return _clip(_clean(value), limit) if isinstance(value, str) else ""


def _bullets(value: object, cap: int = 4) -> tuple[str, ...]:
    return tuple(
        _clip(_clean(text), _BULLET_LIMIT) for text in _strings(value)[:cap]
    )


def _refs(value: object, known: set[str]) -> tuple[str, ...]:
    refs = (text.strip().strip("[]").upper() for text in _strings(value))
    return tuple(dict.fromkeys(ref for ref in refs if ref in known))


def _mappings(value: object) -> list[Mapping[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _dimension_of(value: object, question: str) -> str:
    if isinstance(value, str) and value.strip().lower() in DIMENSION_KEYS:
        return value.strip().lower()
    probe = MapItem(id="", kind="conflict", headline=question, text=question)
    return classify(probe)


def _verdict_of(value: object) -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return text if text in VERDICTS else "undetermined"


def parse_model_map(
    payload: Mapping[str, object], items: Sequence[MapItem]
) -> ReasoningMap:
    """Validate the synthesizer's map against the catalogue it was shown.

    Raises on a map with no bottom line or no usable step -- the caller turns
    that into FINAL_PAPER_FAILED plus the deterministic map, never a half map.
    Unknown ids are dropped; steps are put in spine order so every summary
    reads in the same logical sequence.
    """
    known = {item.id for item in items}
    bottom_line = _text(payload.get("bottom_line"), 1200)
    if not bottom_line:
        raise ValueError("reasoning map missing bottom_line")

    steps: list[ReasoningStep] = []
    for raw in _mappings(payload.get("steps")):
        question = _text(raw.get("question"), 120)
        answer = _text(raw.get("answer"))
        if not answer:
            continue
        dimension = _dimension_of(raw.get("dimension"), question or answer)
        steps.append(
            ReasoningStep(
                dimension=dimension,
                question=question or _DIMENSION_BY_KEY[dimension].question,
                answer=answer,
                support=_bullets(raw.get("support")),
                limits=_bullets(raw.get("limits")),
                unknowns=_bullets(raw.get("unknowns")),
                resolve=_text(raw.get("resolve"), _BULLET_LIMIT),
                refs=_refs(raw.get("refs"), known),
            )
        )
    if not steps:
        raise ValueError("reasoning map produced no steps")
    steps.sort(key=lambda step: DIMENSION_KEYS.index(step.dimension))

    verdicts = tuple(
        ClaimVerdict(
            claim=_text(raw.get("claim"), 160),
            verdict=_verdict_of(raw.get("verdict")),
            reason=_text(raw.get("reason"), 300),
            refs=_refs(raw.get("refs"), known),
        )
        for raw in _mappings(payload.get("verdicts"))[:4]
        if _text(raw.get("claim"))
    )
    cruxes = tuple(
        Crux(
            question=_text(raw.get("question"), 200),
            positions=tuple(
                CruxPosition(
                    holders=_text(position.get("holders"), 80),
                    view=_text(position.get("view"), 300),
                )
                for position in _mappings(raw.get("positions"))[:4]
                if _text(position.get("view"))
            ),
            decider=_text(raw.get("decider"), 300),
            refs=_refs(raw.get("refs"), known),
        )
        for raw in _mappings(payload.get("cruxes"))[:4]
        if _text(raw.get("question"))
    )
    next_steps = tuple(
        NextStep(
            action=_text(raw.get("action"), 200),
            why=_text(raw.get("why"), 300),
            refs=_refs(raw.get("refs"), known),
        )
        for raw in _mappings(payload.get("next_steps"))[:5]
        if _text(raw.get("action"))
    )
    return _file_uncited(
        ReasoningMap(
            mode=MODE_MODEL,
            bottom_line=bottom_line,
            verdicts=verdicts,
            steps=tuple(steps),
            cruxes=cruxes,
            next_steps=next_steps,
        ),
        items,
    )


def _cited(rmap: ReasoningMap) -> set[str]:
    cited: set[str] = set()
    for verdict in rmap.verdicts:
        cited.update(verdict.refs)
    for step in rmap.steps:
        cited.update(step.refs)
    for crux in rmap.cruxes:
        cited.update(crux.refs)
    for action in rmap.next_steps:
        cited.update(action.refs)
    return cited


def _file_uncited(rmap: ReasoningMap, items: Sequence[MapItem]) -> ReasoningMap:
    """Attach every item the model did not cite to the step of its dimension.

    Judgments are excluded: they are listed whole in the appendix anyway.
    An item whose dimension has no step lands in ``unassigned``.
    """
    cited = _cited(rmap)
    first_step: dict[str, int] = {}
    for position, step in enumerate(rmap.steps):
        first_step.setdefault(step.dimension, position)
    related: dict[int, list[str]] = defaultdict(list)
    unassigned: list[str] = []
    for item in items:
        if item.kind == "judgment" or item.id in cited:
            continue
        target = first_step.get(classify(item))
        if target is None:
            unassigned.append(item.id)
        else:
            related[target].append(item.id)
    steps = tuple(
        replace(step, related=tuple(related.get(index, ())))
        for index, step in enumerate(rmap.steps)
    )
    return replace(rmap, steps=steps, items=tuple(items), unassigned=tuple(unassigned))


# ------------------------------------------------------- deterministic path


def deterministic_map(
    items: Sequence[MapItem], *, bottom_line: str = ""
) -> ReasoningMap:
    """File the catalogue under the spine without any model call.

    Each step's answer is the owning seat's own verdict sentence, attributed
    by name; beside it go up to three findings (support), boundaries
    (limits), blindspots/conflicts (unknowns, strongest first) and the first
    falsification test (how to resolve). Everything else filed under the step
    is kept as its related items. ``bottom_line`` is left empty unless the
    council itself wrote a real consensus -- a mechanical filing must not
    invent an answer the council did not give.
    """
    judgments = {
        item.seat: item for item in items if item.kind == "judgment" and item.seat
    }
    filed: dict[str, list[MapItem]] = defaultdict(list)
    for item in items:
        if item.kind != "judgment":
            filed[classify(item)].append(item)

    steps: list[ReasoningStep] = []
    for dimension in DIMENSIONS:
        owners = [judgments[seat] for seat in dimension.seats if seat in judgments]
        pool = filed.get(dimension.key, [])
        if not owners and not pool:
            continue
        support = [item for item in pool if item.kind == "finding"]
        limits = [item for item in pool if item.kind == "boundary"]
        unknowns = [item for item in pool if item.kind in ("blindspot", "conflict")]
        tests = [item for item in pool if item.kind in ("test", "study")]
        # A dissent that is not a restatement of some judgment (those were
        # merged in the catalogue) is a minority position in its own right:
        # it goes into the answer, never into the folded remainder.
        dissents = [item for item in pool if item.kind == "dissent"]
        shown = (
            dissents
            + support[:_SHOWN_PER_FIELD]
            + limits[:_SHOWN_PER_FIELD]
            + unknowns[:_SHOWN_PER_FIELD]
            + tests[:1]
        )
        shown_ids = {item.id for item in shown}
        answer = "\n".join(
            [
                f"{seat_display(owner.seat)}："
                + owner.headline
                + ("（保留异议）" if owner.has_dissent else "")
                for owner in owners
            ]
            + [
                f"异议（{seat_display(item.seat) if item.seat else '少数意见'}）："
                + item.headline
                for item in dissents
            ]
        )
        steps.append(
            ReasoningStep(
                dimension=dimension.key,
                question=dimension.question,
                answer=answer or NO_OWNER_ANSWER,
                support=tuple(item.headline for item in support[:_SHOWN_PER_FIELD]),
                limits=tuple(item.headline for item in limits[:_SHOWN_PER_FIELD]),
                unknowns=tuple(
                    item.headline for item in unknowns[:_SHOWN_PER_FIELD]
                ),
                resolve=tests[0].headline if tests else "",
                refs=(
                    *(owner.id for owner in owners),
                    *(item.id for item in shown),
                ),
                related=tuple(
                    item.id for item in pool if item.id not in shown_ids
                ),
            )
        )
    return ReasoningMap(
        mode=MODE_DETERMINISTIC,
        bottom_line=meaningful_consensus(bottom_line),
        steps=tuple(steps),
        items=tuple(items),
    )


# ------------------------------------------------------------ serialisation


def _item_payload(item: MapItem) -> dict[str, object]:
    return {
        "id": item.id,
        "kind": item.kind,
        "headline": item.headline,
        "text": item.text,
        "seat": item.seat,
        "score": item.score,
        "has_dissent": item.has_dissent,
    }


def map_to_payload(rmap: ReasoningMap) -> dict[str, object]:
    return {
        "version": MAP_VERSION,
        "mode": rmap.mode,
        "bottom_line": rmap.bottom_line,
        "verdicts": [
            {
                "claim": verdict.claim,
                "verdict": verdict.verdict,
                "reason": verdict.reason,
                "refs": list(verdict.refs),
            }
            for verdict in rmap.verdicts
        ],
        "steps": [
            {
                "dimension": step.dimension,
                "question": step.question,
                "answer": step.answer,
                "support": list(step.support),
                "limits": list(step.limits),
                "unknowns": list(step.unknowns),
                "resolve": step.resolve,
                "refs": list(step.refs),
                "related": list(step.related),
            }
            for step in rmap.steps
        ],
        "cruxes": [
            {
                "question": crux.question,
                "positions": [
                    {"holders": position.holders, "view": position.view}
                    for position in crux.positions
                ],
                "decider": crux.decider,
                "refs": list(crux.refs),
            }
            for crux in rmap.cruxes
        ],
        "next_steps": [
            {"action": action.action, "why": action.why, "refs": list(action.refs)}
            for action in rmap.next_steps
        ],
        "items": [_item_payload(item) for item in rmap.items],
        "unassigned": list(rmap.unassigned),
    }


def _plain(value: object) -> str:
    return value if isinstance(value, str) else ""


def _tuple(value: object) -> tuple[str, ...]:
    return tuple(_strings(value))


def map_from_payload(value: object) -> ReasoningMap | None:
    """Read a stored map back. ``None`` for anything that is not a map --
    the renderer then falls back to the paper's prose fields."""
    if not isinstance(value, Mapping):
        return None
    steps = tuple(
        ReasoningStep(
            dimension=_plain(raw.get("dimension")) or "mechanism",
            question=_plain(raw.get("question")),
            answer=_plain(raw.get("answer")),
            support=_tuple(raw.get("support")),
            limits=_tuple(raw.get("limits")),
            unknowns=_tuple(raw.get("unknowns")),
            resolve=_plain(raw.get("resolve")),
            refs=_tuple(raw.get("refs")),
            related=_tuple(raw.get("related")),
        )
        for raw in _mappings(value.get("steps"))
    )
    return ReasoningMap(
        mode=_plain(value.get("mode")) or MODE_DETERMINISTIC,
        bottom_line=_plain(value.get("bottom_line")),
        verdicts=tuple(
            ClaimVerdict(
                claim=_plain(raw.get("claim")),
                verdict=_verdict_of(raw.get("verdict")),
                reason=_plain(raw.get("reason")),
                refs=_tuple(raw.get("refs")),
            )
            for raw in _mappings(value.get("verdicts"))
        ),
        steps=steps,
        cruxes=tuple(
            Crux(
                question=_plain(raw.get("question")),
                positions=tuple(
                    CruxPosition(
                        holders=_plain(position.get("holders")),
                        view=_plain(position.get("view")),
                    )
                    for position in _mappings(raw.get("positions"))
                ),
                decider=_plain(raw.get("decider")),
                refs=_tuple(raw.get("refs")),
            )
            for raw in _mappings(value.get("cruxes"))
        ),
        next_steps=tuple(
            NextStep(
                action=_plain(raw.get("action")),
                why=_plain(raw.get("why")),
                refs=_tuple(raw.get("refs")),
            )
            for raw in _mappings(value.get("next_steps"))
        ),
        items=tuple(
            MapItem(
                id=_plain(raw.get("id")),
                kind=_plain(raw.get("kind")),
                headline=_plain(raw.get("headline")),
                text=_plain(raw.get("text")),
                seat=_plain(raw.get("seat")) or None,
                score=_plain(raw.get("score")) or None,
                has_dissent=raw.get("has_dissent") is True,
            )
            for raw in _mappings(value.get("items"))
            if _plain(raw.get("id"))
        ),
        unassigned=_tuple(value.get("unassigned")),
    )


# ------------------------------------------------------------- prose view

DETERMINISTIC_NOTICE = (
    "本次结论未经模型整合：系统把各位专家的终审要点与全部讨论条目，"
    "按证据链六个环节机械归档，供逐步核对；每一步的判断以专家原话为准。"
)


def paper_sections(rmap: ReasoningMap) -> tuple[PaperSection, ...]:
    """The map as headed prose sections -- the FinalPaper ``sections`` field
    that older readers (and the Markdown export of a map-less consumer)
    render. Kept short on purpose: the map itself is the full view."""
    sections: list[PaperSection] = []
    for number, step in enumerate(rmap.steps, start=1):
        paragraphs = [step.answer]
        for label, values in (
            ("依据", step.support),
            ("边界", step.limits),
            ("尚未知道", step.unknowns),
        ):
            if values:
                paragraphs.append(
                    f"{label}：\n" + "\n".join(f"- {value}" for value in values)
                )
        if step.resolve:
            paragraphs.append(f"如何验证：{step.resolve}")
        sections.append(
            PaperSection(
                heading=f"{number}. {step.question}", paragraphs=tuple(paragraphs)
            )
        )
    if rmap.cruxes:
        sections.append(
            PaperSection(
                heading="关键分歧",
                paragraphs=tuple(
                    crux.question
                    + "".join(
                        f"\n- {position.holders or '一方'}：{position.view}"
                        for position in crux.positions
                    )
                    + (f"\n- 裁决所需证据：{crux.decider}" if crux.decider else "")
                    for crux in rmap.cruxes
                ),
            )
        )
    if rmap.next_steps:
        sections.append(
            PaperSection(
                heading="下一步研究",
                paragraphs=tuple(
                    f"{index}. {action.action}"
                    + (f"（{action.why}）" if action.why else "")
                    for index, action in enumerate(rmap.next_steps, start=1)
                ),
            )
        )
    return tuple(sections)


def paper_abstract(rmap: ReasoningMap) -> str:
    parts = [rmap.bottom_line or DETERMINISTIC_NOTICE]
    for verdict in rmap.verdicts:
        label = VERDICT_LABELS[verdict.verdict]
        parts.append(f"- **{verdict.claim}**：{label}。{verdict.reason}")
    return "\n\n".join(part for part in parts if part)


def finding_references(
    findings: Sequence[tuple[str, Mapping[str, object]]],
) -> tuple[PaperReference, ...]:
    """References from admitted findings with a DOI, one per DOI.

    ``findings`` is ``(node_id, payload)``. A finding is the council's take on
    one study and its DOI is that study's identifier; a missing title falls
    back to the DOI itself, never to a guessed title.
    """
    references: list[PaperReference] = []
    seen: set[str] = set()
    for node_id, payload in findings:
        doi = payload.get("doi")
        if not isinstance(doi, str) or not doi.strip() or doi.strip() in seen:
            continue
        doi = doi.strip()
        seen.add(doi)
        title = payload.get("source_title") or payload.get("title")
        if not isinstance(title, str) or not title.strip():
            title = f"DOI {doi}"
        references.append(PaperReference(id=node_id, title=title, doi=doi))
    return tuple(references)


__all__ = [
    "DETERMINISTIC_NOTICE",
    "DIMENSIONS",
    "DIMENSION_KEYS",
    "JudgmentRow",
    "KIND_LABELS",
    "MODE_DETERMINISTIC",
    "MODE_MODEL",
    "VERDICTS",
    "VERDICT_LABELS",
    "build_catalog",
    "catalog_prompt_lines",
    "classify",
    "deterministic_map",
    "finding_references",
    "headline_of",
    "map_from_payload",
    "map_to_payload",
    "meaningful_consensus",
    "paper_abstract",
    "paper_sections",
    "parse_model_map",
    "seat_display",
    "verdict_sentence",
]
