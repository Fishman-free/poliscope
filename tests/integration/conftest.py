"""Everything in this directory needs a real PostgreSQL container.

Marking the directory rather than each module means a new integration test
cannot be added without the marker -- and a test that does not need a database
does not belong here. Ten modules that exercised pure logic have been moved to
``tests/unit`` for the same reason: a directory named "integration" whose
contents integrate nothing tells you the boundaries work when they were never
crossed.
"""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    for item in items:
        item.add_marker(pytest.mark.requires_docker)
