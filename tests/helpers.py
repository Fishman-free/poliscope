from collections.abc import Mapping
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from packages.kernel.contracts import FrozenDict


async def table_names(engine: AsyncEngine) -> set[str]:
    """Return the current PostgreSQL table names."""
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        return set(names)


async def table_column_details(
    engine: AsyncEngine,
    table_name: str,
) -> dict[str, dict[str, Any]]:
    """Return PostgreSQL column metadata keyed by column name."""
    async with engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync: inspect(sync).get_columns(table_name)
        )
    return {str(column["name"]): dict(column) for column in columns}


async def table_columns(engine: AsyncEngine, table_name: str) -> set[str]:
    """Return the column names for a PostgreSQL table."""
    return set(await table_column_details(engine, table_name))


def assert_recursively_frozen(value: Any) -> None:
    """Assert that nested public containers use immutable representations."""
    if isinstance(value, Mapping):
        assert isinstance(value, FrozenDict)
        for item in value.values():
            assert_recursively_frozen(item)
        return
    if isinstance(value, tuple | frozenset):
        for item in value:
            assert_recursively_frozen(item)
        return
    assert not isinstance(value, list | set | dict)
