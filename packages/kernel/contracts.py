from collections.abc import Iterator, Mapping
from typing import Any, Generic, Self, TypeVar, cast, get_args

from pydantic import BaseModel, ConfigDict, GetCoreSchemaHandler, model_validator
from pydantic_core import CoreSchema, core_schema

K = TypeVar("K")
V = TypeVar("V")


def freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict({key: freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_value(item) for item in value)
    return value


class FrozenDict(Mapping[K, V], Generic[K, V]):  # noqa: UP046
    def __init__(self, values: Mapping[K, V] | None = None) -> None:
        self._values: dict[K, V] = {
            key: cast(V, freeze_value(value))
            for key, value in (values or {}).items()
        }

    def __getitem__(self, key: K) -> V:
        return self._values[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __hash__(self) -> int:
        return hash(frozenset(self._values.items()))

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        arguments = get_args(source_type)
        if len(arguments) == 2:
            key_schema = handler.generate_schema(arguments[0])
            value_schema = handler.generate_schema(arguments[1])
        else:
            key_schema = core_schema.any_schema()
            value_schema = core_schema.any_schema()
        mapping_schema = core_schema.dict_schema(key_schema, value_schema)

        return core_schema.no_info_after_validator_function(
            cls,
            mapping_schema,
            serialization=core_schema.wrap_serializer_function_ser_schema(
                lambda value, serializer: serializer(dict(value))
            ),
        )


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, arbitrary_types_allowed=True
    )

    @model_validator(mode="before")
    @classmethod
    def freeze_containers(cls, value: Any) -> Any:
        return freeze_value(value)

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        if update is not None:
            raise TypeError("immutable contracts do not support updates")
        return super().model_copy(deep=deep)
