"""Grip key and registry model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar, overload

from .errors import DuplicateGrip

T = TypeVar("T")
_MISSING = object()
_DEFAULT_SCOPE = "app"


@dataclass(eq=False, frozen=True, slots=True)
class Grip(Generic[T]):
    """Typed grip identifier used as a stable key in the graph.

    A ``Grip[T]`` carries:
    - ``scope``: namespace for grip identity.
    - ``name``: user-facing symbolic name.
    - ``key``: canonical identity ``<scope>:<name>`` used for lookups.
    - ``default``: default value propagated when no producer is available.
    - ``data_type``: runtime type metadata for conversions and tooling.
    """

    scope: str
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

    _keys_by_key: dict[str, Grip[Any]] = field(default_factory=dict, init=False)

    @staticmethod
    def canonical_key(scope: str, name: str) -> str:
        """Return the canonical grip key for ``scope`` + ``name``."""
        return f"{scope}:{name}"

    @overload
    def add(self, name: str, default: T, *, scope: str = _DEFAULT_SCOPE) -> Grip[T]:
        ...

    @overload
    def add(
        self,
        name: str,
        *,
        scope: str = _DEFAULT_SCOPE,
        value_type: type[T],
    ) -> Grip[T | None]:
        ...

    @overload
    def add(
        self,
        name: str,
        default: None,
        *,
        scope: str = _DEFAULT_SCOPE,
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
        scope: str = _DEFAULT_SCOPE,
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
        scope: str = _DEFAULT_SCOPE,
        value_type: type[T],
        nullable: Literal[True],
    ) -> Grip[T | None]:
        ...

    def add(
        self,
        name: str,
        default: Any = _MISSING,
        *,
        scope: str = _DEFAULT_SCOPE,
        value_type: type[Any] | None = None,
        nullable: bool = False,
    ) -> Grip[Any]:
        """Register a grip and return its typed key object.

        Supported call forms:
        - ``add(name, default, scope="app") -> Grip[T]``
          Infers ``T`` from ``default``.
        - ``add(name, scope="app", value_type=T) -> Grip[T | None]``
          Creates a nullable grip with ``default=None``.
        - ``add(name, default, scope="app", value_type=T, nullable=False) -> Grip[T]``
          Converts the default with ``T(default)``.
        - ``add(name, None, scope="app", value_type=T, nullable=True) -> Grip[T | None]``
          Explicit nullable typed grip.

        Type-checkers/IDEs infer return types from overloads, e.g.:
        - ``registry.add("UserName", "")`` infers ``Grip[str]``
        - ``registry.add("Theme", "light", scope="session")`` infers ``Grip[str]``
        - ``registry.add("TempCelsius", value_type=float)`` infers ``Grip[float | None]``

        Canonical identity is ``<scope>:<name>``.

        Args:
            name: Grip name within ``scope``.
            default: Optional default value.
            scope: Grip identity namespace. Defaults to ``"app"``.
            value_type: Optional explicit target type for conversion/inference.
            nullable: Whether ``None`` is allowed when ``value_type`` is provided.

        Raises:
            DuplicateGrip: If ``scope:name`` already exists.
            TypeError: If the call shape is invalid.
        """
        key = self.canonical_key(scope, name)
        if key in self._keys_by_key:
            raise DuplicateGrip(f"Grip '{key}' is already registered")

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
            scope=scope,
            name=name,
            key=key,
            default=resolved_default,
            data_type=resolved_type,
        )
        self._keys_by_key[key] = grip
        return grip

    def get(self, scope: str, name: str) -> Grip[Any] | None:
        """Return a grip by ``scope`` and ``name``, or ``None`` when missing."""
        return self.get_by_key(self.canonical_key(scope, name))

    def get_by_key(self, key: str) -> Grip[Any] | None:
        """Return a grip by canonical key, or ``None`` when missing."""
        return self._keys_by_key.get(key)

    def find_or_add_by_key(
        self,
        key: str,
        *,
        value_type: type[Any] = object,
        nullable: bool = True,
    ) -> Grip[Any]:
        """Find a grip by canonical key or create a generic one when missing."""
        existing = self.get_by_key(key)
        if existing is not None:
            return existing
        if ":" not in key:
            raise ValueError(f"Invalid canonical grip key: {key}")
        scope, name = key.split(":", 1)
        if not scope or not name:
            raise ValueError(f"Invalid canonical grip key: {key}")
        return self.add(
            name,
            None,
            scope=scope,
            value_type=value_type,
            nullable=nullable,
        )

    @overload
    def find_or_add(self, name: str, default: T, *, scope: str = _DEFAULT_SCOPE) -> Grip[T]:
        ...

    @overload
    def find_or_add(
        self,
        name: str,
        *,
        scope: str = _DEFAULT_SCOPE,
        value_type: type[T],
    ) -> Grip[T | None]:
        ...

    @overload
    def find_or_add(
        self,
        name: str,
        default: None,
        *,
        scope: str = _DEFAULT_SCOPE,
        value_type: type[T],
        nullable: Literal[True] = True,
    ) -> Grip[T | None]:
        ...

    @overload
    def find_or_add(
        self,
        name: str,
        default: Any,
        *,
        scope: str = _DEFAULT_SCOPE,
        value_type: type[T],
        nullable: Literal[False] = False,
    ) -> Grip[T]:
        ...

    @overload
    def find_or_add(
        self,
        name: str,
        default: Any,
        *,
        scope: str = _DEFAULT_SCOPE,
        value_type: type[T],
        nullable: Literal[True],
    ) -> Grip[T | None]:
        ...

    def find_or_add(
        self,
        name: str,
        default: Any = _MISSING,
        *,
        scope: str = _DEFAULT_SCOPE,
        value_type: type[Any] | None = None,
        nullable: bool = False,
    ) -> Grip[Any]:
        """Find a grip by ``scope:name`` or add it when missing."""
        existing = self.get(scope, name)
        if existing is not None:
            return existing
        return self.add(
            name,
            default,
            scope=scope,
            value_type=value_type,
            nullable=nullable,
        )
