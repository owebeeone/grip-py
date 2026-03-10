"""Atom tap implementations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import InitVar, dataclass, field
from threading import RLock
from typing import Any

from .base_tap import BaseTap
from .grip import Grip
from .interfaces import TapExecutionMode


def _is_json_persistable(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return all(_is_json_persistable(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_persistable(item) for key, item in value.items())
    return False


@dataclass(eq=False)
class MultiAtomValueTap(BaseTap):
    """Simple mutable value tap for one or more output grips."""

    values: InitVar[dict[Grip[Any], Any]]
    _values: dict[Grip[Any], Any] = field(init=False, default_factory=dict)
    _lock: RLock = field(init=False, default_factory=RLock)

    def __post_init__(self, values: dict[Grip[Any], Any]) -> None:
        super().__init__(provides=tuple(values.keys()), execution_mode="replicated")
        self._values = dict(values)

    def set(self, grip: Grip[Any], value: Any) -> None:
        """Set one provided grip value and publish it to connected consumers."""
        with self._lock:
            if grip not in self._values:
                raise KeyError(f"Grip {grip.name!r} is not provided by this tap")
            self._values[grip] = value
        if self._engine is not None:
            self._engine.note_local_persistence_dirty()
        self.publish({grip: value})

    def get(self, grip: Grip[Any]) -> Any:
        """Return the current value for a provided grip."""
        with self._lock:
            if grip not in self._values:
                raise KeyError(f"Grip {grip.name!r} is not provided by this tap")
            return self._values[grip]

    def update(self, grip: Grip[Any], updater: Callable[[Any], Any]) -> None:
        """Apply a synchronous updater and publish the resulting value."""
        with self._lock:
            if grip not in self._values:
                raise KeyError(f"Grip {grip.name!r} is not provided by this tap")
            next_value = updater(self._values[grip])
            self._values[grip] = next_value
        if self._engine is not None:
            self._engine.note_local_persistence_dirty()
        self.publish({grip: next_value})

    async def update_async(
        self,
        grip: Grip[Any],
        updater: Callable[[Any], Awaitable[Any]],
    ) -> None:
        """Apply an async updater and publish the resulting value."""
        # Not atomic across await boundaries: concurrent writers may interleave.
        current_value = MultiAtomValueTap.get(self, grip)
        next_value = await updater(current_value)
        MultiAtomValueTap.set(self, grip, next_value)

    def produce(self, *, dest_context=None) -> None:
        """Publish current values to all or one destination context."""
        self.publish(self._values, dest_context=dest_context)

    def get_persisted_grip_values(self) -> dict[Grip[Any], Any]:
        """Return JSON-persistable value grips owned by this tap."""
        return {
            grip: value
            for grip, value in self._values.items()
            if _is_json_persistable(value)
        }

    def restore_persisted_grip_value(self, grip: Grip[Any], value: Any) -> bool:
        """Restore one persisted grip value back into this tap."""
        if grip not in self._values:
            return False
        MultiAtomValueTap.set(self, grip, value)
        return True


@dataclass(init=False, eq=False)
class AtomValueTap(MultiAtomValueTap):
    """Single-output atom tap."""

    _grip: Grip[Any]

    def __init__(
        self,
        grip: Grip[Any],
        initial: Any = None,
        *,
        execution_mode: TapExecutionMode = "replicated",
    ):
        value = initial if initial is not None else grip.default
        super().__init__({grip: value})
        self._grip = grip
        self._execution_mode = execution_mode
        self._execution_role = (
            "follower" if execution_mode == "negotiated-primary" else "primary"
        )

    def set(self, value: Any) -> None:  # type: ignore[override]
        """Set the atom value and publish it."""
        super().set(self._grip, value)

    def get(self) -> Any:  # type: ignore[override]
        """Return the current atom value."""
        return super().get(self._grip)

    def update(self, updater: Callable[[Any], Any]) -> None:  # type: ignore[override]
        """Apply a synchronous updater to the atom value."""
        super().update(self._grip, updater)

    async def update_async(self, updater: Callable[[Any], Awaitable[Any]]) -> None:  # type: ignore[override]
        """Apply an async updater to the atom value."""
        await super().update_async(self._grip, updater)


def create_atom_value_tap(
    grip: Grip[Any],
    *,
    initial: Any = None,
    execution_mode: TapExecutionMode = "replicated",
) -> AtomValueTap:
    """Create a single-output mutable atom tap."""
    return AtomValueTap(grip, initial, execution_mode=execution_mode)


def create_multi_atom_value_tap(values: dict[Grip[Any], Any]) -> MultiAtomValueTap:
    """Create a multi-output mutable atom tap."""
    return MultiAtomValueTap(values)
