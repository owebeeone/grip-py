"""Tap materialization registry for shared projection hydration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .grip import Grip
from .interfaces import Grok, SharedProjectionTapSpec, Tap, TapMaterializationRegistry
from .passive_tap import PassiveTap, create_passive_tap


TapMaterializer = Callable[[Grok, SharedProjectionTapSpec], Tap]


class DefaultTapMaterializationRegistry(TapMaterializationRegistry):
    def __init__(self) -> None:
        self._materializers: dict[str, TapMaterializer] = {}

    def register(self, tap_type: str, materializer: TapMaterializer) -> None:
        self._materializers[tap_type] = materializer

    def materialize_tap(self, grok: Grok, spec: SharedProjectionTapSpec) -> Tap:
        materializer = self._materializers.get(spec.tap_type)
        if materializer is not None:
            return materializer(grok, spec)
        provides = tuple(
            grip
            for grip in (
                grok.get_registry().find_or_add_by_key(grip_id) for grip_id in spec.provides
            )
            if isinstance(grip, Grip)
        )
        return create_passive_tap(spec, provides)


def is_passive_tap(tap: Tap) -> bool:
    return isinstance(tap, PassiveTap)
