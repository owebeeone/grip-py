"""Atom tap implementations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import InitVar, dataclass, field
from threading import RLock
from typing import Any

from .base_tap import BaseTap
from .grip import Grip


@dataclass(eq=False)
class MultiAtomValueTap(BaseTap):
    """Simple mutable value tap for one or more output grips."""

    values: InitVar[dict[Grip[Any], Any]]
    _values: dict[Grip[Any], Any] = field(init=False, default_factory=dict)
    _lock: RLock = field(init=False, default_factory=RLock)

    def __post_init__(self, values: dict[Grip[Any], Any]) -> None:
        super().__init__(provides=tuple(values.keys()))
        self._values = dict(values)

    def set(self, grip: Grip[Any], value: Any) -> None:
        with self._lock:
            if grip not in self._values:
                raise KeyError(f"Grip {grip.name!r} is not provided by this tap")
            self._values[grip] = value
        self.publish({grip: value})

    def get(self, grip: Grip[Any]) -> Any:
        with self._lock:
            if grip not in self._values:
                raise KeyError(f"Grip {grip.name!r} is not provided by this tap")
            return self._values[grip]

    def update(self, grip: Grip[Any], updater: Callable[[Any], Any]) -> None:
        with self._lock:
            if grip not in self._values:
                raise KeyError(f"Grip {grip.name!r} is not provided by this tap")
            next_value = updater(self._values[grip])
            self._values[grip] = next_value
        self.publish({grip: next_value})

    async def update_async(
        self,
        grip: Grip[Any],
        updater: Callable[[Any], Awaitable[Any]],
    ) -> None:
        # Not atomic across await boundaries: concurrent writers may interleave.
        current_value = MultiAtomValueTap.get(self, grip)
        next_value = await updater(current_value)
        MultiAtomValueTap.set(self, grip, next_value)

    def produce(self, *, dest_context=None) -> None:
        self.publish(self._values, dest_context=dest_context)


@dataclass(init=False, eq=False)
class AtomValueTap(MultiAtomValueTap):
    """Single-output atom tap."""

    _grip: Grip[Any]

    def __init__(self, grip: Grip[Any], initial: Any = None):
        value = initial if initial is not None else grip.default
        super().__init__({grip: value})
        self._grip = grip

    def set(self, value: Any) -> None:  # type: ignore[override]
        super().set(self._grip, value)

    def get(self) -> Any:  # type: ignore[override]
        return super().get(self._grip)

    def update(self, updater: Callable[[Any], Any]) -> None:  # type: ignore[override]
        super().update(self._grip, updater)

    async def update_async(self, updater: Callable[[Any], Awaitable[Any]]) -> None:  # type: ignore[override]
        await super().update_async(self._grip, updater)


def create_atom_value_tap(grip: Grip[Any], *, initial: Any = None) -> AtomValueTap:
    return AtomValueTap(grip, initial)


def create_multi_atom_value_tap(values: dict[Grip[Any], Any]) -> MultiAtomValueTap:
    return MultiAtomValueTap(values)
