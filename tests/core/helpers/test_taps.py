from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grip_py.core.base_tap import BaseTap
from grip_py.core.grip import Grip


@dataclass
class DestinationRecorder:
    added: list[Grip[Any]] = field(default_factory=list)
    removed: list[Grip[Any]] = field(default_factory=list)
    detached: int = 0

    def drip_added(self, grip: Grip[Any]) -> None:
        self.added.append(grip)

    def drip_removed(self, grip: Grip[Any]) -> None:
        self.removed.append(grip)

    def on_detach(self) -> None:
        self.detached += 1


class FixedValueTap(BaseTap):
    """Simple deterministic test tap."""

    def __init__(self, values: dict[Grip[Any], Any]):
        super().__init__(provides=tuple(values.keys()))
        self.values = dict(values)

    def produce(self, *, dest_context=None) -> None:
        self.publish(self.values, dest_context=dest_context)


class DestinationContextTap(BaseTap):
    """Test tap with destination lifecycle callbacks."""

    def __init__(self, values: dict[Grip[Any], Any]):
        super().__init__(provides=tuple(values.keys()))
        self.values = dict(values)
        self.contexts: list[DestinationRecorder] = []

    def produce(self, *, dest_context=None) -> None:
        self.publish(self.values, dest_context=dest_context)

    def create_destination_context(self, destination):
        rec = DestinationRecorder()
        self.contexts.append(rec)
        return rec
