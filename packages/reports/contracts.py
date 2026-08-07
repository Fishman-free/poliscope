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
class FinalPaper:
    """The synthesised paper: one model call's integration of the council's
    already-admitted outputs. An expression-layer document, never evidence.

    ``limitations`` sit beside the conclusions in the renderer, not at the
    end, because CLAUDE.md 11 requires them side by side and CLAUDE.md 4
    forbids a report that reads as consensus when dissent was recorded.
    """

    title: str
    abstract: str
    sections: tuple[PaperSection, ...]
    references: tuple[PaperReference, ...]
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
