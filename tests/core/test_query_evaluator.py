from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grip_py.core.grip import GripRegistry
from grip_py.core.query import Query
from grip_py.core.query_evaluator import (
    EvaluationDelta,
    MatchedTap,
    QueryBinding,
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


def test_query_evaluator_binding_input_deltas_and_evaluation_delta() -> None:
    registry = GripRegistry()
    flag = registry.add("flag", "")
    out = registry.add("out", 0)

    tap = DummyTap((out,))
    evaluator = QueryEvaluator()

    add_result = evaluator.add_binding(
        QueryBinding(id="one", query=Query({flag: "yes"}), tap=tap, base_score=1)
    )
    assert add_result.new_inputs == {flag}
    assert add_result.removed_inputs == set()

    snapshot_values = {flag: "yes"}

    class Snapshot:
        def get_value(self, grip):
            return snapshot_values.get(grip)

    delta = evaluator.on_grips_changed({flag}, Snapshot())
    assert tap in delta.added
    assert delta.added[tap].attributed_grips == {out}

    snapshot_values[flag] = "no"
    delta = evaluator.on_grips_changed({flag}, Snapshot())
    assert tap in delta.removed
    assert delta.removed[tap].attributed_grips == {out}

    remove_result = evaluator.remove_binding("one")
    assert remove_result.removed_inputs == {flag}
