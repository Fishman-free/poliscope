"""Knowledge-base documents entering the council as Level A sources.

A task linked to a knowledge base (``research_tasks.knowledge_base_id``)
feeds its documents into the acquisition round as user-provided sources
(``sources.knowledge_document_id``), runs each seat's requests against the
collection as keyword search, and carries hits into later phases' prompts as
explicitly non-evidence process context.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import fitz  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import JobResult
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.models import GraphNodeModel, ScientificEventModel
from packages.knowledge.models import KnowledgeBaseModel, KnowledgeDocumentModel
from packages.papers.acquisition import KnowledgeDocumentRef, SourceAcquisition
from packages.papers.models import SourceModel
from packages.papers.object_store import PrivateObjectStore
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED
from packages.tools.contracts import ToolGateway
from packages.tools.fulltext_fetcher import FullTextFetcher
from packages.tools.models import ToolCallModel
from tests.integration.test_seat_deliberation import (
    QUESTION,
    SHARED_COHORT_DOI,
    SHARED_COHORT_QUOTE,
    _fake_fulltext_fetcher,
    _run_to_completion,
    _ScriptedGateway,
    _StubProvider,
)

# The seat requests "doi 10.1234/shared-cohort" (see _ScriptedGateway's
# ACQUISITION answer); the document carries that DOI plus the quote the
# extractor claims, so both the keyword-search pass and the Level A
# extraction can hit it.
DOCUMENT_TEXT = (
    f"{SHARED_COHORT_QUOTE} "
    "Participants were adolescents aged 12-18. DOI: "
    f"{SHARED_COHORT_DOI}."
)
DOCUMENT_TITLE = "cohort-study.pdf"


def _pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    return bytes(document.tobytes())


async def _seed_knowledge_base_with_document(
    sessions: async_sessionmaker[AsyncSession],
    text_content: str = DOCUMENT_TEXT,
) -> UUID:
    """Seed a knowledge base whose document also exists on disk in the
    default private object store -- the worker's extract_knowledge_document
    reads the bytes back by object_key, so a DB-only row would be a gap."""
    kb_id = uuid4()
    stored = PrivateObjectStore().store_named(
        f"knowledge/{kb_id}", _pdf_bytes(text_content)
    )
    async with sessions() as session:
        session.add(
            KnowledgeBaseModel(
                id=kb_id,
                name="integration kb",
                created_by="acquisition_test",
            )
        )
        await session.flush()
        session.add(
            KnowledgeDocumentModel(
                id=uuid4(),
                knowledge_base_id=kb_id,
                title=DOCUMENT_TITLE,
                object_key=stored.object_key,
                content_hash=stored.content_hash,
                content_type=stored.content_type,
                size_bytes=stored.size_bytes,
                page_count=1,
                text_content=text_content,
                created_by="acquisition_test",
            )
        )
        await session.commit()
    return kb_id


async def _seed_queued_task_with_kb(
    sessions: async_sessionmaker[AsyncSession],
    kb_id: UUID,
) -> tuple[UUID, UUID]:
    task_id = uuid4()
    claim_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="knowledge_acquisition_test",
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=50,
                user_evidence={},
                knowledge_base_id=kb_id,
            )
        )
        await session.flush()
        session.add(
            AtomicClaimModel(
                id=claim_id,
                task_id=task_id,
                statement="Heavy use predicts higher depressive symptom scores.",
                claim_type="correlational",
                scope={"population": "adolescents"},
                falsification_condition="A preregistered cohort finds a null effect.",
                status=CLAIM_CONFIRMED,
                created_by="knowledge_acquisition_test",
            )
        )
        await session.commit()
    return task_id, claim_id


async def _run_with_kb(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    gateway: _ScriptedGateway,
    *,
    tools: ToolGateway | None = None,
    fulltext_fetcher: FullTextFetcher | None = None,
) -> JobResult:
    return await _run_to_completion(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        tools=tools or _StubProvider(),
        fulltext_fetcher=fulltext_fetcher,
    )


async def _knowledge_sources(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[SourceModel]:
    async with sessions() as session:
        result = await session.execute(
            select(SourceModel).where(
                SourceModel.task_id == task_id,
                SourceModel.knowledge_document_id.is_not(None),
            )
        )
        return list(result.scalars())


async def test_knowledge_documents_become_level_a_sources(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    kb_id = await _seed_knowledge_base_with_document(app_sessions)
    task_id, claim_id = await _seed_queued_task_with_kb(app_sessions, kb_id)
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_with_kb(
        app_sessions,
        projector_sessions,
        task_id,
        gateway,
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )

    assert result.run.failures == ()
    sources = await _knowledge_sources(app_sessions, task_id)
    assert len(sources) == 1
    assert sources[0].title == DOCUMENT_TITLE

    # The extraction reached Level A: a STUDY_FINDING event carrying the
    # document id, admitted onto the graph.
    async with app_sessions() as session:
        findings = (
            await session.execute(
                select(ScientificEventModel).where(
                    ScientificEventModel.task_id == task_id,
                    ScientificEventModel.event_type
                    == EvidenceNodeType.STUDY_FINDING.value,
                )
            )
        ).scalars().all()
    kb_findings = [
        event
        for event in findings
        if event.payload.get("knowledge_document_id") is not None
    ]
    assert len(kb_findings) == 1
    assert kb_findings[0].status == "admitted"

    async with app_sessions() as session:
        source_nodes = (
            await session.execute(
                select(GraphNodeModel).where(
                    GraphNodeModel.task_id == task_id,
                    GraphNodeModel.node_type == EvidenceNodeType.SOURCE.value,
                )
            )
        ).scalars().all()
    # The knowledge-document source is Level B at acquisition time (metadata
    # only -- full text is read when the finding extractor runs), so the gate
    # admits it as SOURCE_ONLY / provisional, and the STUDY_FINDING that
    # follows carries it to Level A. Either way the node must carry the
    # document id so the interface can trace the source back to the library.
    assert any(
        node.payload.get("knowledge_document_id") is not None
        for node in source_nodes
    )


async def test_knowledge_base_does_not_suppress_external_retrieval(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Round-4 requirement: a task linked to a knowledge base still runs the
    external literature retrieval. The KB is an *additional* channel (Level A
    documents + keyword-search hits), never a replacement for OpenAlex /
    Crossref / Semantic Scholar -- the external tool gateway must be called
    exactly as it would be without a KB."""
    kb_id = await _seed_knowledge_base_with_document(app_sessions)
    task_id, claim_id = await _seed_queued_task_with_kb(app_sessions, kb_id)
    result = await _run_with_kb(
        app_sessions,
        projector_sessions,
        task_id,
        _ScriptedGateway(claim_id, uuid4()),
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )

    assert result.run.failures == ()
    # The ScriptedGateway's ACQUISITION answer requests the shared-cohort DOI,
    # which has no DOI in the KB document path -- only the external adapter
    # can resolve it, so the audited tool_calls rows prove external retrieval
    # ran even with a knowledge base attached.
    async with app_sessions() as session:
        external = (
            await session.execute(
                select(ToolCallModel).where(
                    ToolCallModel.task_id == task_id,
                    ToolCallModel.tool_name == "openalex",
                    ToolCallModel.operation == "lookup_doi",
                )
            )
        ).scalars().all()
    assert external, "no external DOI lookup happened for a KB task"
    # And the KB document still became a Level A source: the two channels are
    # additive.
    assert len(await _knowledge_sources(app_sessions, task_id)) == 1


