"""Atom tap implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base_tap import BaseTap
from .grip import Grip


@dataclass(init=False, eq=False)
class MultiAtomValueTap(BaseTap):
    """Simple mutable value tap for one or more output grips."""

    _values: dict[Grip[Any], Any]

    def __init__(self, values: dict[Grip[Any], Any]):
        super().__init__(provides=tuple(values.keys()))
        self._values: dict[Grip[Any], Any] = dict(values)

    def set(self, grip: Grip[Any], value: Any) -> None:
        if grip not in self._values:
            raise KeyError(f"Grip {grip.name!r} is not provided by this tap")
        self._values[grip] = value
        self.publish({grip: value})

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


def create_atom_value_tap(grip: Grip[Any], *, initial: Any = None) -> AtomValueTap:
    return AtomValueTap(grip, initial)


def create_multi_atom_value_tap(values: dict[Grip[Any], Any]) -> MultiAtomValueTap:
    return MultiAtomValueTap(values)
