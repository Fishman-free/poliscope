from __future__ import annotations

from datetime import date
from uuid import uuid4

from apps.api.schemas import (
    ConfirmClaimsRequest,
    CreateTaskRequest,
    TaskResponse,
)


def test_create_task_request_validates_question() -> None:
    req = CreateTaskRequest(
        question="Does social media affect mental health?",
        scope={
            "populations": ["adolescents"],
            "regions": ["global"],
            "languages": ["en"],
            "date_from": str(date(2015, 1, 1)),
            "date_until": str(date(2025, 12, 31)),
            "evidence_priorities": ["CORRELATION"],
            "allow_preprints": False,
        },
        budget={
            "wall_clock_minutes": 60,
            "model_cost_usd": "10.00",
            "tool_call_limit": 100,
            "source_limit": 50,
        },
        user_evidence={"dois": [], "bibtex_entries": [], "pdf_object_ids": []},
    )
    assert req.question.startswith("Does")


def test_confirm_claims_request_requires_ids() -> None:
    req = ConfirmClaimsRequest(claim_ids=[uuid4(), uuid4()])
    assert len(req.claim_ids) == 2


def test_task_response_has_required_fields() -> None:
    resp = TaskResponse(
        id=uuid4(),
        question="test",
        status="DRAFT",
        created_by="user",
    )
    assert resp.status == "DRAFT"


def test_suite() -> None:
    test_create_task_request_validates_question()
    test_confirm_claims_request_requires_ids()
    test_task_response_has_required_fields()
