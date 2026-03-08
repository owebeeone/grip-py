"""Tap attribution and delta helpers for matcher-style resolution."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .grip import Grip


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


class QueryEvaluator:
    """Utility for deterministic output attribution and delta computation."""

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
