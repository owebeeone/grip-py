"""Function-based tap implementations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .base_tap import BaseTap
from .grip import Grip
from .interfaces import GripContext


@dataclass(init=False, eq=False)
class FunctionTap(BaseTap):
    """Computes output values from destination context state."""

    _compute: Callable[[GripContext], dict[Grip[Any], Any]]

    def __init__(
        self,
        *,
        provides: Iterable[Grip[Any]],
        destination_param_grips: Iterable[Grip[Any]] | None = None,
        home_param_grips: Iterable[Grip[Any]] | None = None,
        compute: Callable[[GripContext], dict[Grip[Any], Any]],
    ):
        super().__init__(
            provides=provides,
            destination_param_grips=destination_param_grips,
            home_param_grips=home_param_grips,
        )
        self._compute = compute

    def produce(self, *, dest_context: GripContext | None = None) -> None:
        if dest_context is not None:
            values = self._compute(dest_context)
            self.publish(values, dest_context=dest_context)
            return

        if self._producer is None:
            return
        for node in tuple(self._producer.get_destinations().keys()):
            ctx = node.get_context()
            if ctx is None:
                continue
            values = self._compute(ctx)
            self.publish(values, dest_context=ctx)


def create_function_tap(
    *,
    provides: Iterable[Grip[Any]],
    destination_param_grips: Iterable[Grip[Any]] | None = None,
    home_param_grips: Iterable[Grip[Any]] | None = None,
    compute: Callable[[GripContext], dict[Grip[Any], Any]],
) -> FunctionTap:
    return FunctionTap(
        provides=provides,
        destination_param_grips=destination_param_grips,
        home_param_grips=home_param_grips,
        compute=compute,
    )
