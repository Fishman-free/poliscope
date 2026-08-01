from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

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


@pytest.fixture
def valid_research_contract() -> Any:
    """Return a deterministic valid ResearchContract instance."""
    return make_research_contract()
