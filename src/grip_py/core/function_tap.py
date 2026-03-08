"""Function-based tap implementations."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from .base_tap import BaseTap
from .grip import Grip
from .interfaces import GripContext


@dataclass(slots=True, eq=False)
class FunctionTapComputeArgs:
    """Compute callback inputs for FunctionTap."""

    dest_context: GripContext | None
    _tap: "FunctionTap"

    def get_home_param(self, grip: Grip[Any]) -> Any:
        return self._tap.get_home_param_value(grip)

    def get_destination_param(
        self,
        grip: Grip[Any],
        dest_context: GripContext | None = None,
    ) -> Any:
        context = dest_context or self.dest_context or self._tap.get_home_context()
        if context is None:
            return grip.default
        return self._tap.get_destination_param_value(context, grip)

    def get_state(self, grip: Grip[Any]) -> Any:
        return self._tap.get_state(grip)

    def __getattr__(self, name: str) -> Any:
        """Compatibility: allow legacy compute(ctx) style usage."""
        context = self.dest_context or self._tap.get_home_context()
        if context is None:
            raise AttributeError(name)
        return getattr(context, name)


class FunctionTapHandle(Protocol):
    """State handle API exposed through optional handle grip."""

    def get_state(self, grip: Grip[Any]) -> Any: ...

    def set_state(self, grip: Grip[Any], value: Any) -> None: ...


ComputeFn = Callable[[FunctionTapComputeArgs], Mapping[Grip[Any], Any]]


@dataclass(init=False, eq=False)
class FunctionTap(BaseTap):
    """Computes output values from context params and optional local state."""

    _compute: ComputeFn
    _state: dict[Grip[Any], Any]
    _state_grips: set[Grip[Any]]
    handle_grip: Grip[Any] | None

    def __init__(
        self,
        *,
        provides: Iterable[Grip[Any]],
        destination_param_grips: Iterable[Grip[Any]] | None = None,
        home_param_grips: Iterable[Grip[Any]] | None = None,
        compute: ComputeFn,
        handle_grip: Grip[Any] | None = None,
        state_grips: Iterable[Grip[Any]] | None = None,
        initial_state: Mapping[Grip[Any], Any]
        | Iterable[tuple[Grip[Any], Any]]
        | None = None,
    ):
        provides_list = list(provides)
        if handle_grip is not None and handle_grip not in provides_list:
            provides_list.append(handle_grip)
        super().__init__(
            provides=tuple(provides_list),
            destination_param_grips=destination_param_grips,
            home_param_grips=home_param_grips,
        )
        self._compute = compute
        self.handle_grip = handle_grip

        init_items = (
            dict(initial_state).items()
            if initial_state is not None
            else tuple()
        )
        self._state = {grip: value for grip, value in init_items}
        self._state_grips = set(state_grips or ()) | set(self._state.keys())

    def get_state(self, grip: Grip[Any]) -> Any:
        return self._state.get(grip)

    def set_state(self, grip: Grip[Any], value: Any) -> None:
        previous = self._state.get(grip)
        if previous == value:
            return
        self._state[grip] = value
        self.produce()

    def produce(self, *, dest_context: GripContext | None = None) -> None:
        if dest_context is not None:
            values = self._compute_for_context(dest_context)
            self.publish(values, dest_context=dest_context)
            return

        if self._producer is None:
            return
        for node in tuple(self._producer.get_destinations().keys()):
            ctx = node.get_context()
            if ctx is None:
                continue
            values = self._compute_for_context(ctx)
            self.publish(values, dest_context=ctx)

    def _compute_for_context(self, dest_context: GripContext | None) -> dict[Grip[Any], Any]:
        args = FunctionTapComputeArgs(dest_context=dest_context, _tap=self)
        raw = dict(self._compute(args))

        publish_values: dict[Grip[Any], Any] = {}
        for grip, value in raw.items():
            if grip in self._state_grips:
                self._state[grip] = value
            else:
                publish_values[grip] = value

        if self.handle_grip is not None:
            publish_values[self.handle_grip] = self

        return publish_values


def create_function_tap(
    *,
    provides: Iterable[Grip[Any]],
    destination_param_grips: Iterable[Grip[Any]] | None = None,
    home_param_grips: Iterable[Grip[Any]] | None = None,
    compute: ComputeFn,
    handle_grip: Grip[Any] | None = None,
    state_grips: Iterable[Grip[Any]] | None = None,
    initial_state: Mapping[Grip[Any], Any] | Iterable[tuple[Grip[Any], Any]] | None = None,
) -> FunctionTap:
    return FunctionTap(
        provides=provides,
        destination_param_grips=destination_param_grips,
        home_param_grips=home_param_grips,
        compute=compute,
        handle_grip=handle_grip,
        state_grips=state_grips,
        initial_state=initial_state,
    )
