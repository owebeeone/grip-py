"""Tap resolution and linking logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .grip import Grip
from .interfaces import Grok, GripContext, GripContextNode, Tap, TapFactory


class IGripResolver(Protocol):
    """Resolver interface."""

    def add_producer(self, context: GripContext, tap: Tap | TapFactory) -> None: ...

    def remove_producer(self, context: GripContext, tap: Tap) -> None: ...

    def add_consumer(self, context: GripContext, grip: Grip[Any]) -> None: ...

    def remove_consumer(self, context: GripContext, grip: Grip[Any]) -> None: ...


@dataclass(slots=True, eq=False)
class SimpleResolver:
    """Simple nearest-provider resolver."""

    _grok: Grok

    def add_producer(self, context: GripContext, tap_or_factory: Tap | TapFactory) -> None:
        home_context = context.get_grip_home_context()

        if hasattr(tap_or_factory, "build") and not hasattr(tap_or_factory, "provides"):
            tap = tap_or_factory.build()
        else:
            tap = tap_or_factory  # type: ignore[assignment]

        tap.on_attach(home_context)

        for grip in tap.get_provides():
            self._recompute_grip(grip)

    def remove_producer(self, context: GripContext, tap: Tap) -> None:
        home_context = tap.get_home_context() or context.get_grip_home_context()
        home_node = home_context._get_context_node()
        affected = home_node._remove_tap(tap)
        for grip in affected:
            self._recompute_grip(grip)

    def add_consumer(self, context: GripContext, grip: Grip[Any]) -> None:
        consumer = context.get_grip_consumer_context()._get_context_node()
        self._link_consumer(consumer, grip)

    def remove_consumer(self, context: GripContext, grip: Grip[Any]) -> None:
        context.get_grip_consumer_context()._get_context_node().remove_consumer_for_grip(grip)

    def add_parent(self, child_context: GripContext, parent_context: GripContext) -> None:
        self._recompute_all()

    def unlink_parent(self, child_context: GripContext, parent_context: GripContext) -> None:
        self._recompute_all()

    def apply_producer_delta(self, context: GripContext, delta: dict[str, Any]) -> None:
        """Phase-2 hook for matcher/query evaluator integration."""
        added = delta.get("added") if isinstance(delta, dict) else None
        removed = delta.get("removed") if isinstance(delta, dict) else None

        if isinstance(added, dict):
            for tap_or_factory, attribution in added.items():
                self.add_producer(context, tap_or_factory)
        if isinstance(removed, dict):
            for tap_or_factory, attribution in removed.items():
                if hasattr(tap_or_factory, "on_detach"):
                    self.remove_producer(context, tap_or_factory)

    def _recompute_all(self) -> None:
        nodes = self._grok.get_graph().values()
        seen_grips: set[Grip[Any]] = set()
        for node in nodes:
            for grip in tuple(node.get_consumers().keys()):
                seen_grips.add(grip)
        for grip in seen_grips:
            self._recompute_grip(grip)

    def _recompute_grip(self, grip: Grip[Any]) -> None:
        for node in self._grok.get_graph().values():
            live = node.get_live_drip_for_grip(grip)
            if live is not None:
                self._link_consumer(node, grip)

    def _link_consumer(self, consumer_node: GripContextNode, grip: Grip[Any]) -> None:
        provider_node = self._resolve_provider_node(consumer_node, grip)
        previous = consumer_node.get_resolved_providers().get(grip)

        if previous is provider_node:
            return

        if previous is not None:
            previous.remove_destination_for_context(grip, consumer_node)

        if provider_node is None:
            consumer_node.get_resolved_providers().pop(grip, None)
            return

        producer = provider_node.get_producers().get(grip)
        if producer is None:
            consumer_node.get_resolved_providers().pop(grip, None)
            return

        producer.add_destination_grip(consumer_node, grip)
        consumer_node.set_resolved_provider(grip, provider_node)

    def _resolve_provider_node(
        self,
        start_node: GripContextNode,
        grip: Grip[Any],
    ) -> GripContextNode | None:
        visited: set[GripContextNode] = set()
        queue: list[GripContextNode] = [start_node]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            if grip in current.get_producers():
                return current

            non_roots: list[GripContextNode] = []
            roots: list[GripContextNode] = []
            for pref in current.get_parents_with_priority():
                parent = pref.node
                if parent in visited:
                    continue
                if parent.is_root():
                    roots.append(parent)
                else:
                    non_roots.append(parent)
            queue.extend(non_roots)
            queue.extend(roots)

        return None
