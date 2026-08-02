"""Tests for the FindingExtractor pipeline.

Drives ``extract_pages``/``locate_quote`` through real PyMuPDF code paths
using a PDF built in-memory by ``fitz`` (no committed binary fixture), and a
``FullTextFetcher`` wired to ``httpx.MockTransport`` (no live network),
matching the isolation discipline of ``tests/unit/test_fulltext_fetcher.py``.
The Model/Tool gateways are hand-written fakes rather than the real
vendor-backed implementations, since this module's own logic -- not gateway
plumbing -- is what is under test here.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import fitz  # type: ignore[import-untyped]
import httpx
import pytest

from packages.epistemo.budget import BudgetTracker, ResearchBudget
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import (
    ModelRequest,
    ModelResult,
    SchemaStatus,
)
from packages.papers.finding_extraction import FindingExtractor
from packages.tools.contracts import ToolRequest, ToolResult
from packages.tools.fulltext_fetcher import FullTextFetcher


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr("packages.kernel.http_retry.asyncio.sleep", _instant)


def _pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    return bytes(document.tobytes())


def _fetcher(content: bytes) -> FullTextFetcher:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FullTextFetcher(client=client)


def _valid_payload(quote: str) -> dict[str, object]:
    return {
        "study_question": "Does screen time affect wellbeing?",
        "population": "adolescents",
        "design": "cross_sectional",
        "exposure_variable": "screen_time",
        "outcome_variable": "anxiety",
        "analysis_method": "linear regression",
        "finding_statement": "Screen time correlates with anxiety.",
        "origin": "SOURCE_TEXT",
        "effect_direction": "positive",
        "exact_quote": quote,
        "author_conclusions": ["Screen time matters."],
        "author_limitations": ["Self-reported."],
        "data_availability": "restricted",
        "code_availability": "unavailable",
        "preregistration": "not_reported",
        "method_quality": {
            "directness": 0.8,
            "design_quality": 0.75,
            "measurement_quality": 0.7,
            "precision": 0.65,
            "replicability": 0.6,
            "external_validity": 0.55,
        },
    }


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushed = 0

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flushed += 1


class _FakeToolGateway:
    """Fake Unpaywall-shaped ToolGateway. ``url=None`` simulates no OA copy."""

    def __init__(self, url: str | None) -> None:
        self._url = url
        self.calls: list[ToolRequest] = []

    async def execute(self, request: ToolRequest) -> ToolResult:
        self.calls.append(request)
        payload: dict[str, object] = {"url": self._url} if self._url else {}
        return ToolResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            latency_ms=1,
            retries=0,
            error_code=None,
        )


class _FakeModelGateway:
    def __init__(
        self, payload: dict[str, object], cost_usd: Decimal = Decimal("0")
    ) -> None:
        self._payload = payload
        self._cost = cost_usd
        self.calls: list[ModelRequest] = []

    async def invoke(self, request: ModelRequest) -> ModelResult:
        self.calls.append(request)
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(self._payload),
            input_tokens=10,
            output_tokens=10,
            cost_usd=self._cost,
            latency_ms=5,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


def _budget(
    tool_call_limit: int = 10, model_cost_usd: Decimal = Decimal("10")
) -> BudgetTracker:
    return BudgetTracker(
        ResearchBudget(
            wall_clock_minutes=10,
            model_cost_usd=model_cost_usd,
            tool_call_limit=tool_call_limit,
            source_limit=10,
        )
    )


async def test_extract_success_persists_three_rows_and_returns_finding() -> None:
    quote = "We found a significant association between screen time and anxiety."
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _FakeModelGateway(_valid_payload(quote))
    extractor = FindingExtractor(
        session,
        tools,
        model,
        uuid4(),
        fulltext_fetcher=_fetcher(_pdf_bytes(quote)),
    )

    result = await extractor.extract(uuid4(), "10.1234/example")

    assert result.ok is True
    assert result.evidence_level == "A"
    assert isinstance(result.finding_id, UUID)
    assert isinstance(result.study_id, UUID)
    assert result.method_quality["directness"] == 0.8
    # SourceVersion, Study, Finding, CitationAnchor -- four rows, flushed one
    # at a time so each parent is visible before its child is inserted (no
    # relationship() is declared between these models for SQLAlchemy to infer
    # the order itself; see the comment in finding_extraction.py).
    assert len(session.added) == 4
    assert session.flushed == 4


async def test_no_open_access_url_records_gap_without_spending_model_budget() -> None:
    session = _FakeSession()
    tools = _FakeToolGateway(url=None)
    model = _FakeModelGateway(_valid_payload("irrelevant"))
    extractor = FindingExtractor(
        session, tools, model, uuid4(), fulltext_fetcher=_fetcher(b"unused")
    )

    result = await extractor.extract(uuid4(), "10.1234/missing")

    assert result.ok is False
    assert "open access" in result.reason
    assert model.calls == []
    assert session.added == []


async def test_pdf_parse_failure_records_gap_not_exception() -> None:
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _FakeModelGateway(_valid_payload("irrelevant"))
    broken_pdf = b"%PDF-1.4\nthis is not a real pdf body\n%%EOF"
    extractor = FindingExtractor(
        session, tools, model, uuid4(), fulltext_fetcher=_fetcher(broken_pdf)
    )

    result = await extractor.extract(uuid4(), "10.1234/broken")

    assert result.ok is False
    assert "pdf" in result.reason
    assert model.calls == []
    assert session.added == []


async def test_quote_not_locatable_records_gap_without_persisting() -> None:
    """CLAUDE.md 7.3: an unlocatable quote must not become a formal result."""
    pdf_bytes = _pdf_bytes("A completely different sentence is in this paper.")
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _FakeModelGateway(
        _valid_payload("This exact quote never appears in the pdf at all.")
    )
    extractor = FindingExtractor(
        session, tools, model, uuid4(), fulltext_fetcher=_fetcher(pdf_bytes)
    )

    result = await extractor.extract(uuid4(), "10.1234/mismatch")

    assert result.ok is False
    assert "not found in source text" in result.reason
    assert session.added == []


async def test_tool_call_budget_exhausted_before_lookup_skips_gateway() -> None:
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _FakeModelGateway(_valid_payload("x"))
    extractor = FindingExtractor(
        session,
        tools,
        model,
        uuid4(),
        _budget(tool_call_limit=0),
        fulltext_fetcher=_fetcher(b"unused"),
    )

    result = await extractor.extract(uuid4(), "10.1234/nobudget")

    assert result.ok is False
    assert "budget" in result.reason
    assert tools.calls == []
    assert model.calls == []


async def test_model_cost_budget_exhausted_blocks_persistence() -> None:
    quote = "We found a significant association between screen time and anxiety."
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _FakeModelGateway(_valid_payload(quote), cost_usd=Decimal("5"))
    extractor = FindingExtractor(
        session,
        tools,
        model,
        uuid4(),
        _budget(model_cost_usd=Decimal("1")),
        fulltext_fetcher=_fetcher(_pdf_bytes(quote)),
    )

    result = await extractor.extract(uuid4(), "10.1234/expensive")

    assert result.ok is False
    assert "model cost budget exhausted" in result.reason
    assert session.added == []
