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
from pathlib import Path
from typing import Any
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
from packages.papers.object_store import PrivateObjectStore
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
        self.executed: list[Any] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        self.flushed += 1

    async def execute(self, statement: Any) -> Any:
        self.executed.append(statement)


class _ScalarResult:
    """Minimal stand-in for the SQLAlchemy ``Result`` object ``extract_uploaded``
    reads via ``.scalar_one_or_none()`` -- the three ``select()`` lookups it
    issues (``SourceModel.object_id``, then ``ObjectModel.file_name``, then
    ``ObjectModel.object_key`` inside ``_retrieve_uploaded``) are the only
    callers that ever read the return value of ``execute()``."""

    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeUploadSession(_FakeSession):
    """Scripts ``scalar_one_or_none()`` results per ``execute()`` call, in
    call order, for ``extract_uploaded``'s three lookups (object_id, then
    file_name, then object_key). Any later ``execute()`` call (the
    dataset_id UPDATE) never has its result read, so an empty queue after
    all scalars are consumed is harmless."""

    def __init__(self, scalars: list[object]) -> None:
        super().__init__()
        self._scalars = list(scalars)

    async def execute(self, statement: Any) -> _ScalarResult:
        self.executed.append(statement)
        if self._scalars:
            return _ScalarResult(self._scalars.pop(0))
        return _ScalarResult(None)


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


class _SequencedModelGateway:
    """Returns each payload in ``payloads`` in turn, one per ``invoke`` call."""

    def __init__(
        self, payloads: list[dict[str, object]], cost_usd: Decimal = Decimal("0")
    ) -> None:
        self._payloads = list(payloads)
        self._cost = cost_usd
        self.calls: list[ModelRequest] = []

    async def invoke(self, request: ModelRequest) -> ModelResult:
        self.calls.append(request)
        payload = self._payloads[len(self.calls) - 1]
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
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
    assert result.dataset_id is None
    assert session.executed == []


async def test_dataset_identifier_detected_and_written_back_to_source_row() -> None:
    """CLAUDE.md 7.4 / README known-gaps: a real Data Availability declaration
    in the full text should reach ``SourceModel.dataset_id``, not stay None."""
    quote = "We found a significant association between screen time and anxiety."
    full_text = f"{quote}\nData Availability: ICPSR study number 37183."
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _FakeModelGateway(_valid_payload(quote))
    source_id = uuid4()
    extractor = FindingExtractor(
        session,
        tools,
        model,
        uuid4(),
        fulltext_fetcher=_fetcher(_pdf_bytes(full_text)),
    )

    result = await extractor.extract(source_id, "10.1234/example")

    assert result.ok is True
    assert result.dataset_id == "ICPSR:37183"
    assert len(session.executed) == 1
    params = session.executed[0].compile().params
    assert params["dataset_id"] == "ICPSR:37183"


async def test_dataset_identifier_written_even_when_finding_extraction_fails() -> None:
    """Detection runs off the fetched full text alone -- it must not depend on
    the model successfully producing a quote-verified finding afterward."""
    full_text = (
        "A completely different sentence is in this paper.\n"
        "Data Availability: ICPSR study number 37183."
    )
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _FakeModelGateway(
        _valid_payload("This exact quote never appears in the pdf at all.")
    )
    extractor = FindingExtractor(
        session, tools, model, uuid4(), fulltext_fetcher=_fetcher(_pdf_bytes(full_text))
    )

    result = await extractor.extract(uuid4(), "10.1234/mismatch")

    assert result.ok is False
    assert result.dataset_id == "ICPSR:37183"
    assert len(session.executed) == 1
    assert session.added == []


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


async def test_dual_extraction_agreement_persists_like_single_pass() -> None:
    """CLAUDE.md 7.4, mechanism 3 of 4: two agreeing passes are as good as one."""
    quote = "We found a significant association between screen time and anxiety."
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _SequencedModelGateway([_valid_payload(quote), _valid_payload(quote)])
    extractor = FindingExtractor(
        session, tools, model, uuid4(), fulltext_fetcher=_fetcher(_pdf_bytes(quote))
    )

    result = await extractor.extract(uuid4(), "10.1234/example", dual_extraction=True)

    assert result.ok is True
    assert result.evidence_level == "A"
    assert len(model.calls) == 2
    assert len(session.added) == 4
    assert session.flushed == 4


