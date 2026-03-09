"""Base Tap implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any
from uuid import uuid4

from .grip import Grip
from .interfaces import (
    Destination,
    Grok,
    GripContext,
    ProducerRecord,
    TapExecutionMode,
    TapExecutionRole,
)
from .tap import TapDestinationContext


@dataclass(init=False, eq=False)
class BaseTap(ABC):
    """Base class for concrete taps."""

    id: str = field(init=False)
    provides: tuple[Grip[Any], ...] = field(init=False)
    destination_param_grips: tuple[Grip[Any], ...] = field(init=False)
    home_param_grips: tuple[Grip[Any], ...] = field(init=False)
    _home_context: GripContext | None = field(init=False)
    _engine: Grok | None = field(init=False)
    _producer: ProducerRecord | None = field(init=False)
    _home_param_values: dict[Grip[Any], Any] = field(init=False)
    _home_param_unsubscribers: list[Callable[[], None]] = field(init=False)
    _destination_param_values: dict[str, dict[Grip[Any], Any]] = field(init=False)
    _destination_param_unsubscribers: dict[str, list[Callable[[], None]]] = field(init=False)
    _param_lock: RLock = field(init=False)
    _execution_mode: TapExecutionMode = field(init=False)
    _execution_role: TapExecutionRole = field(init=False)

    def __init__(
        self,
        *,
        provides: Iterable[Grip[Any]],
        destination_param_grips: Iterable[Grip[Any]] | None = None,
        home_param_grips: Iterable[Grip[Any]] | None = None,
        execution_mode: TapExecutionMode = "origin-primary",
    ):
        self.id = f"tap_{uuid4().hex[:9]}"
        self.provides: tuple[Grip[Any], ...] = tuple(provides)
        if not self.provides:
            raise ValueError("Tap must provide at least one grip")
        self.destination_param_grips = tuple(destination_param_grips or ())
        self.home_param_grips = tuple(home_param_grips or ())
        self._home_context: GripContext | None = None
        self._engine: Grok | None = None
        self._producer: ProducerRecord | None = None
        self._home_param_values: dict[Grip[Any], Any] = {}
        self._home_param_unsubscribers: list[Callable[[], None]] = []
        self._destination_param_values: dict[str, dict[Grip[Any], Any]] = {}
        self._destination_param_unsubscribers: dict[str, list[Callable[[], None]]] = {}
        self._param_lock = RLock()
        self._execution_mode = execution_mode
        self._execution_role = (
            "follower" if execution_mode == "negotiated-primary" else "primary"
        )

    def get_home_context(self) -> GripContext | None:
        return self._home_context

    def get_execution_mode(self) -> TapExecutionMode:
        return self._execution_mode

    def get_execution_role(self) -> TapExecutionRole:
        return self._execution_role

    def set_execution_role(self, role: TapExecutionRole) -> None:
        self._execution_role = role

    def can_execute_locally(self) -> bool:
        return self._execution_mode == "replicated" or self._execution_role == "primary"

    def get_provides(self) -> Iterable[Grip[Any]]:
        return self.provides

    def on_attach(self, home_context: GripContext) -> None:
        self._home_context = home_context
        self._engine = home_context.get_grok()
        node = home_context._get_context_node()
        self._producer = node.get_or_create_producer_record(self, self.provides)
        for grip in self.provides:
            node.record_producer(grip, self._producer)
        self._bind_home_param_subscriptions()

    def on_detach(self) -> None:
        self._clear_home_param_subscriptions()
        self._clear_destination_param_subscriptions()
        self._home_context = None
        self._engine = None
        self._producer = None

    def on_connect(self, dest_context: GripContext, grip: Grip[Any]) -> None:
        self._bind_destination_param_subscriptions(dest_context)
        # Default behavior: publish current values for destination.
        self.produce(dest_context=dest_context)

    def on_disconnect(self, dest_context: GripContext, grip: Grip[Any]) -> None:
        if self._producer is None:
            self._clear_destination_param_subscriptions_for_context_id(dest_context.id)
            return
        has_destination = any(
            node.id == dest_context.id for node in self._producer.get_destinations()
        )
        if not has_destination:
            self._clear_destination_param_subscriptions_for_context_id(dest_context.id)

    @abstractmethod
    def produce(self, *, dest_context: GripContext | None = None) -> None:
        """Publish output values."""

    def produce_on_dest_params(self, dest_context: GripContext, grip: Grip[Any]) -> None:
        self.produce(dest_context=dest_context)

    def produce_on_home_params(self, grip: Grip[Any]) -> None:
        self.produce()

    def publish(self, values: dict[Grip[Any], Any], dest_context: GripContext | None = None) -> int:
        """Publish output values to destination(s)."""
        if not self.can_execute_locally() or self._producer is None or self._engine is None:
            return 0

        value_map = {grip: value for grip, value in values.items() if grip in self.provides}
        if not value_map:
            return 0

        def updater(ctx: GripContext, grip: Grip[Any], value: Any) -> None:
            self._engine.notify_consumers(ctx, grip, value)

        if dest_context is None:
            return self._producer.publish(value_map, updater)
        return self._producer.publish_to_destination(dest_context, value_map, updater)

    def create_destination_context(self, destination: Destination) -> TapDestinationContext | None:
        return None

    def get_home_param_value(self, grip: Grip[Any] | None) -> Any:
        if grip is None:
            return None
        with self._param_lock:
            return self._home_param_values.get(grip, grip.default)

    def get_home_param_values(self) -> dict[Grip[Any], Any]:
        with self._param_lock:
            return dict(self._home_param_values)

    def get_destination_param_value(self, dest_context: GripContext, grip: Grip[Any]) -> Any:
        with self._param_lock:
            return self._destination_param_values.get(dest_context.id, {}).get(grip, grip.default)

    def get_destination_param_values(self, dest_context: GripContext) -> dict[Grip[Any], Any]:
        with self._param_lock:
            return dict(self._destination_param_values.get(dest_context.id, {}))

    def _bind_home_param_subscriptions(self) -> None:
        if self._engine is None or self._home_context is None or not self.home_param_grips:
            return
        self._clear_home_param_subscriptions()
        for grip in self.home_param_grips:
            drip = self._engine.query(grip, self._home_context)
            with self._param_lock:
                self._home_param_values[grip] = drip.get()

            async def on_value(value: Any, *, current_grip: Grip[Any] = grip) -> None:
                with self._param_lock:
                    self._home_param_values[current_grip] = value
                self.produce_on_home_params(current_grip)

            unsubscribe = self._subscribe_async_skip_initial(drip.subscribe_async, on_value)
            self._home_param_unsubscribers.append(unsubscribe)

    def _clear_home_param_subscriptions(self) -> None:
        with self._param_lock:
            unsubscribers = tuple(self._home_param_unsubscribers)
            self._home_param_unsubscribers.clear()
            self._home_param_values.clear()
        for unsubscribe in unsubscribers:
            unsubscribe()

    def _bind_destination_param_subscriptions(self, dest_context: GripContext) -> None:
        if self._engine is None or not self.destination_param_grips:
            return
        with self._param_lock:
            if dest_context.id in self._destination_param_unsubscribers:
                return

        values: dict[Grip[Any], Any] = {}
        unsubscribers: list[Callable[[], None]] = []
        for grip in self.destination_param_grips:
            drip = self._engine.query(grip, dest_context)
            values[grip] = drip.get()

            async def on_value(
                value: Any,
                *,
                current_grip: Grip[Any] = grip,
                ctx: GripContext = dest_context,
            ) -> None:
                with self._param_lock:
                    dest_values = self._destination_param_values.setdefault(ctx.id, {})
                    dest_values[current_grip] = value
                self.produce_on_dest_params(ctx, current_grip)

            unsubscribe = self._subscribe_async_skip_initial(drip.subscribe_async, on_value)
            unsubscribers.append(unsubscribe)

        with self._param_lock:
            self._destination_param_values[dest_context.id] = values
            self._destination_param_unsubscribers[dest_context.id] = unsubscribers

    def _clear_destination_param_subscriptions(self) -> None:
        with self._param_lock:
            all_unsubscribers = tuple(self._destination_param_unsubscribers.values())
            self._destination_param_unsubscribers.clear()
            self._destination_param_values.clear()
        for unsubscribers in all_unsubscribers:
            for unsubscribe in unsubscribers:
                unsubscribe()

    def _clear_destination_param_subscriptions_for_context_id(self, context_id: str) -> None:
        with self._param_lock:
            unsubscribers = tuple(self._destination_param_unsubscribers.pop(context_id, ()))
            self._destination_param_values.pop(context_id, None)
        for unsubscribe in unsubscribers:
            unsubscribe()

    @staticmethod
    def _subscribe_async_skip_initial(
        subscribe_fn: Callable[[Callable[[Any], Awaitable[None]]], Callable[[], None]],
        callback: Callable[[Any], Awaitable[None]],
    ) -> Callable[[], None]:
        is_first = True

        async def wrapped(value: Any) -> None:
            nonlocal is_first
            if is_first:
                is_first = False
                return
            await callback(value)

        return subscribe_fn(wrapped)
