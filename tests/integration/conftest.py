"""Everything in this directory needs a real PostgreSQL container.

Marking the directory rather than each module means a new integration test
cannot be added without the marker -- and a test that does not need a database
does not belong here. Ten modules that exercised pure logic have been moved to
``tests/unit`` for the same reason: a directory named "integration" whose
contents integrate nothing tells you the boundaries work when they were never
crossed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INTEGRATION_ROOT = Path(__file__).resolve().parent


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    for item in items:
        if Path(str(item.path)).resolve().is_relative_to(INTEGRATION_ROOT):
            item.add_marker(pytest.mark.requires_docker)


@pytest.fixture(autouse=True)
def _stub_model_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep confirm-claims hermetic: the pre-flight probe is a real network
    call by design, so integration tests must not depend on a vendor being
    reachable. The probe's own mapping is unit-tested (test_endpoint_config),
    and the blocking/transient branches are integration-tested with an
    explicit override (test_task_api)."""

    async def _ok(
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        client: object | None = None,
    ) -> object:
        from packages.models.endpoint_config import ProbeResult

        return ProbeResult(True, "连接成功（测试）", 1)

    monkeypatch.setattr("apps.api.routers.tasks.probe_endpoint", _ok)