async def test_knowledge_document_acquisition_is_idempotent_on_replay(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A replayed acquisition must reuse the Source row, not mint a second
    one -- the parallel of acquire_uploaded's dedup by object id."""
    kb_id = await _seed_knowledge_base_with_document(app_sessions)
    task_id, claim_id = await _seed_queued_task_with_kb(app_sessions, kb_id)
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_with_kb(
        app_sessions,
        projector_sessions,
        task_id,
        gateway,
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )
    assert result.run.failures == ()
    assert len(await _knowledge_sources(app_sessions, task_id)) == 1

    # Directly re-run the acquisition pass against a fresh session with the
    # real document id: everything is already_known, nothing new is persisted.
    async with app_sessions() as session:
        row = (
            await session.execute(
                select(KnowledgeDocumentModel).where(
                    KnowledgeDocumentModel.knowledge_base_id == kb_id
                )
            )
        ).scalar_one()
        refs = (KnowledgeDocumentRef(row.id, row.object_key, row.title),)
        acquisition = SourceAcquisition(session, _StubProvider(), task_id)
        replay = await acquisition.acquire_knowledge_documents(refs)
        assert len(replay.acquired) == 1
        assert replay.acquired[0].already_known is True


async def _seed_knowledge_base_with_text_document(
    sessions: async_sessionmaker[AsyncSession],
    text_content: str = DOCUMENT_TEXT,
) -> UUID:
    """Seed a knowledge base whose document is pasted text.

    Deliberately no object-store file behind the (virtual) object key: the
    extractor's text-content branch must read ``text_content`` directly, or
    this becomes a gap.
    """
    kb_id = uuid4()
    async with sessions() as session:
        session.add(
            KnowledgeBaseModel(
                id=kb_id,
                name="text kb",
                created_by="acquisition_test",
            )
        )
        await session.flush()
        session.add(
            KnowledgeDocumentModel(
                id=uuid4(),
                knowledge_base_id=kb_id,
                title="pasted-notes.txt",
                object_key=f"knowledge/{kb_id}/text/{uuid4().hex}.txt",
                content_hash=uuid4().hex,
                content_type="text/plain",
                size_bytes=len(text_content.encode("utf-8")),
                page_count=1,
                text_content=text_content,
                created_by="acquisition_test",
            )
        )
        await session.commit()
    return kb_id


async def test_pasted_text_document_becomes_level_a_source(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A pasted-text document (no PDF, no object-store bytes) must still reach
    Level A -- the extractor reads text_content instead of parsing a PDF."""
    kb_id = await _seed_knowledge_base_with_text_document(app_sessions)
    task_id, claim_id = await _seed_queued_task_with_kb(app_sessions, kb_id)
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_with_kb(
        app_sessions,
        projector_sessions,
        task_id,
        gateway,
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )
    assert result.run.failures == ()

    sources = await _knowledge_sources(app_sessions, task_id)
    assert len(sources) == 1
    assert sources[0].title == "pasted-notes.txt"

    async with app_sessions() as session:
        findings = (
            await session.execute(
                select(ScientificEventModel).where(
                    ScientificEventModel.task_id == task_id,
                    ScientificEventModel.event_type
                    == EvidenceNodeType.STUDY_FINDING.value,
                )
            )
        ).scalars().all()
    kb_findings = [
        event
        for event in findings
        if event.payload.get("knowledge_document_id") is not None
    ]
    assert len(kb_findings) == 1
    assert kb_findings[0].status == "admitted"


async def test_knowledge_search_hits_reach_later_phase_prompts(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    kb_id = await _seed_knowledge_base_with_document(app_sessions)
    task_id, claim_id = await _seed_queued_task_with_kb(app_sessions, kb_id)
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_with_kb(
        app_sessions,
        projector_sessions,
        task_id,
        gateway,
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )
    assert result.run.failures == ()

    # The seats' "doi {SHARED_COHORT_DOI}" request is ILIKE-matched against
    # the document, and the hit is carried into every later phase's prompt,
    # explicitly labelled as non-evidence process context. (Finding-extractor
    # calls are filtered out first: their purpose is not a TaskPhase value.)
    later_prompts = [
        request.messages[1].content
        for request in gateway.calls
        if request.output_schema != "StudyFindingExtraction"
        and request.output_schema != "FinalPaper"
        and TaskPhase(request.purpose) is TaskPhase.FINAL_REJUDGMENT
    ]
    assert later_prompts
    assert all("研究者知识库检索命中" in prompt for prompt in later_prompts)
    assert all(DOCUMENT_TITLE in prompt for prompt in later_prompts)
    # The label must make the non-evidence status explicit, and no phase
    # before acquisition can have seen a hit.
    assert all("非正式证据" in prompt for prompt in later_prompts)
    precommitment_prompts = [
        request.messages[1].content
        for request in gateway.calls
        if request.output_schema != "StudyFindingExtraction"
        and request.output_schema != "FinalPaper"
        and TaskPhase(request.purpose) is TaskPhase.PRECOMMITMENT
    ]
    assert all("研究者知识库检索命中" not in prompt for prompt in precommitment_prompts)


async def test_knowledge_search_miss_records_nothing(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A collection the request cannot match contributes no context at all --
    an empty hit list must not read as coverage (CLAUDE.md 7)."""
    kb_id = await _seed_knowledge_base_with_document(
        app_sessions, text_content="Unrelated content about exercise."
    )
    task_id, claim_id = await _seed_queued_task_with_kb(app_sessions, kb_id)
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_with_kb(
        app_sessions, projector_sessions, task_id, gateway
    )

    assert result.run.failures == ()
    later_prompts = [
        request.messages[1].content
        for request in gateway.calls
        if request.output_schema != "StudyFindingExtraction"
        and request.output_schema != "FinalPaper"
        and TaskPhase(request.purpose) is TaskPhase.FINAL_REJUDGMENT
    ]
    assert all("研究者知识库检索命中" not in prompt for prompt in later_prompts)
