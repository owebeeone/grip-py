from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grip_py.core.grip import GripRegistry
from grip_py.core.query_evaluator import (
    EvaluationDelta,
    MatchedTap,
    QueryEvaluator,
    TapAttribution,
)


@dataclass(eq=False)
class DummyTap:
    provides: tuple[Any, ...]


def test_query_evaluator_attribute_uses_score_then_binding_id_tiebreak() -> None:
    registry = GripRegistry()
    a = registry.add("a", 0)
    b = registry.add("b", 0)
    c = registry.add("c", 0)

    tap1 = DummyTap((a, b))
    tap2 = DummyTap((b, c))

    matches = [
        MatchedTap(tap=tap1, score=10, binding_id="z"),
        MatchedTap(tap=tap2, score=10, binding_id="a"),
    ]

    attributions = QueryEvaluator().attribute(matches)

    assert attributions[tap2].attributed_grips == {b, c}
    assert attributions[tap1].attributed_grips == {a}


def test_query_evaluator_diff_reports_partial_transfer() -> None:
    registry = GripRegistry()
    a = registry.add("a", 0)
    b = registry.add("b", 0)

    tap1 = DummyTap((a, b))
    tap2 = DummyTap((b,))

    previous = {
        tap1: TapAttribution(
            producer_tap=tap1,
            score=1,
            binding_id="old",
            attributed_grips={a, b},
        )
    }
    current = {
        tap1: TapAttribution(
            producer_tap=tap1,
            score=1,
            binding_id="old",
            attributed_grips={a},
        ),
        tap2: TapAttribution(
            producer_tap=tap2,
            score=2,
            binding_id="new",
            attributed_grips={b},
        ),
    }

    delta: EvaluationDelta = QueryEvaluator().diff(previous, current)

    assert delta.removed[tap1].attributed_grips == {b}
    assert delta.added[tap2].attributed_grips == {b}
