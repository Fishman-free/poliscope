from typing import Any

import pytest
from pydantic import ValidationError

from packages.kernel.contracts import ContractModel, FrozenDict, freeze_value
from tests.helpers import assert_recursively_frozen


class ExampleContract(ContractModel):
    labels: tuple[str, ...]
    metadata: FrozenDict[str, Any]


def test_freeze_value_recursively_freezes_builtin_containers() -> None:
    frozen = freeze_value(
        {"sequence": ["a", {"nested": ["b"]}], "members": {"x", "y"}}
    )

    assert isinstance(frozen, FrozenDict)
    assert frozen["sequence"] == ("a", FrozenDict({"nested": ("b",)}))
    assert frozen["members"] == frozenset({"x", "y"})
    assert_recursively_frozen(frozen)


def test_frozen_dict_is_hashable_independent_of_mapping_order() -> None:
    first = FrozenDict({"a": [1, 2], "b": {"nested": "value"}})
    second = FrozenDict({"b": {"nested": "value"}, "a": [1, 2]})

    assert first == second
    assert hash(first) == hash(second)


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
