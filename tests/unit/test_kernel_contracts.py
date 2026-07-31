from collections import Counter, OrderedDict, defaultdict, deque
from collections.abc import (
    Iterable,
    Mapping,
    MutableMapping,
    MutableSequence,
    MutableSet,
)
from copy import copy, deepcopy
from datetime import date
from decimal import Decimal
from enum import StrEnum
from io import BytesIO, StringIO
from typing import Annotated, Any, Literal
from uuid import UUID

import pytest
from pydantic import Field, TypeAdapter, ValidationError

from packages.kernel.contracts import ContractModel, FrozenDict, freeze_value
from tests.helpers import assert_recursively_frozen


class ExampleContract(ContractModel):
    labels: tuple[str, ...]
    metadata: FrozenDict[str, Any]


def test_contract_model_rejects_mutable_container_annotations() -> None:
    for field_name, annotation in (
        ("items", list[str]),
        ("members", set[str]),
        ("metadata", dict[str, str]),
        ("mapping", Mapping[str, str]),
        ("mutable_mapping", MutableMapping[str, str]),
        ("mutable_sequence", MutableSequence[str]),
        ("mutable_set", MutableSet[str]),
        ("deque", deque[str]),
        ("ordered", OrderedDict[str, str]),
        ("defaultdict", defaultdict[str, str]),
        ("counter", Counter[str]),
        ("iterable", Iterable[str]),
    ):
        with pytest.raises(TypeError, match=field_name):

            class InvalidContract(ContractModel):
                __annotations__ = {field_name: annotation}


def test_contract_model_accepts_safe_typing_wrappers() -> None:
    class WrappedContract(ContractModel):
        optional_items: tuple[str, ...] | None
        annotated_items: Annotated[tuple[str, ...], "immutable"]
        literal_value: Literal["fixed"]

    contract = WrappedContract(
        optional_items=("value",),
        annotated_items=("value",),
        literal_value="fixed",
    )
    assert contract.optional_items == ("value",)


def test_contract_model_validates_defaults_and_rejects_mutable_any_default() -> None:
    with pytest.raises(TypeError, match="payload"):

        class InvalidDefaultContract(ContractModel):
            payload: Any = []


def test_contract_model_rejects_default_factories() -> None:
    with pytest.raises(TypeError, match="payload"):

        class InvalidFactoryContract(ContractModel):
            payload: Any = Field(default_factory=list)


def test_contract_model_rejects_nested_mutable_defaults() -> None:
    for default in (([1],), ({"key": "value"},)):
        with pytest.raises(TypeError, match="payload"):

            class InvalidNestedDefaultContract(ContractModel):
                payload: tuple[Any, ...] = default


def test_contract_model_accepts_explicit_immutable_defaults() -> None:
    class ValidDefaultContract(ContractModel):
        payload: tuple[str, ...] = ("value",)

    assert ValidDefaultContract().payload == ("value",)


def test_contract_model_rejects_defaults_that_require_freezing() -> None:
    with pytest.raises(TypeError, match="payload"):

        class BufferDefaultContract(ContractModel):
            payload: Any = bytearray(b"value")


def test_contract_model_rejects_unknown_stateful_defaults() -> None:
    class MutableLeaf:
        pass

    with pytest.raises(TypeError, match="payload"):

        class StatefulDefaultContract(ContractModel):
            payload: Any = MutableLeaf()


def test_frozen_dict_rejects_mutable_generic_value_annotations() -> None:
    with pytest.raises(TypeError, match="FrozenDict"):
        TypeAdapter(FrozenDict[str, list[int]])
    with pytest.raises(TypeError, match="FrozenDict"):
        TypeAdapter(FrozenDict[str, tuple[dict[str, int], ...]])


def test_freeze_value_rejects_cycles_but_allows_shared_values() -> None:
    cyclic: list[Any] = []
    cyclic.append(cyclic)

    with pytest.raises(ValueError, match="cyclic"):
        freeze_value(cyclic)

    shared = ["value"]
    assert freeze_value([shared, shared]) == (("value",), ("value",))


def test_contract_cycle_is_reported_as_validation_error() -> None:
    cyclic: list[Any] = []
    cyclic.append(cyclic)

    with pytest.raises(ValidationError, match="cyclic"):
        ExampleContract.model_validate({"labels": [], "metadata": {"cycle": cyclic}})


def test_freeze_value_recursively_freezes_builtin_containers() -> None:
    frozen = freeze_value(
        {"sequence": ["a", {"nested": ["b"]}], "members": {"x", "y"}}
    )

    assert isinstance(frozen, FrozenDict)
    assert frozen["sequence"] == ("a", FrozenDict({"nested": ("b",)}))
    assert frozen["members"] == frozenset({"x", "y"})
    assert_recursively_frozen(frozen)


def test_byte_buffers_freeze_to_hashable_bytes() -> None:
    source = bytearray(b"value")
    frozen = FrozenDict({"payload": source, "view": memoryview(source)})

    assert frozen["payload"] == b"value"
    assert frozen["view"] == b"value"
    assert hash(frozen)
    assert deepcopy(frozen) is frozen

    contract = ExampleContract(labels=(), metadata={"payload": source})
    assert contract.metadata["payload"] == b"value"
    source[0] = ord("X")
    assert contract.metadata["payload"] == b"value"


