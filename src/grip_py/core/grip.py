"""Grip key and registry model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar, overload

from .errors import DuplicateGrip

T = TypeVar("T")
_MISSING = object()


@dataclass(eq=False, frozen=True, slots=True)
class Grip(Generic[T]):
    """Typed grip identifier used as a stable key in the graph.

    A ``Grip[T]`` carries:
    - ``name``: user-facing symbolic name.
    - ``key``: canonical identity used for lookups.
    - ``default``: default value propagated when no producer is available.
    - ``data_type``: runtime type metadata for conversions and tooling.
    """

    name: str
    key: str
    default: T | None
    data_type: type[T] | None


@dataclass(slots=True, eq=False)
class GripRegistry:
    """Registry for defining and owning ``Grip`` instances.

    The first version of grip-py only registers grip keys (not taps).

    Example:
        >>> registry = GripRegistry()
        >>> user_name = registry.add("UserName", "")
        >>> user_age = registry.add("UserAge", 0)
        >>> temp_c = registry.add("TempCelsius", value_type=float)
        >>> # Inferred as Grip[str], Grip[int], Grip[float | None]
    """

    _keys_by_name: dict[str, Grip[Any]] = field(default_factory=dict, init=False)

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
        """Register a grip and return its typed key object.

        Supported call forms:
        - ``add(name, default) -> Grip[T]``
          Infers ``T`` from ``default``.
        - ``add(name, *, value_type=T) -> Grip[T | None]``
          Creates a nullable grip with ``default=None``.
        - ``add(name, default, value_type=T, nullable=False) -> Grip[T]``
          Converts the default with ``T(default)``.
        - ``add(name, None, value_type=T, nullable=True) -> Grip[T | None]``
          Explicit nullable typed grip.

        Type-checkers/IDEs infer return types from overloads, e.g.:
        - ``registry.add("UserName", "")`` infers ``Grip[str]``
        - ``registry.add("TempCelsius", value_type=float)`` infers ``Grip[float | None]``

        Args:
            name: Unique grip name in this registry.
            default: Optional default value.
            value_type: Optional explicit target type for conversion/inference.
            nullable: Whether ``None`` is allowed when ``value_type`` is provided.

        Raises:
            DuplicateGrip: If ``name`` already exists.
            TypeError: If the call shape is invalid.
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
