"""Two papers sharing a Data Availability accession get linked by SAME_DATASET.

Until ``packages.papers.finding_extraction`` wrote a detected dataset
identifier back onto ``SourceModel.dataset_id``, this column was always
``None`` (README known gaps) and ``packages.evidence.lineage_detection``'s
``SAME_DATASET`` merging -- fully implemented since it first landed -- had no
real input to merge on. This proves the whole path end to end, against a real
Postgres: two independently acquired sources whose full text both declare the
same ICPSR accession collapse into one independent-evidence cluster in the
workspace snapshot the frontend actually reads (CLAUDE.md 7.4).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import fitz  # type: ignore[import-untyped]
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.worker.jobs import run_task
from packages.epistemo.contracts import TaskPhase, TaskStatus
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus
from packages.papers.models import SourceModel
from packages.research.models import ResearchTaskModel
from packages.tools.contracts import ToolRequest, ToolResult
from packages.tools.fulltext_fetcher import FullTextFetcher

QUESTION = "Does adolescent social media use cause depressive symptoms?"
FIRST_DOI = "10.1234/first-cohort"
SECOND_DOI = "10.1234/second-cohort"
SHARED_ACCESSION_TEXT = "Data Availability: ICPSR study number 37183."


class _RequestingGateway:
    """Model gateway whose only scripted phase is ACQUISITION, requesting both
    DOIs -- every other phase (including finding extraction) answers with an
    empty payload, the same convention as ``test_source_acquisition.py``'s
    double of the same name, since nothing past acquisition/extraction is
    under test here: dataset-identifier detection runs off the fetched full
    text alone, before any model call, so it does not depend on the finding
    extraction itself succeeding.
    """

    def __init__(self, dois: tuple[str, ...]) -> None:
        self._dois = dois

    async def invoke(self, request: ModelRequest) -> ModelResult:
        payload: dict[str, object] = {}
        if TaskPhase(request.purpose) is TaskPhase.ACQUISITION:
            payload = {"requests": [f"doi {doi}" for doi in self._dois]}
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            input_tokens=10,
            output_tokens=5,
            cost_usd=Decimal("0"),
            latency_ms=1,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


class _ProviderGateway:
    """Stands in for OpenAlex (metadata) and Unpaywall (OA url) both, keyed by
    ``tool_name`` -- the same two providers ``packages.papers.acquisition``
    and ``packages.papers.finding_extraction`` each call exactly once per
    source.
    """

    def __init__(self, oa_url: str) -> None:
        self._oa_url = oa_url
        self.calls: list[ToolRequest] = []

    async def execute(self, request: ToolRequest) -> ToolResult:
        self.calls.append(request)
        doi = str(request.arguments["doi"])
        if request.tool_name == "unpaywall":
            return ToolResult(
                call_id=uuid4(),
                payload=FrozenDict({"url": self._oa_url}),
                latency_ms=1,
                retries=0,
                error_code=None,
            )
        return ToolResult(
            call_id=uuid4(),
            payload=FrozenDict(
                {
                    "id": f"https://openalex.org/{doi}",
                    "title": f"Study of {doi}",
                    "authors": ("A. Researcher",),
                    "year": 2021,
                    "type": "journal-article",
                    "retracted": False,
                }
            ),
            latency_ms=3,
            retries=0,
            error_code=None,
        )


def _fetcher_with_shared_accession() -> FullTextFetcher:
    """Every fetch (regardless of which source's URL is requested) returns a
    PDF declaring the same ICPSR accession -- both sources' finding-extraction
    attempts run this detection independently, so ending up with a shared
    ``dataset_id`` proves real per-source detection, not one write reused
    across rows.
    """
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), SHARED_ACCESSION_TEXT)
    content = bytes(document.tobytes())

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FullTextFetcher(client=client)


async def _seed(
    sessions: async_sessionmaker[AsyncSession],
    user_id: UUID,
) -> UUID:
    task_id = uuid4()
    async with sessions() as session:
        session.add(
            ResearchTaskModel(
                id=uuid4(),
                task_id=task_id,
                question=QUESTION,
                status=TaskStatus.QUEUED,
                created_by="dataset_lineage_test",
                user_id=user_id,
                wall_clock_minutes=60,
                model_cost_usd=Decimal("10.0000"),
                tool_call_limit=100,
                source_limit=50,
                user_evidence={},
            )
        )
        await session.commit()
    return task_id


async def _sources(
    sessions: async_sessionmaker[AsyncSession],
    task_id: UUID,
) -> list[SourceModel]:
    async with sessions() as session:
        result = await session.execute(
            select(SourceModel).where(SourceModel.task_id == task_id)
        )
        return list(result.scalars())


async def test_shared_dataset_identifier_is_detected_on_both_sources(
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """The regression this plan phase fixes: dataset_id must stop being
    always-None once full text carries a real Data Availability declaration."""
    task_id = await _seed(app_sessions, UUID(account["id"]))

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_RequestingGateway((FIRST_DOI, SECOND_DOI)),
        tools=_ProviderGateway(oa_url="https://example.test/paper.pdf"),
        fulltext_fetcher=_fetcher_with_shared_accession(),
    )

    sources = await _sources(app_sessions, task_id)
    assert {source.canonical_doi for source in sources} == {FIRST_DOI, SECOND_DOI}
    assert {source.dataset_id for source in sources} == {"ICPSR:37183"}


async def test_shared_dataset_identifier_merges_the_two_papers_into_one_cluster(
    api_client: Any,
    app_sessions: async_sessionmaker[AsyncSession],
    projector_sessions: async_sessionmaker[AsyncSession],
    account: dict[str, Any],
) -> None:
    """CLAUDE.md 7.4: the interface must show paper count and independent
    cluster count separately, and two papers sharing a dataset must collapse
    into a single independent-evidence cluster instead of reporting two."""
    task_id = await _seed(app_sessions, UUID(account["id"]))

    await run_task(
        app_sessions,
        projector_sessions,
        task_id,
        gateway=_RequestingGateway((FIRST_DOI, SECOND_DOI)),
        tools=_ProviderGateway(oa_url="https://example.test/paper.pdf"),
        fulltext_fetcher=_fetcher_with_shared_accession(),
    )

    body = (await api_client.get(f"/api/workspace/{task_id}")).json()

    assert body["paper_count"] == 2
    assert body["independent_cluster_count"] == 1
