"""Paper-understanding step for paper-review tasks (round-7).

A ``paper_review`` task hands the council an uploaded paper instead of a
controversy question. Before the council runs, one model call reads the
paper's extracted text and states what it actually is -- title, research
question, main claims with the evidence the paper offers for each, and what
could not be verified. That summary is injected into every seat's prompt as
explicitly non-evidence context (the paper itself is already Level A user
evidence via ``acquire_uploaded`` / ``extract_uploaded``), so the seven
scientists critique the *paper the researcher wrote*, not a paraphrase.

Honesty invariants, mirroring ``packages/reports/synthesis.py``:

* **The understanding is not evidence.** It is stored as process-only ledger
  events (``PAPER_UNDERSTANDING_CAPTURED`` / ``PAPER_UNDERSTANDING_FAILED``),
  never graph nodes -- the machine's reading of the paper is auditable
  history, not a formal result (CLAUDE.md 6).
* **A missing model provider is not a failure.** With no gateway the step
  writes nothing and returns a reason; the council still runs (its seats go
  absent as usual) and the final report must say it could not critique the
  paper's content.
* **Truncation is admitted.** The extracted text is capped at
  ``MAX_PAPER_TEXT_CHARS``; the payload records ``truncated: true`` so the
  report can say the analysis covered only part of the paper.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.council.deliberation import OUTPUT_LANGUAGE_DIRECTIVES
from packages.evidence.sql_ledger import SqlEventLedger
from packages.kernel.contracts import thaw_for_serialization
from packages.kernel.database import canonical_uuid
from packages.knowledge.extractors import InvalidDocument, extract_text
from packages.models.contracts import (
    ModelClass,
    ModelGateway,
    ModelMessage,
    ModelRequest,
    SchemaStatus,
)
from packages.models.gateway import AuditedModelGateway
from packages.papers.models import ObjectModel
from packages.papers.object_store import ObjectNotFound, PrivateObjectStore
from packages.reports.safety import sanitize_export
from packages.research.language import detect_output_language
from packages.research.models import ResearchTaskModel

logger = logging.getLogger(__name__)

# Ledger event names. Both stay out of NODE_EVENT_TYPES, so the projector
# marks them process_only: the machine's reading of the paper is auditable
# history, not evidence.
PAPER_UNDERSTANDING_CAPTURED = "PAPER_UNDERSTANDING_CAPTURED"
PAPER_UNDERSTANDING_FAILED = "PAPER_UNDERSTANDING_FAILED"

_IDEMPOTENCY_CAPTURED = "PAPER_UNDERSTANDING:captured"
_IDEMPOTENCY_FAILED = "PAPER_UNDERSTANDING:failed"

# Upper bound on the text handed to the model. Papers run longer than this;
# the payload records the truncation so the report admits partial coverage.
MAX_PAPER_TEXT_CHARS = 60_000

# How many uploaded objects to read in one understanding pass.
MAX_PAPER_OBJECTS = 5


@dataclass(frozen=True, slots=True)
class PaperUnderstandingResult:
    ok: bool
    reason: str | None = None
    payload: dict[str, object] | None = None


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def _render_text_blocks(blocks: Sequence[object], kind: str) -> str:
    """Join extracted blocks into one labelled passage.

    ``extract_text`` returns PageText objects whose ``page_number`` means
    "page" for PDF/PPTX, "paragraph" for DOCX, and 1 for the rest (see
    packages/knowledge/extractors.py). The label admits what it is: the
    locator a claim cites later must be traceable to the same block scheme.
    """
    lines: list[str] = []
    for block in blocks:
        page = getattr(block, "page_number", None)
        text = getattr(block, "text", "")
        if not text:
            continue
        lines.append(f"({kind} {page}): {text}")
    return "\n".join(lines)


def _build_user_prompt(
    paper_text: str,
    truncated: bool,
    output_language: str,
) -> str:
    lines = [
        "Read the uploaded paper text below and state what it is: its title, "
        "its research question, its main claims, and the evidence the paper "
        "offers for each. Do not judge the claims yet -- the council will "
        "critique them. Do not invent evidence the text does not contain; "
        "if a claim's support is unclear or missing, put it in "
        "`unverifiable` instead.",
        "",
    ]
    if truncated:
        lines.append(
            "NOTE: the paper was truncated for length; say so implicitly by "
            "only covering what is actually present, and add a note to "
            "`unverifiable` that the full text was not seen."
        )
        lines.append("")
    lines.append(f"## Uploaded paper text\n\n{paper_text}")
    return sanitize_export("\n".join(lines))


async def load_paper_text(
    session: AsyncSession,
    object_store: PrivateObjectStore,
    object_ids: Sequence[UUID],
) -> tuple[str, bool, str | None]:
    """Read and extract the uploaded objects into one labelled passage.

    Returns ``(paper_text, truncated, error_reason)``. A missing or
    unparsable object is an error -- the understanding step cannot pretend to
    read a paper its bytes could not produce (CLAUDE.md 7).
    """
    if not object_ids:
        return "", False, "task has no uploaded papers to understand"

    result = await session.execute(
        select(ObjectModel.object_key, ObjectModel.file_name).where(
            ObjectModel.id.in_(tuple(object_ids[:MAX_PAPER_OBJECTS]))
        )
    )
    rows = result.all()
    if not rows:
        return "", False, "uploaded papers not found in the object registry"

    chunks: list[str] = []
    total = 0
    truncated = False
    for object_key, file_name in rows:
        filename = file_name or "paper.pdf"
        try:
            content = object_store.retrieve(object_key)
        except ObjectNotFound:
            return "", False, f"uploaded object {object_key} missing from store"
        try:
            blocks, _ = extract_text(content, filename)
        except InvalidDocument as error:
            return "", False, str(error)
        if not blocks:
            return "", False, "uploaded paper produced no extractable text"
        # What `page_number` means depends on the format the extractor
        # dispatched on: real pages for PDF/PPTX, paragraph chunks for DOCX,
        # one block for the rest (packages/knowledge/extractors.py). The
        # locator label says which it is so a claim's cited location stays
        # traceable to the same scheme.
        suffix = PurePosixPath(filename.lower()).suffix
        locator_kind = ".docx" if suffix == ".docx" else "页"
        for block in blocks:
            text = getattr(block, "text", "") or ""
            rendered = (
                f"({filename}, {locator_kind} "
                f"{getattr(block, 'page_number', 1)}): {text}\n"
            )
            if total + len(rendered) > MAX_PAPER_TEXT_CHARS:
                # The cap is hit mid-paper: keep as much of this block as
                # fits so the summary covers what it can, and set the
                # truncated flag so the report admits partial coverage
                # (CLAUDE.md 7) instead of silently dropping the tail.
                room = MAX_PAPER_TEXT_CHARS - total
                if room > 0 and text:
                    chunks.append(text[:room])
                truncated = True
                break
            chunks.append(rendered)
            total += len(rendered)
        if truncated:
            break
    if not chunks:
        return "", False, "uploaded paper produced no extractable text"
    return "".join(chunks), truncated, None


async def understand_paper(
    session: AsyncSession,
    task_id: UUID,
    gateway: ModelGateway | None,
    object_store: PrivateObjectStore,
    output_language: str | None = None,
) -> PaperUnderstandingResult:
    """Run the one-shot understanding call and record its outcome.

    Never raises for a model failure: a failed call is recorded as a
    ``PAPER_UNDERSTANDING_FAILED`` event (or nothing at all when no gateway
    is connected) so the run continues and the report admits the gap.
    """
    task_id = canonical_uuid(task_id)
    task_row = await session.execute(
        select(ResearchTaskModel).where(ResearchTaskModel.task_id == task_id)
    )
    task = task_row.scalar_one_or_none()
    if task is None:
        return PaperUnderstandingResult(ok=False, reason="task not found")

    language = output_language or task.output_language or "auto"
    if language == "auto":
        language = detect_output_language(task.question)

    object_ids = (task.user_evidence or {}).get("pdf_object_ids") or ()
    paper_text, truncated, error = await load_paper_text(
        session, object_store, [canonical_uuid(oid) for oid in object_ids]
    )
    if error is not None:
        # A parse failure is a real, recorded gap -- not a model failure.
        try:
            await SqlEventLedger(session).append(
                task_id,
                PAPER_UNDERSTANDING_FAILED,
                {"reason": error},
                _IDEMPOTENCY_FAILED,
            )
        except Exception as ledger_error:  # noqa: BLE001
            logger.error(
                "failed to record PAPER_UNDERSTANDING_FAILED: %s", ledger_error
            )
        return PaperUnderstandingResult(ok=False, reason=error)

    if gateway is None:
        # No model provider: nothing to call, nothing failed -- the honest
        # state is "no understanding attempted". The report derives the
        # reason from the absent seats / missing event.
        return PaperUnderstandingResult(
            ok=False,
            reason="no model provider connected to the Model Gateway",
        )

    directive = OUTPUT_LANGUAGE_DIRECTIVES.get(
        language, OUTPUT_LANGUAGE_DIRECTIVES["en"]
    )
    request = ModelRequest(
        task_id=task_id,
        actor="paper_reader",
        purpose="PAPER_REVIEW_UNDERSTANDING",
        model_class=ModelClass.MEDIUM,
        messages=(
            ModelMessage(
                role="system",
                content=(
                    "You are the paper reader for a seven-seat research "
                    "council. You read the uploaded paper and state what it "
                    "claims -- you do not critique it, you do not judge its "
                    "evidence, you only report what the paper says and what "
                    "it offers in support.\n"
                    f"{directive}\n"
                    "Reply only with the requested schema."
                ),
            ),
            ModelMessage(
                role="user",
                content=_build_user_prompt(paper_text, truncated, language),
            ),
        ),
        output_schema="PaperUnderstanding",
        evidence_refs=(),
    )

    try:
        audited = AuditedModelGateway(gateway, session)
        model_result = await audited.invoke(request)
        if model_result.schema_status == SchemaStatus.QUARANTINED:
            raise ValueError(
                "paper understanding schema could not be repaired; "
                "output quarantined"
            )
        payload = dict(model_result.payload)
    except Exception as error:  # noqa: BLE001 -- a model failure is recorded, not raised
        reason = sanitize_export(str(error))[:500]
        logger.warning("paper understanding failed: %s", reason)
        try:
            await SqlEventLedger(session).append(
                task_id,
                PAPER_UNDERSTANDING_FAILED,
                {"reason": reason},
                _IDEMPOTENCY_FAILED,
            )
        except Exception as ledger_error:  # noqa: BLE001
            logger.error(
                "failed to record PAPER_UNDERSTANDING_FAILED: %s", ledger_error
            )
        return PaperUnderstandingResult(ok=False, reason=reason)

    main_claims = payload.get("main_claims")
    unverifiable = payload.get("unverifiable")
    stored_payload: dict[str, object] = {
        "title": _as_str(payload.get("title")),
        "research_question": _as_str(payload.get("research_question")),
        # The gateway freezes every nested dict into FrozenDict (lists into
        # tuples); thaw_for_serialization recurses through tuples/mappings,
        # so pass the frozen shapes through unchanged -- a *plain* list would
        # come back untouched with its FrozenDict items still frozen, and
        # JSONB would reject them (same discipline as packages/papers/packet.py).
        "main_claims": (
            thaw_for_serialization(main_claims)
            if isinstance(main_claims, (list, tuple))
            else []
        ),
        "unverifiable": (
            thaw_for_serialization(unverifiable)
            if isinstance(unverifiable, (list, tuple))
            else []
        ),
        "truncated": truncated,
    }
    try:
        await SqlEventLedger(session).append(
            task_id,
            PAPER_UNDERSTANDING_CAPTURED,
            stored_payload,
            _IDEMPOTENCY_CAPTURED,
        )
    except Exception as ledger_error:  # noqa: BLE001
        logger.error(
            "failed to record PAPER_UNDERSTANDING_CAPTURED: %s", ledger_error
        )
        return PaperUnderstandingResult(
            ok=False,
            reason="understanding recorded but could not be persisted",
        )
    return PaperUnderstandingResult(ok=True, payload=stored_payload)


async def load_paper_understanding(
    session: AsyncSession, task_id: UUID
) -> dict[str, object] | None:
    """Read the latest captured understanding for a resumed run.

    The understanding step runs once per task (its idempotency key is
    stable); a resumed run reads the event back instead of paying for the
    call again.
    """
    from packages.evidence.models import ScientificEventModel

    row = await session.scalar(
        select(ScientificEventModel)
        .where(
            ScientificEventModel.task_id == canonical_uuid(task_id),
            ScientificEventModel.event_type == PAPER_UNDERSTANDING_CAPTURED,
        )
        .order_by(ScientificEventModel.sequence.desc())
        .limit(1)
    )
    if row is None:
        return None
    return dict(row.payload)


__all__ = [
    "MAX_PAPER_TEXT_CHARS",
    "PAPER_UNDERSTANDING_CAPTURED",
    "PAPER_UNDERSTANDING_FAILED",
    "PaperUnderstandingResult",
    "load_paper_understanding",
    "load_paper_text",
    "understand_paper",
]
