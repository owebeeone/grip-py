"""Query primitives for matcher-driven tap activation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .grip import Grip

QueryMatchScoreMap = Mapping[Any, float]
QueryConditions = Mapping[Grip[Any], QueryMatchScoreMap]


@dataclass(frozen=True, slots=True)
class Query:
    """Declarative query of value->score maps for each input grip.

    A query matches when all condition grips are present and each grip's current
    value exists in that grip's score-map.
    """

    conditions: QueryConditions

    def match_score(self, get_value: Callable[[Grip[Any]], Any]) -> float | None:
        """Return additive score if query matches, otherwise None."""
        if not self.conditions:
            return None

        score = 0.0
        for grip, values_and_scores in self.conditions.items():
            value = get_value(grip)
            if value not in values_and_scores:
                return None
            score += float(values_and_scores[value])
        return score


@dataclass(slots=True, init=False)
class QueryBuilder:
    """Builder for score-based query conditions."""

    _conditions: dict[Grip[Any], dict[Any, float]]
    _is_built: bool

    def __init__(self) -> None:
        self._conditions = {}
        self._is_built = False

    def one_of(self, grip: Grip[Any], value: Any, score: float = 100.0) -> QueryBuilder:
        """Add a single-value match condition for a grip."""
        self._copy_on_write()
        values = self._conditions.setdefault(grip, {})
        values[value] = float(score)
        return self

    def any_of(
        self,
        grip: Grip[Any],
        values: Sequence[Any],
        score: float = 100.0,
    ) -> QueryBuilder:
        """Add a multi-value OR-match condition for a grip."""
        self._copy_on_write()
        score_value = float(score)
        value_scores = self._conditions.setdefault(grip, {})
        for value in values:
            value_scores[value] = score_value
        return self

    def build(self) -> Query:
        """Build an immutable query snapshot."""
        self._is_built = True
        return Query(
            {
                grip: dict(value_scores)
                for grip, value_scores in self._conditions.items()
            }
        )

    # grip-core naming parity helpers
    def oneOf(self, grip: Grip[Any], value: Any, score: float = 100.0) -> QueryBuilder:
        """Alias of :meth:`one_of` for grip-core naming parity."""
        return self.one_of(grip, value, score)

    def anyOf(
        self,
        grip: Grip[Any],
        values: Sequence[Any],
        score: float = 100.0,
    ) -> QueryBuilder:
        """Alias of :meth:`any_of` for grip-core naming parity."""
        return self.any_of(grip, values, score)

    def _copy_on_write(self) -> None:
        if self._is_built:
            self._conditions = {
                grip: dict(value_scores)
                for grip, value_scores in self._conditions.items()
            }
            self._is_built = False


@dataclass(slots=True)
class QueryBuilderFactory:
    """Factory for creating new :class:`QueryBuilder` instances."""

    def new_query(self) -> QueryBuilder:
        """Return a new empty query builder."""
        return QueryBuilder()


def with_one_of(grip: Grip[Any], value: Any, score: float = 100.0) -> QueryBuilder:
    """Create a builder pre-seeded with a single value condition."""
    return QueryBuilderFactory().new_query().one_of(grip, value, score)


def with_any_of(grip: Grip[Any], values: Sequence[Any], score: float = 100.0) -> QueryBuilder:
    """Create a builder pre-seeded with a multi-value condition."""
    return QueryBuilderFactory().new_query().any_of(grip, values, score)
