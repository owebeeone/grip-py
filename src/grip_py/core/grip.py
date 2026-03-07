"""Grip key and registry model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar, overload

from .errors import DuplicateGrip

T = TypeVar("T")
_MISSING = object()


@dataclass(eq=False, frozen=True, slots=True)
class Grip(Generic[T]):
    """Typed grip identifier."""

    name: str
    key: str
    default: T | None
    data_type: type[T] | None


class GripRegistry:
    """Registry for grip keys only."""

    def __init__(self):
        self._keys_by_name: dict[str, Grip[Any]] = {}

    @overload
    def add(self, name: str, default: T) -> Grip[T]:
        ...

    @overload
    def add(self, name: str, *, value_type: type[T]) -> Grip[T | None]:
        ...

    @overload
    def add(
        self,
        name: str,
        default: None,
        *,
        value_type: type[T],
        nullable: Literal[True] = True,
    ) -> Grip[T | None]:
        ...

    @overload
    def add(
        self,
        name: str,
        default: Any,
        *,
        value_type: type[T],
        nullable: Literal[False] = False,
    ) -> Grip[T]:
        ...

    @overload
    def add(
        self,
        name: str,
        default: Any,
        *,
        value_type: type[T],
        nullable: Literal[True],
    ) -> Grip[T | None]:
        ...

    def add(
        self,
        name: str,
        default: Any = _MISSING,
        *,
        value_type: type[Any] | None = None,
        nullable: bool = False,
    ) -> Grip[Any]:
        """Define a grip and register it by name.

        Rules:
        - Name must be unique.
        - `add(name, default)` infers type from default.
        - `add(name, *, value_type=T)` creates nullable grip with default None.
        - `add(name, default, value_type=T)` converts default using `T(default)`.
        """
        if name in self._keys_by_name:
            raise DuplicateGrip(f"Grip '{name}' is already registered")

        has_default = default is not _MISSING

        if not has_default:
            if value_type is None:
                raise TypeError("add(name) is invalid; provide default or value_type")
            resolved_default = None
            resolved_type = value_type
            resolved_nullable = True
        else:
            if default is None and value_type is None:
                raise TypeError("add(name, None) requires value_type")

            if value_type is None:
                resolved_default = default
                resolved_type = type(default)
                resolved_nullable = default is None
            else:
                resolved_type = value_type
                resolved_nullable = nullable
                if default is None:
                    if not resolved_nullable:
                        raise TypeError(
                            "default=None requires nullable=True when value_type is provided"
                        )
                    resolved_default = None
                else:
                    resolved_default = value_type(default)

        grip = Grip(
            name=name,
            key=name,
            default=resolved_default,
            data_type=resolved_type,
        )
        self._keys_by_name[name] = grip
        return grip

