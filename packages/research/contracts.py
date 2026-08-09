from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from packages.kernel.contracts import ContractModel, FrozenDict


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
    ``deepseek-v4-flash`` when the deployment has none), which is the "system
    default DeepSeek" the web form promises. The API key is stored on the
    task row and is never returned by any read endpoint (CLAUDE.md 16).

    ``is_free_trial`` marks a task whose inherited endpoint is the
    deployment's free-trial vendor (round-7); ``extra_body`` carries the
    vendor-specific request fields that endpoint needs (DashScope's
    ``enable_thinking``), forwarded to the worker's gateway. Both default
    off/None so the ordinary researcher-owned path is untouched.
    """

    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    model_name: str | None = None
    is_free_trial: bool = False
    # Vendor-specific request fields (e.g. DashScope's ``enable_thinking``),
    # forwarded verbatim into the chat-completions body by the worker's
    # gateway. FrozenDict because ContractModel forbids mutable containers;
    # it serialises to a plain JSON object on the task row.
    extra_body: FrozenDict[str, object] | None = None

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
    # Language the council must write its outputs in, following the language
    # the researcher asked in (round-4 requirement): "auto" means the API
    # detects it from `question` and stores the resolved value; otherwise one
    # of zh-Hans / zh-Hant / en. The worker injects it into every seat's
    # system prompt, so reasoning, structured outputs, and the final report
    # all come back in that language.
    output_language: str = "auto"
    # Task mode (round-7): "deep_research" (a controversy question, the
    # original flow) or "paper_review" (the researcher uploads a paper for
    # the council to critique). The worker reads it to decide whether to run
    # the paper-understanding step and which prompt shape to use; the
    # synthesizer reads it to decide which report shape to emit.
    task_type: str = "deep_research"
