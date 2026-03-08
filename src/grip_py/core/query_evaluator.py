"""Tap attribution and delta helpers for matcher-style resolution."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .grip import Grip
from .query import Query


@dataclass(frozen=True, slots=True)
class MatchedTap:
    """A matched producer with evaluation metadata."""

    tap: Any
    score: float
    binding_id: str


@dataclass(frozen=True, slots=True)
class TapAttribution:
    """Per-tap output attribution."""

    producer_tap: Any
    score: float
    binding_id: str
    attributed_grips: set[Grip[Any]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class EvaluationDelta:
    """Incremental attribution change to apply on a context."""

    added: dict[Any, TapAttribution] = field(default_factory=dict)
    removed: dict[Any, TapAttribution] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QueryBinding:
    """Binding between a query and a producer tap/factory."""

    id: str
    query: Query
    tap: Any
    base_score: float = 0.0


@dataclass(frozen=True, slots=True)
class AddBindingResult:
    """Input grip delta after adding or replacing a binding."""

    new_inputs: set[Grip[Any]] = field(default_factory=set)
    removed_inputs: set[Grip[Any]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class RemoveBindingResult:
    """Input grip delta after removing a binding."""

    removed_inputs: set[Grip[Any]] = field(default_factory=set)


class ContextSnapshot(Protocol):
    """Query evaluation snapshot protocol."""

    def get_value(self, grip: Grip[Any]) -> Any: ...


class QueryEvaluator:
    """Evaluator for query bindings with deterministic attribution."""

    def __init__(self) -> None:
        self._bindings: dict[str, QueryBinding] = {}
        self._active_attributions: dict[Any, TapAttribution] = {}

    def add_binding(self, binding: QueryBinding) -> AddBindingResult:
        """Add or replace a binding and return input subscription deltas."""
        before_inputs = self._all_inputs()
        self._bindings[binding.id] = binding
        after_inputs = self._all_inputs()
        return AddBindingResult(
            new_inputs=after_inputs - before_inputs,
            removed_inputs=before_inputs - after_inputs,
        )

    def remove_binding(self, binding_id: str) -> RemoveBindingResult:
        """Remove a binding and return input grips no longer needed."""
        before_inputs = self._all_inputs()
        self._bindings.pop(binding_id, None)
        after_inputs = self._all_inputs()
        return RemoveBindingResult(removed_inputs=before_inputs - after_inputs)

    def get_binding(self, binding_id: str) -> QueryBinding | None:
        return self._bindings.get(binding_id)

    def on_grips_changed(
        self,
        changed_grips: set[Grip[Any]],
        snapshot: ContextSnapshot,
    ) -> EvaluationDelta:
        """Re-evaluate current bindings and return producer attribution delta."""
        current = self.evaluate(snapshot.get_value)
        delta = self.diff(self._active_attributions, current)
        self._active_attributions = current
        return delta

    def evaluate(self, get_value: Callable[[Grip[Any]], Any]) -> dict[Any, TapAttribution]:
        """Evaluate all bindings against a value lookup callable."""
        matches: list[MatchedTap] = []
        for binding in self._bindings.values():
            score = binding.query.match_score(get_value)
            if score is None:
                continue
            matches.append(
                MatchedTap(
                    tap=binding.tap,
                    score=float(binding.base_score) + float(score),
                    binding_id=binding.id,
                )
            )
        return self.attribute(matches)

    def attribute(self, matches: Iterable[MatchedTap]) -> dict[Any, TapAttribution]:
        """Assign output grips to winning taps by score then binding-id tie-break."""
        ordered = sorted(matches, key=lambda m: (-m.score, m.binding_id))
        seen: set[Grip[Any]] = set()
        attributions: dict[Any, TapAttribution] = {}

        for matched in ordered:
            provides = tuple(getattr(matched.tap, "provides", ()) or ())
            novel = {grip for grip in provides if grip not in seen}
            if not novel:
                continue
            seen.update(novel)
            attributions[matched.tap] = TapAttribution(
                producer_tap=matched.tap,
                score=matched.score,
                binding_id=matched.binding_id,
                attributed_grips=novel,
            )

        return attributions

    def diff(
        self,
        previous: dict[Any, TapAttribution],
        current: dict[Any, TapAttribution],
    ) -> EvaluationDelta:
        """Compute per-tap added/removed grip attributions."""
        added: dict[Any, TapAttribution] = {}
        removed: dict[Any, TapAttribution] = {}

        keys = set(previous.keys()) | set(current.keys())
        for tap in keys:
            prev_attr = previous.get(tap)
            cur_attr = current.get(tap)
            prev_grips = set(prev_attr.attributed_grips if prev_attr else ())
            cur_grips = set(cur_attr.attributed_grips if cur_attr else ())

            removed_grips = prev_grips - cur_grips
            added_grips = cur_grips - prev_grips

            if removed_grips:
                source = prev_attr or cur_attr
                if source is not None:
                    removed[tap] = TapAttribution(
                        producer_tap=source.producer_tap,
                        score=source.score,
                        binding_id=source.binding_id,
                        attributed_grips=removed_grips,
                    )
            if added_grips:
                source = cur_attr or prev_attr
                if source is not None:
                    added[tap] = TapAttribution(
                        producer_tap=source.producer_tap,
                        score=source.score,
                        binding_id=source.binding_id,
                        attributed_grips=added_grips,
                    )

        return EvaluationDelta(added=added, removed=removed)

    def _all_inputs(self) -> set[Grip[Any]]:
        inputs: set[Grip[Any]] = set()
        for binding in self._bindings.values():
            inputs.update(binding.query.conditions.keys())
        return inputs
