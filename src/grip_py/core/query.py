"""Query primitives for matcher-driven tap activation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .grip import Grip

Condition = Any | Callable[[Any], bool | float | int]


@dataclass(frozen=True, slots=True)
class Query:
    """Declarative query: all grip conditions must match."""

    conditions: Mapping[Grip[Any], Condition]

    def match_score(self, get_value: Callable[[Grip[Any]], Any]) -> float | None:
        """Return additive score if query matches, otherwise None."""
        score = 0.0
        for grip, condition in self.conditions.items():
            value = get_value(grip)
            match_score = _condition_score(condition, value)
            if match_score is None:
                return None
            score += match_score
        return score


def _condition_score(condition: Condition, value: Any) -> float | None:
    if callable(condition):
        result = condition(value)
        if isinstance(result, bool):
            return 1.0 if result else None
        numeric = float(result)
        return numeric if numeric > 0 else None
    return 1.0 if value == condition else None