async def test_dual_extraction_exact_quote_mismatch_records_gap() -> None:
    quote = "We found a significant association between screen time and anxiety."
    other_quote = "Screen time was linked to worse outcomes in this sample."
    pdf_bytes = _pdf_bytes(f"{quote}\n{other_quote}")
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _SequencedModelGateway(
        [_valid_payload(quote), _valid_payload(other_quote)]
    )
    extractor = FindingExtractor(
        session, tools, model, uuid4(), fulltext_fetcher=_fetcher(pdf_bytes)
    )

    result = await extractor.extract(uuid4(), "10.1234/example", dual_extraction=True)

    assert result.ok is False
    assert "exact_quote" in result.reason
    assert "needs manual audit" in result.reason
    assert session.added == []


async def test_dual_extraction_effect_direction_mismatch_records_gap() -> None:
    quote = "We found a significant association between screen time and anxiety."
    first_payload = _valid_payload(quote)
    second_payload = dict(_valid_payload(quote))
    second_payload["effect_direction"] = "negative"
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _SequencedModelGateway([first_payload, second_payload])
    extractor = FindingExtractor(
        session, tools, model, uuid4(), fulltext_fetcher=_fetcher(_pdf_bytes(quote))
    )

    result = await extractor.extract(uuid4(), "10.1234/example", dual_extraction=True)

    assert result.ok is False
    assert "effect_direction" in result.reason
    assert "needs manual audit" in result.reason
    assert session.added == []


async def test_dual_extraction_second_pass_failure_records_gap() -> None:
    """The second pass's quote is not in the source text -- a Level A failure,
    not a disagreement -- so this hits the second.ok is False branch, not the
    comparison branch."""
    quote = "We found a significant association between screen time and anxiety."
    session = _FakeSession()
    tools = _FakeToolGateway(url="https://example.test/paper.pdf")
    model = _SequencedModelGateway(
        [_valid_payload(quote), _valid_payload("this quote never appears anywhere")]
    )
    extractor = FindingExtractor(
        session, tools, model, uuid4(), fulltext_fetcher=_fetcher(_pdf_bytes(quote))
    )

    result = await extractor.extract(uuid4(), "10.1234/example", dual_extraction=True)

    assert result.ok is False
    assert "second pass failed" in result.reason
    assert "needs manual audit" in result.reason
    assert session.added == []
    assert session.added == []


async def test_extract_uploaded_success_persists_four_rows(tmp_path: Path) -> None:
    """Mirrors test_extract_success_persists_three_rows_and_returns_finding, but
    through the uploaded-PDF path: no Unpaywall lookup, no fulltext fetch --
    the object store is the only source of bytes."""
    quote = "We found a significant association between screen time and anxiety."
    store = PrivateObjectStore(root=str(tmp_path))
    task_id = uuid4()
    stored = store.store(task_id=task_id, content=_pdf_bytes(quote))
    source_id = uuid4()
    object_id = uuid4()
    session = _FakeUploadSession([object_id, None, stored.object_key])
    tools = _FakeToolGateway(url=None)
    model = _FakeModelGateway(_valid_payload(quote))
    extractor = FindingExtractor(
        session, tools, model, task_id, object_store=store
    )

    result = await extractor.extract_uploaded(source_id, object_id)

    assert result.ok is True
    assert result.doi is None
    assert result.evidence_level == "A"
    assert isinstance(result.finding_id, UUID)
    assert len(session.added) == 4
    assert session.flushed == 4
    assert tools.calls == []  # no open-access lookup on the uploaded path


async def test_extract_uploaded_source_without_object_records_gap(
    tmp_path: Path,
) -> None:
    """SourceModel.object_id is None -- nothing was ever uploaded for this
    source -- so extraction must record a gap, not raise or fabricate."""
    store = PrivateObjectStore(root=str(tmp_path))
    session = _FakeUploadSession([None])
    tools = _FakeToolGateway(url=None)
    model = _FakeModelGateway(_valid_payload("irrelevant"))
    extractor = FindingExtractor(
        session, tools, model, uuid4(), object_store=store
    )

    result = await extractor.extract_uploaded(uuid4(), uuid4())

    assert result.ok is False
    assert "no uploaded object" in result.reason
    assert session.added == []
    assert model.calls == []


async def test_extract_uploaded_missing_object_row_records_gap(
    tmp_path: Path,
) -> None:
    """The source names an object_id, but that objects row itself is gone --
    a distinct gap from the file simply being missing from disk."""
    store = PrivateObjectStore(root=str(tmp_path))
    session = _FakeUploadSession([uuid4(), None, None])
    tools = _FakeToolGateway(url=None)
    model = _FakeModelGateway(_valid_payload("irrelevant"))
    extractor = FindingExtractor(
        session, tools, model, uuid4(), object_store=store
    )

    result = await extractor.extract_uploaded(uuid4(), uuid4())

    assert result.ok is False
    assert "not found in private object store" in result.reason
    assert session.added == []


