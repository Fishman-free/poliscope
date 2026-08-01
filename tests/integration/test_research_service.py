from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from packages.research.contracts import (
    EvidenceDemandType,
    ResearchBudget,
    ResearchContract,
    ResearchScope,
    UserEvidenceInput,
)
from packages.research.service import (
    ResearchService,
    UnconfirmedClaims,
)


def _valid_contract() -> ResearchContract:
    return ResearchContract(
        question="Does social media affect adolescent mental health?",
        scope=ResearchScope(
            populations=("adolescents",),
            regions=("global",),
            languages=("en",),
            date_from=date(2015, 1, 1),
            date_until=date(2025, 12, 31),
            evidence_priorities=(
                EvidenceDemandType.CORRELATION,
                EvidenceDemandType.CAUSAL_OR_REVERSE_CAUSAL,
            ),
            allow_preprints=False,
        ),
        budget=ResearchBudget(
            wall_clock_minutes=60,
            model_cost_usd=Decimal("10.00"),
            tool_call_limit=100,
            source_limit=50,
        ),
        user_evidence=UserEvidenceInput(
            dois=("10.1234/example",),
            pdf_object_ids=(uuid4(),),
        ),
    )


async def test_create_persists_contract() -> None:
    service = ResearchService()
    contract = _valid_contract()
    task = await service.create(contract)
    assert task.id is not None
    assert task.status == "DRAFT"


async def test_queue_requires_claim_confirmation() -> None:
    service = ResearchService()
    contract = _valid_contract()
    task = await service.create(contract)
    with pytest.raises(UnconfirmedClaims):
        await service.queue(task.id)


async def test_queue_succeeds_after_confirmation() -> None:
    service = ResearchService()
    contract = _valid_contract()
    task = await service.create(contract)
    claims = service.suggest_atomic_claims(task.id)
    assert len(claims) > 0
    confirmed = await service.confirm_claims(
        task.id, [c.claim_id for c in claims]
    )
    assert confirmed is True
    queued = await service.queue(task.id)
    assert queued.status == "QUEUED"


def test_suite() -> None:
    import asyncio
    asyncio.run(test_create_persists_contract())
    asyncio.run(test_queue_requires_claim_confirmation())
    asyncio.run(test_queue_succeeds_after_confirmation())
