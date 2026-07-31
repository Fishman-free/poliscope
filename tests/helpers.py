from collections.abc import Mapping
from typing import Any

from packages.kernel.contracts import FrozenDict


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
