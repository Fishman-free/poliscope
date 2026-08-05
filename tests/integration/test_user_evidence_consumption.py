"""The researcher's own evidence (DOIs, BibTeX) finally reaching acquisition.

``user_evidence.dois`` and ``user_evidence.bibtex_entries`` used to be
persisted and never read -- CLAUDE.md 7 treats a stored-but-unused entry as
silent data loss. This file pins the behaviour that replaced that: explicit
DOIs and BibTeX-extracted DOIs enter the acquisition round in their own pass
(``SourceAcquisition.acquire_dois``), deduplicate against the seat-request
pass, and can reach Level A through the ordinary finding extractor.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.epistemo.contracts import TaskStatus
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.models import ScientificEventModel
from packages.papers.models import SourceModel
from packages.research.models import AtomicClaimModel, ResearchTaskModel
from packages.research.repository import CLAIM_CONFIRMED
from packages.tools.adapters.normalization import normalize_doi

from tests.integration.test_seat_deliberation import (
    QUESTION,
    SHARED_COHORT_DOI,
    _ScriptedGateway,
    _StubProvider,
    _fake_fulltext_fetcher,
)

USER_DOI = "10.9876/user-supplied"


async def _seed_queued_task_with_evidence(
    sessions: async_sessionmaker[AsyncSession],
    user_evidence: Mapping[str, object],
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
                created_by="user_evidence_test",
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=50,
                user_evidence=dict(user_evidence),
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
                created_by="user_evidence_test",
            )
        )
        await session.commit()
    return task_id, claim_id


async def _run_to_terminal(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    *,
    gateway: object,
    tools: object,
    fulltext_fetcher: object = None,
) -> object:
    from tests.integration.test_seat_deliberation import _run_to_completion

    return await _run_to_completion(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        tools=tools,
        fulltext_fetcher=fulltext_fetcher,
    )


async def _source_count(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
    canonical_doi: str,
) -> int:
    async with sessions() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(SourceModel)
                .where(
                    SourceModel.task_id == task_id,
                    SourceModel.canonical_doi == canonical_doi,
                )
            )
        )


async def _user_doi_source_events(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[ScientificEventModel]:
    async with sessions() as session:
        result = await session.execute(
            select(ScientificEventModel).where(
                ScientificEventModel.task_id == task_id,
                ScientificEventModel.event_type == EvidenceNodeType.SOURCE.value,
            )
        )
        return [
            event
            for event in result.scalars()
            if event.payload.get("kind") == "user_doi"
        ]


async def test_user_dois_become_sources(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id, claim_id = await _seed_queued_task_with_evidence(
        app_sessions, {"dois": [USER_DOI]}
    )
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_to_terminal(
        app_sessions, projector_sessions, task_id, gateway=gateway, tools=_StubProvider()
    )

    assert result.run.failures == ()
    assert await _source_count(app_sessions, task_id, normalize_doi(USER_DOI)) == 1
    user_events = await _user_doi_source_events(app_sessions, task_id)
    assert len(user_events) == 1
    assert user_events[0].payload["doi"] == normalize_doi(USER_DOI)
    assert user_events[0].status == "admitted"


async def test_bibtex_dois_are_consumed_as_user_dois(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    bibtex = "@article{vogel2024,\n  doi = {10.1000/j.jadohealth.2024.01.001},\n}"
    task_id, claim_id = await _seed_queued_task_with_evidence(
        app_sessions, {"bibtex_entries": [bibtex]}
    )
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_to_terminal(
        app_sessions, projector_sessions, task_id, gateway=gateway, tools=_StubProvider()
    )

    assert result.run.failures == ()
    assert (
        await _source_count(
            app_sessions, task_id, "10.1000/j.jadohealth.2024.01.001"
        )
        == 1
    )


async def test_user_dois_deduped_against_seat_requests(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The same DOI via both paths must resolve to one Source row, with no
    idempotency-key collision between the two acquisition passes."""
    task_id, claim_id = await _seed_queued_task_with_evidence(
        app_sessions, {"dois": [SHARED_COHORT_DOI]}
    )
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_to_terminal(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        tools=_StubProvider(),
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )

    assert result.run.failures == ()
    assert result.run.final_status == TaskStatus.COMPLETED
    assert (
        await _source_count(app_sessions, task_id, normalize_doi(SHARED_COHORT_DOI))
        == 1
    )


async def test_user_dois_reach_level_a_when_extractor_configured(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
) -> None:
    task_id, claim_id = await _seed_queued_task_with_evidence(
        app_sessions, {"dois": [USER_DOI]}
    )
    gateway = _ScriptedGateway(claim_id, uuid4())

    result = await _run_to_terminal(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=gateway,
        tools=_StubProvider(),
        fulltext_fetcher=_fake_fulltext_fetcher(),
    )

    assert result.run.failures == ()
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
    assert any(
        normalize_doi(str(event.payload.get("doi", ""))) == normalize_doi(USER_DOI)
        for event in findings
    )
    assert all(event.status == "admitted" for event in findings)