async def test_extract_uploaded_missing_file_on_disk_records_gap(
    tmp_path: Path,
) -> None:
    """The objects row exists and names a real-shaped key, but the store
    (CLAUDE.md 16-adjacent: retrieve() must fail closed, not raise past the
    caller) never actually has bytes under that key on disk."""
    store = PrivateObjectStore(root=str(tmp_path))
    session = _FakeUploadSession([uuid4(), None, f"tasks/{uuid4()}/never-written.pdf"])
    tools = _FakeToolGateway(url=None)
    model = _FakeModelGateway(_valid_payload("irrelevant"))
    extractor = FindingExtractor(
        session, tools, model, uuid4(), object_store=store
    )

    result = await extractor.extract_uploaded(uuid4(), uuid4())

    assert result.ok is False
    assert "not found in private object store" in result.reason
    assert session.added == []


async def test_extract_uploaded_pdf_parse_failure_records_gap(tmp_path: Path) -> None:
    store = PrivateObjectStore(root=str(tmp_path))
    task_id = uuid4()
    stored = store.store(
        task_id=task_id, content=b"%PDF-1.4\nthis is not a real pdf body\n%%EOF"
    )
    session = _FakeUploadSession([uuid4(), None, stored.object_key])
    tools = _FakeToolGateway(url=None)
    model = _FakeModelGateway(_valid_payload("irrelevant"))
    extractor = FindingExtractor(
        session, tools, model, task_id, object_store=store
    )

    result = await extractor.extract_uploaded(uuid4(), uuid4())

    assert result.ok is False
    assert "pdf" in result.reason
    assert model.calls == []
    assert session.added == []


async def test_extract_uploaded_quote_not_locatable_records_gap(
    tmp_path: Path,
) -> None:
    """CLAUDE.md 7.3, uploaded-path parity with the DOI path's equivalent test."""
    store = PrivateObjectStore(root=str(tmp_path))
    task_id = uuid4()
    stored = store.store(
        task_id=task_id,
        content=_pdf_bytes("A completely different sentence is in this paper."),
    )
    session = _FakeUploadSession([uuid4(), None, stored.object_key])
    tools = _FakeToolGateway(url=None)
    model = _FakeModelGateway(
        _valid_payload("This exact quote never appears in the pdf at all.")
    )
    extractor = FindingExtractor(
        session, tools, model, task_id, object_store=store
    )

    result = await extractor.extract_uploaded(uuid4(), uuid4())

    assert result.ok is False
    assert "not found in source text" in result.reason
    assert session.added == []


async def test_extract_uploaded_dataset_identifier_detected_and_written_back(
    tmp_path: Path,
) -> None:
    """Parity with test_dataset_identifier_detected_and_written_back_to_source_row,
    through the uploaded path -- dataset detection must not be an open-access-
    fetch-only behaviour."""
    quote = "We found a significant association between screen time and anxiety."
    full_text = f"{quote}\nData Availability: ICPSR study number 37183."
    store = PrivateObjectStore(root=str(tmp_path))
    task_id = uuid4()
    stored = store.store(task_id=task_id, content=_pdf_bytes(full_text))
    source_id = uuid4()
    session = _FakeUploadSession([uuid4(), None, stored.object_key])
    tools = _FakeToolGateway(url=None)
    model = _FakeModelGateway(_valid_payload(quote))
    extractor = FindingExtractor(
        session, tools, model, task_id, object_store=store
    )

    result = await extractor.extract_uploaded(source_id, uuid4())

    assert result.ok is True
    assert result.dataset_id == "ICPSR:37183"
    # Three selects (object_id, file_name, object_key) plus one UPDATE for
    # the dataset_id write-back -- distinct from the DOI path's single
    # select-free UPDATE count, since extract_uploaded has the extra object
    # lookups.
    assert len(session.executed) == 4
    params = session.executed[-1].compile().params
    assert params["dataset_id"] == "ICPSR:37183"


async def test_extract_injects_researcher_skill_into_system_prompt() -> None:
    """Round-5 request: an enabled skill instructs *every* process that calls
    a model, extraction included -- not just the council. The skill text is
    rendered into the extraction request's system prompt with the same
    non-evidence labelling as the council's rendering."""
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
        researcher_skills=(
            (
                "measurement-deep-dive",
                "Always report the exact measurement instrument.",
            ),
        ),
    )

    result = await extractor.extract(uuid4(), "10.1234/example")

    assert result.ok is True
    assert len(model.calls) == 1
    system_content = model.calls[0].messages[0].content
    # Same non-evidence labelling as the council's rendering, so a skill can
    # never be mistaken for a source that supports a claim.
    assert "技能指令（非正式证据，来源：measurement-deep-dive）" in system_content
    assert "Always report the exact measurement instrument." in system_content
