from collections.abc import Container, Iterator, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import (
    Annotated,
    Any,
    Generic,
    Literal,
    Self,
    TypeVar,
    cast,
    get_args,
    get_origin,
)
from uuid import UUID

from pydantic import BaseModel, ConfigDict, GetCoreSchemaHandler, model_validator
from pydantic_core import CoreSchema, PydanticUndefined, core_schema

K = TypeVar("K")
V = TypeVar("V")


_SAFE_CONTAINER_ORIGINS = frozenset({tuple, frozenset})
_SAFE_SCALAR_CONTAINERS = frozenset({str, bytes})
_IMMUTABLE_LEAF_TYPES = (
    type(None),
    str,
    bytes,
    bool,
    int,
    float,
    Decimal,
    datetime,
    date,
    time,
    UUID,
    Enum,
)


def _is_disallowed_container_type(annotation: Any) -> bool:
    if annotation is Any or annotation in _SAFE_SCALAR_CONTAINERS:
        return False
    if isinstance(annotation, type) and issubclass(annotation, (str, bytes)):
        return False
    module = getattr(annotation, "__module__", "")
    if module in {"collections", "collections.abc"}:
        return True
    try:
        return isinstance(annotation, type) and issubclass(annotation, Container)
    except TypeError:
        return False


def _contains_disallowed_container_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    candidate = origin or annotation
    if candidate is FrozenDict:
        return any(
            _contains_disallowed_container_annotation(argument)
            for argument in get_args(annotation)
        )
    if candidate in _SAFE_CONTAINER_ORIGINS:
        return any(
            _contains_disallowed_container_annotation(argument)
            for argument in get_args(annotation)
        )
    if origin is Annotated:
        arguments = get_args(annotation)
        return bool(arguments) and _contains_disallowed_container_annotation(
            arguments[0]
        )
    if origin is Literal:
        return False
    if _is_disallowed_container_type(candidate):
        return True
    return any(
        _contains_disallowed_container_annotation(argument)
        for argument in get_args(annotation)
    )


def _default_is_already_immutable(value: Any) -> bool:
    if isinstance(value, (ContractModel, _IMMUTABLE_LEAF_TYPES)):
        return True
    if isinstance(value, (bytearray, memoryview, list, set, dict)):
        return False
    if isinstance(value, tuple | frozenset):
        return all(_default_is_already_immutable(item) for item in value)
    if isinstance(value, FrozenDict):
        return all(
            _default_is_already_immutable(key)
            and _default_is_already_immutable(item)
            for key, item in value.items()
        )
    return False


def freeze_value(value: Any) -> Any:
    return _freeze_value(value, set())


def _freeze_value(value: Any, active_ids: set[int]) -> Any:
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, ContractModel):
        return value
    if isinstance(value, _IMMUTABLE_LEAF_TYPES):
        return value
    if not isinstance(value, (Mapping, list, tuple, set, frozenset)):
        raise TypeError(
            f"unsupported mutable or unknown leaf type: {type(value).__name__}; "
            "an immutable value is required"
        )

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
            cast(K, freeze_value(key)): cast(V, freeze_value(value))
            for key, value in source.items()
        }
        try:
            hash(frozenset(frozen_values.items()))
        except TypeError as exc:
            raise TypeError("FrozenDict keys and values must be hashable") from exc
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
            has_mutable_key = _contains_disallowed_container_annotation(arguments[0])
            has_mutable_value = _contains_disallowed_container_annotation(arguments[1])
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
            if _contains_disallowed_container_annotation(field.annotation):
                raise TypeError(
                    f"field '{field_name}' must use immutable container annotations"
                )
            if field.default_factory is not None:
                raise TypeError(f"field '{field_name}' must not use default_factory")
            default = field.default
            if default is PydanticUndefined:
                continue
            try:
                freeze_value(default)
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    f"field '{field_name}' has an unsupported mutable default value"
                ) from exc
            if not _default_is_already_immutable(default):
                raise TypeError(
                    f"field '{field_name}' default must already be explicitly immutable"
                )

    @model_validator(mode="before")
    @classmethod
    def freeze_containers(cls, value: Any) -> Any:
        try:
            return freeze_value(value)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        if update is not None:
            raise TypeError("immutable contracts do not support updates")
        return super().model_copy(deep=deep)
