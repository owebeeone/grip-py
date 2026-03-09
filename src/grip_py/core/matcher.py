"""Reactive tap matcher runtime built on QueryEvaluator."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from .context import GripContext
from .grip import Grip
from .query_evaluator import AddBindingResult, QueryBinding, QueryEvaluator, RemoveBindingResult


@dataclass(slots=True)
class _Snapshot:
    values: dict[Grip[Any], Any]

    def get_value(self, grip: Grip[Any]) -> Any:
        return self.values.get(grip)


@dataclass(slots=True, init=False)
class TapMatcher:
    """Observe home-context inputs and apply attribution deltas to presentation context."""

    _home_context: GripContext
    _presentation_context: GripContext
    _evaluator: QueryEvaluator
    _values_map: dict[Grip[Any], Any]
    _changed_grips: set[Grip[Any]]
    _drip_subscriptions: dict[Grip[Any], Any]
    _lock: RLock

    def __init__(self, home_context: GripContext, presentation_context: GripContext):
        self._home_context = home_context
        self._presentation_context = presentation_context
        self._evaluator = QueryEvaluator()
        self._values_map = {}
        self._changed_grips = set()
        self._drip_subscriptions = {}
        self._lock = RLock()

    def add_binding(self, binding: QueryBinding) -> None:
        with self._lock:
            result: AddBindingResult = self._evaluator.add_binding(binding)
            for grip in result.new_inputs:
                self._subscribe_to_grip(grip)
            for grip in result.removed_inputs:
                self._unsubscribe_from_grip(grip)

            self._changed_grips.update(binding.query.conditions.keys())
            self._evaluate()

    def remove_binding(self, binding_id: str) -> None:
        with self._lock:
            binding = self._evaluator.get_binding(binding_id)
            if binding is not None:
                self._changed_grips.update(binding.query.conditions.keys())

            result: RemoveBindingResult = self._evaluator.remove_binding(binding_id)
            self._evaluate()

            for grip in result.removed_inputs:
                self._unsubscribe_from_grip(grip)

    def _subscribe_to_grip(self, grip: Grip[Any]) -> None:
        if grip in self._drip_subscriptions:
            return

        drip = self._home_context.get_or_create_consumer(grip)
        self._home_context.get_grok().resolver.add_consumer(self._home_context, grip)

        self._values_map[grip] = drip.get()
        self._changed_grips.add(grip)

        async def on_change(value: Any, *, changed_grip: Grip[Any] = grip) -> None:
            with self._lock:
                self._values_map[changed_grip] = value
                self._changed_grips.add(changed_grip)
                self._evaluate()

        self._drip_subscriptions[grip] = drip.subscribe_async(on_change)

    def _unsubscribe_from_grip(self, grip: Grip[Any]) -> None:
        unsubscribe = self._drip_subscriptions.pop(grip, None)
        if unsubscribe is not None:
            unsubscribe()
        self._values_map.pop(grip, None)

    def _evaluate(self) -> None:
        if not self._changed_grips and not self._drip_subscriptions:
            return

        changed = set(self._changed_grips)
        self._changed_grips.clear()

        delta = self._evaluator.on_grips_changed(changed, _Snapshot(self._values_map))
        self._presentation_context.get_grok().apply_producer_delta(
            self._presentation_context,
            delta,
        )


@dataclass(slots=True, init=False)
class MatchingContext:
    """Convenience wrapper combining home/presentation contexts and a matcher."""

    _home_context: GripContext
    _presentation_context: GripContext
    matcher: TapMatcher

    def __init__(self, home_context: GripContext, presentation_context: GripContext):
        self._home_context = home_context
        self._presentation_context = presentation_context
        self.matcher = TapMatcher(home_context, presentation_context)

    def add_binding(self, binding: QueryBinding) -> None:
        self.matcher.add_binding(binding)

    def remove_binding(self, binding_id: str) -> None:
        self.matcher.remove_binding(binding_id)

    def get_grip_home_context(self) -> GripContext:
        return self._home_context

    def get_grip_consumer_context(self) -> GripContext:
        return self._presentation_context

    def get_grok(self):
        return self._presentation_context.get_grok()
