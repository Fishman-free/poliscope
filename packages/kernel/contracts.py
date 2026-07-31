from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any, Generic, Self, TypeVar, cast, get_args, get_origin

from pydantic import BaseModel, ConfigDict, GetCoreSchemaHandler, model_validator
from pydantic_core import CoreSchema, PydanticUndefined, core_schema

K = TypeVar("K")
V = TypeVar("V")


_MUTABLE_ORIGINS = frozenset({list, set, dict})
_MUTABLE_VALUES = (list, set, dict)


def _contains_mutable_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if annotation in _MUTABLE_ORIGINS or origin in _MUTABLE_ORIGINS:
        return True
    return any(
        _contains_mutable_annotation(argument) for argument in get_args(annotation)
    )


def freeze_value(value: Any) -> Any:
    return _freeze_value(value, set())


def _freeze_value(value: Any, active_ids: set[int]) -> Any:
    if not isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return value

    identity = id(value)
    if identity in active_ids:
        raise ValueError("cyclic container reference is not allowed")
    active_ids.add(identity)
    try:
        if isinstance(value, Mapping):
            return FrozenDict(
                {key: _freeze_value(item, active_ids) for key, item in value.items()}
            )
        if isinstance(value, (list, tuple)):
            return tuple(_freeze_value(item, active_ids) for item in value)
        return frozenset(_freeze_value(item, active_ids) for item in value)
    finally:
        active_ids.remove(identity)


class FrozenDict(Mapping[K, V], Generic[K, V]):  # noqa: UP046
    __slots__ = ("_values",)

    _values: Mapping[K, V]

    def __init__(self, values: Mapping[K, V] | None = None) -> None:
        source = {} if values is None else values
        frozen_values = {
            key: cast(V, freeze_value(value)) for key, value in source.items()
        }
        object.__setattr__(self, "_values", MappingProxyType(frozen_values))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("FrozenDict attributes cannot be reassigned")

    def __getitem__(self, key: K) -> V:
        return self._values[key]

    def __iter__(self) -> Iterator[K]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __hash__(self) -> int:
        return hash(frozenset(self._values.items()))

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        memo[id(self)] = self
        return self

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        arguments = get_args(source_type)
        if len(arguments) == 2:
            has_mutable_key = _contains_mutable_annotation(arguments[0])
            has_mutable_value = _contains_mutable_annotation(arguments[1])
            if has_mutable_key or has_mutable_value:
                raise TypeError(
                    "FrozenDict generic arguments must describe immutable values"
                )
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
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
        validate_default=True,
    )

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        for field_name, field in cls.model_fields.items():
            if _contains_mutable_annotation(field.annotation):
                raise TypeError(
                    f"field '{field_name}' must use immutable container annotations"
                )
            default = field.default
            has_mutable_default = default is not PydanticUndefined and isinstance(
                default, _MUTABLE_VALUES
            )
            if has_mutable_default:
                raise TypeError(f"field '{field_name}' has a mutable default value")

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
