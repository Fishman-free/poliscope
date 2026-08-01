"""Assert that the migrations and the ORM describe the same database.

Drift between the two is silent and destructive. A table that exists in the
database but not in ``Base.metadata`` makes the next autogenerate emit
``drop_table``; a column that exists in the ORM but not in a migration makes
every insert fail at runtime with UndefinedColumnError. Both had happened in
this repository before this test existed, which is why the check runs against a
real PostgreSQL rather than comparing source files.
"""

from __future__ import annotations

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

# Every ORM module must be imported for Base.metadata to be complete. This
# mirrors migrations/env.py; if the two lists diverge, this test fails, which is
# the point.
from packages.council import models as council_models  # noqa: F401
from packages.evidence import models as evidence_models  # noqa: F401
from packages.kernel.database import Base
from packages.models import models as model_models  # noqa: F401
from packages.papers import models as paper_models  # noqa: F401
from packages.research import models as research_models  # noqa: F401
from packages.tools import models as tool_models  # noqa: F401


def _diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "target_metadata": Base.metadata},
    )
    return list(compare_metadata(context, Base.metadata))


async def test_migrations_at_head_match_the_orm_metadata(
    admin_engine: AsyncEngine,
) -> None:
    """Upgrading to head must leave nothing for autogenerate to do."""
    async with admin_engine.connect() as connection:
        differences = await connection.run_sync(_diff)
    assert differences == [], (
        "migrations and ORM disagree; autogenerate would emit: "
        f"{differences}"
    )
