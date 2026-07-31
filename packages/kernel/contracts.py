from collections.abc import Container, Iterator, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
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

from pydantic import (
    BaseModel,
    ConfigDict,
    FieldSerializationInfo,
    GetCoreSchemaHandler,
    SerializerFunctionWrapHandler,
    field_serializer,
    model_validator,
)
from pydantic_core import CoreSchema, PydanticUndefined, core_schema

K = TypeVar("K")
V = TypeVar("V")


_SAFE_CONTAINER_ORIGINS = frozenset({tuple, frozenset})
_SAFE_SCALAR_CONTAINERS = frozenset({str, bytes})
_IMMUTABLE_LEAF_TYPES = frozenset(
    {
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
    }
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


_ENUM_INTERNAL_ATTRIBUTES = frozenset(
    {"_name_", "_value_", "__objclass__", "_sort_order_"}
)
_JSON_KEY_TYPES = frozenset({str})


def _is_safe_enum_member(value: Enum) -> bool:
    enum_type = type(value)
    if not issubclass(enum_type, (StrEnum, IntEnum)):
        return False
    if "__hash__" in enum_type.__dict__:
        return False
    if type(value.value) not in {str, int}:
        return False
    return all(
        name in _ENUM_INTERNAL_ATTRIBUTES
        for name in getattr(value, "__dict__", {})
    )


def _is_json_key_annotation(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is Annotated:
        arguments = get_args(annotation)
        return bool(arguments) and _is_json_key_annotation(arguments[0])
    if origin is Literal:
        return all(type(value) in _JSON_KEY_TYPES for value in get_args(annotation))
    if origin in _SAFE_CONTAINER_ORIGINS:
        return False
    if origin is not None:
        return False
    return annotation is str


def _is_data_enum_type(annotation: Any) -> bool:
    if not isinstance(annotation, type):
        return False
    if not issubclass(annotation, (StrEnum, IntEnum)):
        return False
    public_names = {
        name
        for name in annotation.__dict__
        if not name.startswith("_") and name not in annotation.__members__
    }
    return not public_names and all(
        _is_safe_enum_member(member) for member in annotation
    )


def _enum_annotation_is_unsafe(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin is Annotated:
        arguments = get_args(annotation)
        return bool(arguments) and _enum_annotation_is_unsafe(arguments[0])
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return not _is_data_enum_type(annotation)
    return any(
        _enum_annotation_is_unsafe(argument) for argument in get_args(annotation)
    )


def _default_is_already_immutable(value: Any) -> bool:
    if isinstance(value, ContractModel) or type(value) in _IMMUTABLE_LEAF_TYPES:
        return True
    if isinstance(value, Enum):
        return _is_safe_enum_member(value)
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


def thaw_for_serialization(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            thaw_for_serialization(key): thaw_for_serialization(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [thaw_for_serialization(item) for item in value]
    if isinstance(value, frozenset):
        thawed = [thaw_for_serialization(item) for item in value]
        return sorted(thawed, key=repr)
    return value


def freeze_value(value: Any) -> Any:
    return _freeze_value(value, set())


def _freeze_value(value: Any, active_ids: set[int]) -> Any:
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, ContractModel):
        return value
    if isinstance(value, Enum):
        if not _is_safe_enum_member(value):
            raise TypeError("Enum member is not safely immutable")
        return value.value
    if type(value) in _IMMUTABLE_LEAF_TYPES:
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
        invalid_key = next((key for key in source if type(key) is not str), None)
        if invalid_key is not None:
            raise TypeError("FrozenDict keys must be exact str values")
        frozen_values = {
            key: cast(V, freeze_value(value)) for key, value in source.items()
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
            if not _is_json_key_annotation(arguments[0]):
                raise TypeError(
                    "FrozenDict key type must be exactly str"
                )
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
                lambda value, serializer: serializer(thaw_for_serialization(value))
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
            if _enum_annotation_is_unsafe(field.annotation):
                raise TypeError(
                    f"field '{field_name}' must use a data-only StrEnum or IntEnum"
                )
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

    @field_serializer("*", mode="wrap")
    def serialize_field(
        self,
        value: Any,
        handler: SerializerFunctionWrapHandler,
        info: FieldSerializationInfo,
    ) -> Any:
        if info.mode == "json":
            if isinstance(value, FrozenDict | frozenset):
                return thaw_for_serialization(value)
            serialized = handler(value)
            return thaw_for_serialization(serialized)
        if isinstance(value, FrozenDict):
            return value
        return handler(value)

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
