from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

from tests.factories import make_research_contract

ROOT = Path(__file__).resolve().parents[1]
APP_ROLE = "poliscope_app"
PROJECTOR_ROLE = "poliscope_projector"
APP_PASSWORD = "isolated-app-test-password"
PROJECTOR_PASSWORD = "isolated-projector-test-password"


def _role_url(admin_url: str, username: str, password: str) -> str:
    url = make_url(admin_url)
    return URL.create(
        drivername="postgresql+asyncpg",
        username=username,
        password=password,
        host=url.host,
        port=url.port,
        database=url.database,
    ).render_as_string(hide_password=False)


def _alembic_config(admin_url: str) -> Config:
    os.environ["POLISCOPE_MIGRATOR_DATABASE_URL"] = admin_url
    return Config(ROOT / "alembic.ini")


def docker_is_available() -> bool:
    """Whether a Docker daemon can actually be reached.

    Checked once per session and cached by the caller. Without this the
    container-backed tests fail with a connection error that reads like a broken
    test rather than a missing prerequisite.
    """
    try:
        completed = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip the container-backed tests when there is no Docker to run them on.

    Skipped rather than passed: CLAUDE.md 12.3 forbids claiming a feature works
    on a run that did not exercise it, and a silently-green suite on a machine
    with no database would do exactly that. The skip reason says what is missing.
    """
    if not any(item.get_closest_marker("requires_docker") for item in items):
        return
    if docker_is_available():
        return
    skip = pytest.mark.skip(
        reason="needs a Docker daemon for the PostgreSQL test container"
    )
    for item in items:
        if item.get_closest_marker("requires_docker"):
            item.add_marker(skip)


def _postgres_container() -> PostgresContainer:
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    return PostgresContainer(
        "postgres:16-alpine",
        username="poliscope_migrator",
        password="isolated-migrator-test-password",
        dbname="poliscope_test",
        driver="asyncpg",
    )


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with _postgres_container() as container:
        yield container


@pytest.fixture
def isolated_postgres_container() -> Iterator[PostgresContainer]:
    with _postgres_container() as container:
        yield container


async def _revoke_public_temporary(admin_url: str) -> None:
    engine = create_async_engine(admin_url)
    try:
        async with engine.begin() as connection:
            database_name = await connection.scalar(text("SELECT current_database()"))
            quoted_database = connection.dialect.identifier_preparer.quote(
                database_name
            )
            await connection.execute(
                text(f"REVOKE TEMPORARY ON DATABASE {quoted_database} FROM PUBLIC")
            )
    finally:
        await engine.dispose()


def _prepare_database(container: PostgresContainer) -> str:
    admin_url = container.get_connection_url(driver="asyncpg")
    asyncio.run(_revoke_public_temporary(admin_url))
    return admin_url


@pytest.fixture(scope="session")
def postgres_admin_url(postgres_container: PostgresContainer) -> str:
    return _prepare_database(postgres_container)


@pytest.fixture
def isolated_postgres_admin_url(
    isolated_postgres_container: PostgresContainer,
) -> str:
    return _prepare_database(isolated_postgres_container)


@pytest.fixture(scope="session")
def migrated_db(postgres_admin_url: str) -> str:
    os.environ["POLISCOPE_APP_DATABASE_PASSWORD"] = APP_PASSWORD
    os.environ["POLISCOPE_PROJECTOR_DATABASE_PASSWORD"] = PROJECTOR_PASSWORD
    command.upgrade(_alembic_config(postgres_admin_url), "head")
    return postgres_admin_url


@pytest_asyncio.fixture
async def admin_engine(migrated_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_db)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _role_session(
    admin_url: str,
    username: str,
    password: str,
) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_role_url(admin_url, username, password))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_session(migrated_db: str) -> AsyncIterator[AsyncSession]:
    async for session in _role_session(
        migrated_db,
        APP_ROLE,
        APP_PASSWORD,
    ):
        yield session


@pytest_asyncio.fixture
async def projector_session(migrated_db: str) -> AsyncIterator[AsyncSession]:
    async for session in _role_session(
        migrated_db,
        PROJECTOR_ROLE,
        PROJECTOR_PASSWORD,
    ):
        yield session


async def _role_session_factory(
    admin_url: str,
    username: str,
    password: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_role_url(admin_url, username, password))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_sessions(
    migrated_db: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A session factory, for code that opens and commits its own transactions.

    The worker cannot borrow a caller's session: it commits the deliberation
    before projecting so the projector reads durable events, which means it needs
    to own the transaction boundary.
    """
    async for factory in _role_session_factory(migrated_db, APP_ROLE, APP_PASSWORD):
        yield factory


@pytest_asyncio.fixture
async def projector_sessions(
    migrated_db: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async for factory in _role_session_factory(
        migrated_db, PROJECTOR_ROLE, PROJECTOR_PASSWORD
    ):
        yield factory


@pytest_asyncio.fixture
async def api_client(migrated_db: str) -> AsyncIterator[Any]:
    """An HTTP client bound to the real ASGI app and the test database.

    The app is exercised through its ASGI interface rather than by calling
    handler functions, so routing, dependency injection, status codes, and
    response headers are all covered. A handler that works when called directly
    and 404s over HTTP is a failure users would hit and unit tests would miss.
    """
    import httpx

    from apps.api.dependencies import AppState
    from apps.api.main import app

    state = AppState(_role_url(migrated_db, APP_ROLE, APP_PASSWORD))
    app.state.poliscope = state
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://poliscope.test",
        ) as client:
            yield client
    finally:
        await state.dispose()


@pytest.fixture
def valid_research_contract() -> Any:
    """Return a deterministic valid ResearchContract instance."""
    return make_research_contract()


@pytest_asyncio.fixture
async def seeded_task(app_session: AsyncSession) -> UUID:
    """Insert one research task and return its task_id.

    Call and event rows carry a foreign key to research_tasks.task_id, so any
    audit assertion needs a real parent task rather than a loose UUID.
    """
    from packages.research.models import ResearchTaskModel

    task_id = uuid4()
    app_session.add(
        ResearchTaskModel(
            id=uuid4(),
            task_id=task_id,
            question="Does social media use cause adolescent depression?",
            status="AWAITING_CLAIM_CONFIRMATION",
            created_by="test_harness",
            wall_clock_minutes=60,
            model_cost_usd=Decimal("10.0000"),
            tool_call_limit=100,
            source_limit=50,
            user_evidence={},
        )
    )
    await app_session.flush()
    return task_id
