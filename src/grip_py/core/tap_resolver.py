"""Tap resolution and linking logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .grip import Grip
from .interfaces import Grok, GripContext, GripContextNode, Tap, TapFactory
from .query_evaluator import EvaluationDelta, TapAttribution


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
        home_node = home_context._get_context_node()

        outputs = tuple(getattr(tap_or_factory, "provides", tuple()) or ())
        producer_record = home_node.get_or_create_producer_record(
            tap_or_factory,
            outputs if outputs else None,
        )
        tap = producer_record.tap

        if tap.get_home_context() is None:
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

    def apply_producer_delta(self, context: GripContext, delta: dict[str, Any] | EvaluationDelta) -> None:
        """Apply matcher-style attribution deltas with per-grip ownership updates."""
        added, removed = self._normalize_delta(delta)
        if not added and not removed:
            return

        home_context = context.get_grip_home_context()
        home_node = home_context._get_context_node()
        affected_grips: set[Grip[Any]] = set()

        for tap_key, attribution in removed.items():
            producer_record = self._find_producer_record(home_node, tap_key, attribution)
            if producer_record is None:
                continue
            for grip in tuple(attribution.attributed_grips):
                affected_grips.add(grip)
                stack = home_node.producer_stacks.get(grip)
                if stack is None or producer_record not in stack:
                    continue
                updated_stack = [record for record in stack if record is not producer_record]
                if updated_stack:
                    home_node.producer_stacks[grip] = updated_stack
                    home_node.producers[grip] = updated_stack[-1]
                else:
                    home_node.producer_stacks.pop(grip, None)
                    home_node.producers.pop(grip, None)
                producer_record.outputs.discard(grip)

        for tap_key, attribution in added.items():
            output_grips = tuple(getattr(tap_key, "provides", tuple()))
            producer_record = home_node.get_or_create_producer_record(tap_key, output_grips)
            tap = producer_record.tap
            if tap.get_home_context() is None:
                tap.on_attach(home_context)

            for grip in tuple(attribution.attributed_grips):
                affected_grips.add(grip)
                stack = home_node.producer_stacks.setdefault(grip, [])
                if producer_record in stack:
                    stack.remove(producer_record)
                stack.append(producer_record)
                home_node.producers[grip] = producer_record
                producer_record.outputs.add(grip)

        cleanup_candidates = set(removed.keys()) | set(added.keys())
        for tap_key in cleanup_candidates:
            producer_record = home_node.get_producer_record(tap_key)
            if producer_record is None:
                continue
            if producer_record.outputs:
                continue
            home_node._remove_tap(tap_key)

        for grip in affected_grips:
            self._recompute_grip(grip)

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

        if previous is provider_node and provider_node is not None:
            producer = provider_node.get_producers().get(grip)
            if producer is not None:
                destination = producer.get_destinations().get(consumer_node)
                if destination is not None and grip in destination.get_grips():
                    return

        if previous is not None:
            previous_stack = getattr(previous, "producer_stacks", {}).get(grip)
            if previous_stack:
                for producer_record in tuple(previous_stack):
                    producer_record.remove_destination_grip_for_context(consumer_node, grip)
            else:
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

    def _normalize_delta(
        self,
        delta: dict[str, Any] | EvaluationDelta,
    ) -> tuple[dict[Any, TapAttribution], dict[Any, TapAttribution]]:
        if isinstance(delta, EvaluationDelta):
            return dict(delta.added), dict(delta.removed)

        if not isinstance(delta, dict):
            return {}, {}

        return (
            self._normalize_delta_side(delta.get("added")),
            self._normalize_delta_side(delta.get("removed")),
        )

    def _normalize_delta_side(self, side: Any) -> dict[Any, TapAttribution]:
        normalized: dict[Any, TapAttribution] = {}
        if not isinstance(side, dict):
            return normalized

        for tap_key, attribution in side.items():
            if isinstance(attribution, TapAttribution):
                normalized[tap_key] = attribution
                continue

            if isinstance(attribution, dict):
                grips_raw = attribution.get("attributed_grips", ())
                grips = set(grips_raw) if grips_raw is not None else set()
                normalized[tap_key] = TapAttribution(
                    producer_tap=attribution.get("producer_tap", tap_key),
                    score=float(attribution.get("score", 0.0)),
                    binding_id=str(attribution.get("binding_id", "")),
                    attributed_grips=grips,
                )
                continue

            if isinstance(attribution, (set, list, tuple)):
                normalized[tap_key] = TapAttribution(
                    producer_tap=tap_key,
                    score=0.0,
                    binding_id="",
                    attributed_grips=set(attribution),
                )

        return normalized

    @staticmethod
    def _find_producer_record(home_node: GripContextNode, tap_key: Any, attribution: TapAttribution):
        producer_record = home_node.get_producer_record(tap_key)
        if producer_record is not None:
            return producer_record
        return home_node.get_producer_record(attribution.producer_tap)
