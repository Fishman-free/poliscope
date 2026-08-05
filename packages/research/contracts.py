from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

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
    wall_clock_minutes: StrictInt = Field(gt=0)
    model_cost_usd: Decimal = Field(ge=0)
    tool_call_limit: StrictInt = Field(gt=0)
    source_limit: StrictInt = Field(gt=0)


class UserEvidenceInput(ContractModel):
    dois: tuple[str, ...] = ()
    bibtex_entries: tuple[str, ...] = ()
    pdf_object_ids: tuple[UUID, ...] = ()


class TaskModelConfig(ContractModel):
    """Per-task model configuration: a researcher's own endpoint for this run.

    ``base_url`` and ``api_key`` must come as a pair -- a key without an
    endpoint is as broken as an endpoint without a key. ``model_name`` is
    optional: it defaults to the deployment's configured model (or
    ``deepseek-chat`` when the deployment has none), which is the "system
    default DeepSeek" the web form promises. The API key is stored on the
    task row and is never returned by any read endpoint (CLAUDE.md 16).
    """

    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model_name: str | None = None

    @model_validator(mode="after")
    def validate_base_url_scheme(self) -> Self:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return self


class ResearchContract(ContractModel):
    question: str = Field(min_length=1)
    scope: ResearchScope
    budget: ResearchBudget
    user_evidence: UserEvidenceInput
    # None means "use the deployment's configured model gateway" -- the
    # ordinary path. A value means this task runs against the researcher's own
    # endpoint instead, no matter what the worker process was started with.
    # Named task_model_config rather than model_config because the latter is
    # a Pydantic-reserved attribute name.
    task_model_config: TaskModelConfig | None = None
    # Knowledge base whose documents the council should treat as Level A
    # user-provided sources. None is the ordinary case; the API layer
    # validates that the id names a real knowledge base before the task is
    # created (apps/api/routers/tasks.py).
    knowledge_base_id: UUID | None = None
    # Skills the researcher enabled for this task (migration 0013). The worker
    # resolves these to the downloaded SKILL.md texts and injects them into the
    # council's prompts as explicitly non-evidence process context; the API
    # layer validates that every id belongs to the creating account.
    skill_ids: tuple[UUID, ...] = ()
