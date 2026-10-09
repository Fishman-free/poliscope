"""Collecting integration tests must not skip unrelated unit/eval tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.integration.conftest import pytest_collection_modifyitems


class Item:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.markers: list[Any] = []

    def add_marker(self, marker: Any) -> None:
        self.markers.append(marker)


def test_docker_marker_is_scoped_to_integration_directory() -> None:
    root = Path(__file__).resolve().parents[1]
    unit = Item(root / "unit" / "test_dummy.py")
    integration = Item(root / "integration" / "test_dummy.py")

    pytest_collection_modifyitems(SimpleNamespace(), [unit, integration])  # type: ignore[arg-type, list-item]

    assert unit.markers == []
    assert len(integration.markers) == 1
    assert integration.markers[0].name == "requires_docker"
