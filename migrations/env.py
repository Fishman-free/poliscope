from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Every ORM module must be imported so that Base.metadata is complete before
# autogenerate runs. A missing import makes Alembic emit drop_table for tables
# that already exist in the database.
from packages.council import models as council_models  # noqa: F401
from packages.evidence import models as evidence_models  # noqa: F401
from packages.kernel.config import DatabaseConfig
from packages.kernel.database import Base
from packages.knowledge import models as knowledge_models  # noqa: F401
from packages.models import models as model_models  # noqa: F401
from packages.papers import models as paper_models  # noqa: F401
from packages.research import models as research_models  # noqa: F401
from packages.tools import models as tool_models  # noqa: F401

config = context.config

migrator_url = DatabaseConfig.migrator_url_from_env()
config.set_main_option(
    "sqlalchemy.url",
    migrator_url.replace("%", "%%"),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
