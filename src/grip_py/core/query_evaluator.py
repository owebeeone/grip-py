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
    """Evaluator for query bindings with deterministic attribution.

    Optimizations implemented:
    - Incremental re-evaluation of only affected bindings.
    - Structural-change tracking for add/remove/replace bindings.
    - Optional attribution-result cache keyed by current input values.
    """

    def __init__(self, *, use_cache: bool = True) -> None:
        self._bindings: dict[str, QueryBinding] = {}
        self._bindings_by_input: dict[Grip[Any], set[str]] = {}
        self._input_grip_ref_counts: dict[Grip[Any], int] = {}
        self._active_matches: dict[str, MatchedTap] = {}
        self._active_attributions: dict[Any, TapAttribution] = {}
        self._structurally_changed_binding_ids: set[str] = set()
        self._has_structural_changes = False
        self._use_cache = use_cache
        self._cache: dict[tuple[Any, ...], dict[Any, TapAttribution]] = {}
        self._cache_key_grips: tuple[Grip[Any], ...] = ()

    def add_binding(self, binding: QueryBinding) -> AddBindingResult:
        """Add or replace a binding and return input subscription deltas."""
        removed_inputs: set[Grip[Any]] = set()
        if binding.id in self._bindings:
            removed_inputs = self.remove_binding(binding.id).removed_inputs

        self._bindings[binding.id] = binding
        new_inputs: set[Grip[Any]] = set()
        for grip in binding.query.conditions.keys():
            count = self._input_grip_ref_counts.get(grip, 0)
            if count == 0:
                new_inputs.add(grip)
            self._input_grip_ref_counts[grip] = count + 1
            self._bindings_by_input.setdefault(grip, set()).add(binding.id)

        self._cache_key_grips = self._sorted_input_grips()
        self._structurally_changed_binding_ids.add(binding.id)
        self._has_structural_changes = True
        self._cache.clear()

        return AddBindingResult(
            new_inputs=new_inputs,
            removed_inputs=removed_inputs,
        )

    def remove_binding(self, binding_id: str) -> RemoveBindingResult:
        """Remove a binding and return input grips no longer needed."""
        binding = self._bindings.pop(binding_id, None)
        if binding is None:
            return RemoveBindingResult()

        removed_inputs: set[Grip[Any]] = set()
        for grip in binding.query.conditions.keys():
            refs = self._bindings_by_input.get(grip)
            if refs is not None:
                refs.discard(binding_id)
                if not refs:
                    self._bindings_by_input.pop(grip, None)

            count = self._input_grip_ref_counts.get(grip, 0)
            if count <= 1:
                self._input_grip_ref_counts.pop(grip, None)
                removed_inputs.add(grip)
            else:
                self._input_grip_ref_counts[grip] = count - 1

        self._active_matches.pop(binding_id, None)
        self._structurally_changed_binding_ids.discard(binding_id)
        self._has_structural_changes = True
        self._cache_key_grips = self._sorted_input_grips()
        self._cache.clear()
        return RemoveBindingResult(removed_inputs=removed_inputs)

    def get_binding(self, binding_id: str) -> QueryBinding | None:
        return self._bindings.get(binding_id)

    def on_grips_changed(
        self,
        changed_grips: set[Grip[Any]],
        snapshot: ContextSnapshot,
    ) -> EvaluationDelta:
        """Re-evaluate affected bindings and return producer attribution delta."""
        bindings_to_reevaluate = set(self._structurally_changed_binding_ids)
        for grip in changed_grips:
            bindings_to_reevaluate.update(self._bindings_by_input.get(grip, ()))

        if not bindings_to_reevaluate and not self._has_structural_changes:
            return EvaluationDelta()

        for binding_id in bindings_to_reevaluate:
            binding = self._bindings.get(binding_id)
            if binding is None:
                continue
            score = binding.query.match_score(snapshot.get_value)
            if score is None:
                self._active_matches.pop(binding_id, None)
                continue
            self._active_matches[binding_id] = MatchedTap(
                tap=binding.tap,
                score=float(binding.base_score) + float(score),
                binding_id=binding.id,
            )

        self._structurally_changed_binding_ids.clear()
        current = self._attribute_active_matches(snapshot)
        delta = self.diff(self._active_attributions, current)
        self._active_attributions = current
        self._has_structural_changes = False
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
        return set(self._input_grip_ref_counts.keys())

    def _attribute_active_matches(self, snapshot: ContextSnapshot) -> dict[Any, TapAttribution]:
        if not self._use_cache:
            return self.attribute(self._active_matches.values())

        cache_key = self._make_cache_key(snapshot)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        attributed = self.attribute(self._active_matches.values())
        self._cache[cache_key] = attributed
        return attributed

    def _make_cache_key(self, snapshot: ContextSnapshot) -> tuple[Any, ...]:
        return tuple(
            self._normalize_cache_value(snapshot.get_value(grip))
            for grip in self._cache_key_grips
        )

    @staticmethod
    def _normalize_cache_value(value: Any) -> Any:
        try:
            hash(value)
        except TypeError:
            return ("id", id(value))
        return ("value", value)

    def _sorted_input_grips(self) -> tuple[Grip[Any], ...]:
        return tuple(sorted(self._input_grip_ref_counts.keys(), key=lambda grip: grip.key))
