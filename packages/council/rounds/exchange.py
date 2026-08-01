from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class EvidenceProjectionItem:
    source_id: UUID
    study_id: UUID | None = None
    finding_candidate_id: UUID | None = None
    anchor_summary: str = ""
    level: str = "D"


@dataclass(frozen=True, slots=True)
class ExchangeInput:
    evidence_items: tuple[EvidenceProjectionItem, ...]


@dataclass(frozen=True, slots=True)
class ExchangeOutput:
    evidence_items: tuple[EvidenceProjectionItem, ...]


@dataclass
class ExchangeRound:
    async def run(self, items: tuple[EvidenceProjectionItem, ...]) -> ExchangeOutput:
        # Strip any private fields before publishing
        public_items = tuple(
            EvidenceProjectionItem(
                source_id=item.source_id,
                study_id=item.study_id,
                finding_candidate_id=item.finding_candidate_id,
                anchor_summary=item.anchor_summary,
                level=item.level,
            )
            for item in items
        )
        return ExchangeOutput(evidence_items=public_items)
