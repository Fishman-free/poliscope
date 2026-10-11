from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from packages.kernel.contracts import ContractModel


class ReportRequest(ContractModel):
    task_id: UUID
    format: str = "markdown"  # markdown or json


class ReportResponse(ContractModel):
    task_id: UUID
    content: str
    format: str
    safety_notice_included: bool = False


@dataclass(frozen=True, slots=True)
class PaperSection:
    heading: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PaperReference:
    """One reference the paper cites. ``doi`` is optional: a source that was
    only ever Level B metadata may have none, and the renderer must not invent
    a link for it."""

    id: str
    title: str
    doi: str | None


@dataclass(frozen=True, slots=True)
class Standpoint:
    """One scientist seat's position in the debate, its stated weakness, and
    the evidence it rests on (round-12 「整合结论详细化」).

    The synthesizer names each side explicitly -- what it argues, where that
    position is weak, and what admitted evidence it leans on -- so a reader
    can trace every view instead of receiving a single blended conclusion.
    """

    seat: str
    position: str
    weakness: str
    supporting_evidence: tuple[str, ...] = ()
    disagreement: str = ""


@dataclass(frozen=True, slots=True)
class MapItem:
    """One council output the reasoning map can point at.

    ``id`` is a short catalogue id (``J1``, ``B3``, ``L12`` …) whose letter is
    the kind; ``headline`` is the one-line form the map and the model prompt
    use, ``text`` the original statement kept for the provenance chips. The
    item is a pointer to material the council already produced -- the map
    never creates an item of its own.
    """

    id: str
    kind: str
    headline: str
    text: str
    seat: str | None = None
    score: str | None = None
    has_dissent: bool = False


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    """Where one confirmed claim stands after the council, in one sentence."""

    claim: str
    verdict: str
    reason: str
    refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReasoningStep:
    """One link in the argument chain.

    A step answers one question and carries, *beside* that answer, what
    supports it, where it stops holding, and what is still unknown about it --
    so a blindspot is read as the doubt hanging over a specific conclusion,
    not as a free-floating list item (CLAUDE.md 11: conclusions and limits side
    by side). ``refs`` are the items the step cites; ``related`` the further
    council items filed under the same step so nothing is silently dropped.
    """

    dimension: str
    question: str
    answer: str
    support: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    resolve: str = ""
    refs: tuple[str, ...] = ()
    related: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CruxPosition:
    holders: str
    view: str


@dataclass(frozen=True, slots=True)
class Crux:
    """A disagreement the evidence has not settled, with what would settle it."""

    question: str
    positions: tuple[CruxPosition, ...] = ()
    decider: str = ""
    refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NextStep:
    action: str
    why: str = ""
    refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReasoningMap:
    """The final summary as one connected argument instead of parallel lists.

    ``mode`` is ``"model"`` when the synthesizer organised it and
    ``"deterministic"`` when it was filed mechanically (no model, a failed
    call, or a pre-map paper reorganised on read) -- the renderer says which,
    so a mechanical filing is never mistaken for an integrated judgment.
    ``items`` is the full catalogue; ``unassigned`` lists items no step holds.
    """

    mode: str
    bottom_line: str
    verdicts: tuple[ClaimVerdict, ...] = ()
    steps: tuple[ReasoningStep, ...] = ()
    cruxes: tuple[Crux, ...] = ()
    next_steps: tuple[NextStep, ...] = ()
    items: tuple[MapItem, ...] = ()
    unassigned: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalPaper:
    """The synthesised paper: one model call's integration of the council's
    already-admitted outputs. An expression-layer document, never evidence.

    ``limitations`` sit beside the conclusions in the renderer, not at the
    end, because CLAUDE.md 11 requires them side by side and CLAUDE.md 4
    forbids a report that reads as consensus when dissent was recorded.

    Round-12 「整合结论详细化」: ``standpoints`` names each side's position
    and its weakness, ``overall_conclusion`` states whether and what the
    council's overall view is, and ``conclusion_evidence`` lists the admitted
    evidence the overall conclusion rests on. All three default to empty so a
    model that did not emit them still produces a valid paper.

    ``reasoning_map`` is the structured summary the workspace renders first;
    the prose fields above are derived from it for the Markdown export and
    for readers of older payloads. ``None`` only on a paper stored before the
    map existed (the API reorganises those on read).
    """

    title: str
    abstract: str
    sections: tuple[PaperSection, ...]
    references: tuple[PaperReference, ...]
    limitations: tuple[str, ...]
    investigation_process: tuple[str, ...]
    standpoints: tuple[Standpoint, ...] = ()
    overall_conclusion: str = ""
    conclusion_evidence: tuple[str, ...] = ()
    reasoning_map: ReasoningMap | None = None


@dataclass(frozen=True, slots=True)
class ReviewClaim:
    """One claim the reviewed paper makes, with what the paper offers for it."""

    statement: str
    supporting_evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PaperOverview:
    """The reviewed paper as the synthesizer understood it."""

    title: str | None
    research_question: str
    main_claims: tuple[ReviewClaim, ...]


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    """One identified weakness. ``claim_ref`` names the paper claim it
    concerns (empty when it concerns the paper as a whole); ``severity`` is
    the synthesizer's stated severity (high/medium/low), kept as free text so
    a vendor's phrasing never needs coercing."""

    claim_ref: str | None
    issue: str
    severity: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    """One conclusion whose evidence the review found insufficient, with the
    evidence that would close the gap."""

    claim_ref: str | None
    missing_evidence: str
    suggested_evidence: str | None = None


@dataclass(frozen=True, slots=True)
class PaperReviewReport:
    """The paper-review task's final report (round-7).

    Same expression-layer discipline as FinalPaper: the report is one model
    call's integration of the council's outputs about the *uploaded paper* --
    what the paper argues, where its argument is not rigorous or well
    evidenced, and how to improve it. It is stored as a ledger event, never a
    graph node, and a report that could not critique the paper's content must
    say so rather than improvise.
    """

    title: str
    paper_overview: PaperOverview
    rigor_issues: tuple[ReviewIssue, ...]
    evidence_insufficiency: tuple[EvidenceGap, ...]
    improvement_suggestions: tuple[ReviewIssue, ...]
    conclusion: str
    limitations: tuple[str, ...]
    investigation_process: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SynthesisOutcome:
    """What the synthesis step produced.

    ``available=False`` means the paper was not generated; ``reason`` says
    why (no model provider, model failure, quarantined schema). A missing
    paper never masquerades as a complete one -- CLAUDE.md 10.
    """

    available: bool
    reason: str | None = None
    # True when the stored summary is the deterministic fallback rather than
    # the model's integration (the live view says which one the reader got).
    fallback: bool = False
