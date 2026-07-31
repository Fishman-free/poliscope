from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from packages.kernel.contracts import ContractModel


class EvidenceDemandType(StrEnum):
    CORRELATION = "CORRELATION"
    CAUSAL_OR_REVERSE_CAUSAL = "CAUSAL_OR_REVERSE_CAUSAL"
    MEASUREMENT = "MEASUREMENT"
    REPLICATION = "REPLICATION"
    BOUNDARY = "BOUNDARY"
    MECHANISM = "MECHANISM"
    NULL_OR_COUNTEREXAMPLE = "NULL_OR_COUNTEREXAMPLE"


class ResearchScope(ContractModel):
    populations: tuple[str, ...]
    regions: tuple[str, ...]
    languages: tuple[str, ...]
    date_from: date | None
    date_until: date
    evidence_priorities: tuple[EvidenceDemandType, ...]
    allow_preprints: bool

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        if self.date_from is not None and self.date_from > self.date_until:
            raise ValueError("date_from must not be after date_until")
        return self


class ResearchBudget(ContractModel):
    wall_clock_minutes: int = Field(gt=0)
    model_cost_usd: Decimal = Field(ge=0)
    tool_call_limit: int = Field(gt=0)
    source_limit: int = Field(gt=0)


class UserEvidenceInput(ContractModel):
    dois: tuple[str, ...] = ()
    bibtex_entries: tuple[str, ...] = ()
    pdf_object_ids: tuple[UUID, ...] = ()


class ResearchContract(ContractModel):
    question: str = Field(min_length=1)
    scope: ResearchScope
    budget: ResearchBudget
    user_evidence: UserEvidenceInput
