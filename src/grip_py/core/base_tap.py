"""Base Tap implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .grip import Grip
from .interfaces import Destination, Grok, GripContext, ProducerRecord
from .tap import Tap, TapDestinationContext


@dataclass(init=False, eq=False)
class BaseTap(ABC):
    """Base class for concrete taps."""

    provides: tuple[Grip[Any], ...]
    _home_context: GripContext | None
    _engine: Grok | None
    _producer: ProducerRecord | None

    def __init__(self, *, provides: Iterable[Grip[Any]]):
        self.provides: tuple[Grip[Any], ...] = tuple(provides)
        if not self.provides:
            raise ValueError("Tap must provide at least one grip")
        self._home_context: GripContext | None = None
        self._engine: Grok | None = None
        self._producer: ProducerRecord | None = None

    def get_home_context(self) -> GripContext | None:
        return self._home_context

    def get_provides(self) -> Iterable[Grip[Any]]:
        return self.provides

    def on_attach(self, home_context: GripContext) -> None:
        self._home_context = home_context
        self._engine = home_context.get_grok()
        node = home_context._get_context_node()
        self._producer = node.get_or_create_producer_record(self, self.provides)
        for grip in self.provides:
            node.record_producer(grip, self._producer)

    def on_detach(self) -> None:
        self._home_context = None
        self._engine = None
        self._producer = None

    def on_connect(self, dest_context: GripContext, grip: Grip[Any]) -> None:
        # Default behavior: publish current values for destination.
        self.produce(dest_context=dest_context)

    @abstractmethod
    def produce(self, *, dest_context: GripContext | None = None) -> None:
        """Publish output values."""

    def publish(self, values: dict[Grip[Any], Any], dest_context: GripContext | None = None) -> int:
        """Publish output values to destination(s)."""
        if self._producer is None or self._engine is None:
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