def test_unknown_and_stateful_leaves_are_rejected() -> None:
    class MutableHashableLeaf:
        def __init__(self) -> None:
            self.state = 1

        def __hash__(self) -> int:
            return self.state

    for value in (MutableHashableLeaf(), StringIO("value"), BytesIO(b"value")):
        with pytest.raises(TypeError, match="immutable"):
            FrozenDict({"payload": value})
        with pytest.raises(ValidationError, match="immutable"):
            ExampleContract.model_validate(
                {"labels": [], "metadata": {"payload": value}}
            )


def test_known_immutable_leaves_and_nested_contract_are_stable() -> None:
    class Mode(StrEnum):
        FIXED = "FIXED"

    nested = ExampleContract(labels=("nested",), metadata=FrozenDict())
    frozen = FrozenDict(
        {
            "decimal": Decimal("1.25"),
            "date": date(2025, 1, 1),
            "uuid": UUID("00000000-0000-0000-0000-000000000001"),
            "enum": Mode.FIXED,
            "contract": nested,
        }
    )
    contract = ExampleContract(labels=(), metadata=frozen)

    assert hash(frozen)
    assert contract.model_dump_json() == contract.model_dump_json()
    assert contract.metadata["contract"] is nested


def test_frozen_dict_is_hashable_independent_of_mapping_order() -> None:
    first = FrozenDict({"a": [1, 2], "b": {"nested": "value"}})
    second = FrozenDict({"b": {"nested": "value"}, "a": [1, 2]})

    assert first == second
    assert hash(first) == hash(second)


def test_frozen_dict_copy_operations_return_same_immutable_value() -> None:
    frozen = FrozenDict({"nested": ["value"]})

    assert copy(frozen) is frozen
    assert deepcopy(frozen) is frozen

    contract = ExampleContract(labels=("one",), metadata=frozen)
    copied_contract = contract.model_copy(deep=True)
    assert copied_contract.metadata is contract.metadata


def test_frozen_dict_preserves_falsy_non_empty_mapping() -> None:
    class FalsyMapping(dict[str, str]):
        def __bool__(self) -> bool:
            return False

    assert FrozenDict(FalsyMapping(key="value"))["key"] == "value"


def test_frozen_dict_internal_storage_cannot_be_mutated() -> None:
    frozen = FrozenDict({"key": "value"})
    initial_hash = hash(frozen)

    with pytest.raises((AttributeError, TypeError)):
        frozen._values["key"] = "changed"  # type: ignore[index]
    with pytest.raises((AttributeError, TypeError)):
        frozen._values = {"key": "replaced"}

    assert frozen["key"] == "value"
    assert hash(frozen) == initial_hash


def test_nested_mappings_cannot_add_change_or_delete_keys() -> None:
    frozen = FrozenDict({"outer": {"inner": "value"}})
    inner = frozen["outer"]

    with pytest.raises(TypeError):
        inner["added"] = "value"  # type: ignore[unused-ignore]
    with pytest.raises(TypeError):
        inner["inner"] = "changed"  # type: ignore[unused-ignore]
    with pytest.raises(TypeError):
        del inner["inner"]  # type: ignore[unused-ignore]

    assert inner == FrozenDict({"inner": "value"})


def test_contract_model_converts_plain_containers_to_immutable_values() -> None:
    contract = ExampleContract.model_validate(
        {"labels": ["one", "two"], "metadata": {"nested": ["value"]}}
    )

    assert contract.labels == ("one", "two")
    assert isinstance(contract.metadata, FrozenDict)
    assert contract.metadata["nested"] == ("value",)


def test_contract_model_attributes_cannot_be_reassigned() -> None:
    contract = ExampleContract(labels=("one",), metadata=FrozenDict())

    with pytest.raises(ValidationError):
        contract.labels = ("two",)  # type: ignore[misc]


def test_nested_contract_containers_are_immutable(valid_research_contract: Any) -> None:
    with pytest.raises(TypeError):
        valid_research_contract.scope.languages[0] = "fr"  # type: ignore[unused-ignore]
    frozen = FrozenDict({"outer": {"inner": ["a"]}})
    with pytest.raises(TypeError):
        frozen["outer"]["inner"][0] = "b"  # type: ignore[unused-ignore]


def test_contract_model_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ExampleContract.model_validate(
            {"labels": [], "metadata": {}, "unexpected": True}
        )


def test_contract_model_copy_cannot_bypass_immutability() -> None:
    contract = ExampleContract(labels=("one",), metadata=FrozenDict())

    with pytest.raises(TypeError, match="immutable contracts do not support updates"):
        contract.model_copy(update={"labels": ["mutable"]})


def test_contract_model_serializes_frozen_dict_as_mapping() -> None:
    contract = ExampleContract(
        labels=("one",), metadata=FrozenDict({"nested": ("value",)})
    )

    assert contract.model_dump(mode="json") == {
        "labels": ["one"],
        "metadata": {"nested": ["value"]},
    }
