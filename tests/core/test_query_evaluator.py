from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grip_py.core.grip import Grip, GripRegistry
from grip_py.core.query import Query, with_one_of
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


@dataclass(slots=True)
class Snapshot:
    values: dict[Grip[Any], Any]

    def get_value(self, grip: Grip[Any]) -> Any:
        return self.values.get(grip)


def _find_attr_for_grip(
    attributions: dict[Any, TapAttribution],
    grip: Grip[Any],
) -> TapAttribution | None:
    for attr in attributions.values():
        if grip in attr.attributed_grips:
            return attr
    return None


def _evaluate_and_check_stability(
    evaluator: QueryEvaluator,
    changed_grips: set[Grip[Any]],
    values: dict[Grip[Any], Any],
) -> EvaluationDelta:
    snapshot = Snapshot(values)
    first = evaluator.on_grips_changed(set(changed_grips), snapshot)
    second = evaluator.on_grips_changed(set(changed_grips), snapshot)
    assert second.added == {}
    assert second.removed == {}
    return first


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


def test_query_evaluator_attributes_single_matching_binding() -> None:
    registry = GripRegistry()
    color = registry.add("Color", "")
    out = registry.add("Out", 0)

    tap = DummyTap((out,))
    evaluator = QueryEvaluator()

    add_result = evaluator.add_binding(
        QueryBinding(
            id="A",
            query=with_one_of(color, "red", score=10.0).build(),
            tap=tap,
            base_score=5.0,
        )
    )
    assert add_result.new_inputs == {color}
    assert add_result.removed_inputs == set()

    delta = _evaluate_and_check_stability(evaluator, {color}, {color: "red"})
    attr = _find_attr_for_grip(delta.added, out)
    assert attr is not None
    assert attr.binding_id == "A"
    assert attr.score == 15.0
    assert delta.removed == {}


def test_query_evaluator_independently_attributes_disjoint_outputs() -> None:
    registry = GripRegistry()
    selector = registry.add("Selector", "")
    o1 = registry.add("O1", 0)
    o2 = registry.add("O2", 0)

    t1 = DummyTap((o1,))
    t2 = DummyTap((o2,))

    evaluator = QueryEvaluator()
    r1 = evaluator.add_binding(
        QueryBinding(id="B1", query=with_one_of(selector, "x", score=5.0).build(), tap=t1)
    )
    r2 = evaluator.add_binding(
        QueryBinding(id="B2", query=with_one_of(selector, "x", score=6.0).build(), tap=t2)
    )
    assert r1.new_inputs == {selector}
    assert r2.new_inputs == set()

    delta = _evaluate_and_check_stability(evaluator, {selector}, {selector: "x"})
    assert _find_attr_for_grip(delta.added, o1) is not None
    assert _find_attr_for_grip(delta.added, o2) is not None
    assert delta.removed == {}


def test_query_evaluator_tiebreaks_equal_scores_by_binding_id() -> None:
    registry = GripRegistry()
    toggle = registry.add("Toggle", False)
    out = registry.add("Out", 0)

    t1 = DummyTap((out,))
    t2 = DummyTap((out,))
    query = with_one_of(toggle, True, score=10.0).build()

    evaluator = QueryEvaluator()
    evaluator.add_binding(QueryBinding(id="B1", query=query, tap=t2))
    evaluator.add_binding(QueryBinding(id="A1", query=query, tap=t1))

    delta = _evaluate_and_check_stability(evaluator, {toggle}, {toggle: True})
    attr = _find_attr_for_grip(delta.added, out)
    assert attr is not None
    assert attr.binding_id == "A1"


def test_query_evaluator_bridge_tap_reattributes_overlapping_outputs() -> None:
    registry = GripRegistry()
    g1 = registry.add("G1", "")
    g2 = registry.add("G2", "")
    g3 = registry.add("G3", "")
    o1 = registry.add("O1", 0)
    o2 = registry.add("O2", 0)
    o3 = registry.add("O3", 0)

    t_a = DummyTap((o1,))
    t_b = DummyTap((o2, o3))
    t_bridge = DummyTap((o1, o3))

    evaluator = QueryEvaluator()
    evaluator.add_binding(QueryBinding(id="A", query=with_one_of(g1, "yes", 10.0).build(), tap=t_a))
    evaluator.add_binding(QueryBinding(id="B", query=with_one_of(g2, "yes", 10.0).build(), tap=t_b))

    initial = _evaluate_and_check_stability(
        evaluator,
        {g1, g2, g3},
        {g1: "yes", g2: "yes", g3: "yes"},
    )
    assert _find_attr_for_grip(initial.added, o1) is not None
    assert _find_attr_for_grip(initial.added, o2) is not None
    assert _find_attr_for_grip(initial.added, o3) is not None

    evaluator.add_binding(
        QueryBinding(id="C", query=with_one_of(g3, "yes", 20.0).build(), tap=t_bridge)
    )

    delta = _evaluate_and_check_stability(
        evaluator,
        {g1, g2, g3},
        {g1: "yes", g2: "yes", g3: "yes"},
    )
    assert _find_attr_for_grip(delta.added, o1) is not None
    assert _find_attr_for_grip(delta.added, o3) is not None
    assert _find_attr_for_grip(delta.removed, o1) is not None
    assert _find_attr_for_grip(delta.removed, o3) is not None
    assert _find_attr_for_grip(delta.added, o2) is None


def test_query_evaluator_empty_query_does_not_match() -> None:
    registry = GripRegistry()
    flag = registry.add("Flag", "")
    out = registry.add("Out", 0)

    evaluator = QueryEvaluator()
    tap = DummyTap((out,))
    evaluator.add_binding(QueryBinding(id="E", query=Query({}), tap=tap, base_score=100.0))

    delta = _evaluate_and_check_stability(evaluator, {flag}, {flag: "anything"})
    assert _find_attr_for_grip(delta.added, out) is None


def test_query_evaluator_remove_unknown_binding_is_noop() -> None:
    registry = GripRegistry()
    flag = registry.add("Flag", "")
    out = registry.add("Out", 0)

    evaluator = QueryEvaluator()
    tap = DummyTap((out,))
    evaluator.add_binding(QueryBinding(id="ID", query=with_one_of(flag, "x").build(), tap=tap))

    remove_result = evaluator.remove_binding("UNKNOWN")
    assert remove_result.removed_inputs == set()

    delta = _evaluate_and_check_stability(evaluator, {flag}, {flag: "x"})
    attr = _find_attr_for_grip(delta.added, out)
    assert attr is not None
    assert attr.binding_id == "ID"


def test_query_evaluator_binding_replacement_can_change_winner() -> None:
    registry = GripRegistry()
    g = registry.add("G", "")
    out = registry.add("Out", 0)

    stronger = DummyTap((out,))
    weaker = DummyTap((out,))

    evaluator = QueryEvaluator()
    evaluator.add_binding(QueryBinding(id="S", query=with_one_of(g, "a", 10.0).build(), tap=stronger))
    evaluator.add_binding(QueryBinding(id="W", query=with_one_of(g, "a", 9.0).build(), tap=weaker))

    initial = _evaluate_and_check_stability(evaluator, {g}, {g: "a"})
    initial_attr = _find_attr_for_grip(initial.added, out)
    assert initial_attr is not None
    assert initial_attr.binding_id == "S"

    replace_result = evaluator.add_binding(
        QueryBinding(id="W", query=with_one_of(g, "a", 11.0).build(), tap=weaker)
    )
    assert replace_result.new_inputs == set()
    assert replace_result.removed_inputs == set()

    delta = _evaluate_and_check_stability(evaluator, {g}, {g: "a"})
    assert _find_attr_for_grip(delta.added, out).binding_id == "W"
    assert _find_attr_for_grip(delta.removed, out).binding_id == "S"


def test_query_evaluator_partial_match_missing_input_does_not_match() -> None:
    registry = GripRegistry()
    color = registry.add("Color", "")
    size = registry.add("Size", "")
    out = registry.add("Out", 0)

    tap = DummyTap((out,))
    query = with_one_of(color, "red", 10.0).one_of(size, "L", 5.0).build()

    evaluator = QueryEvaluator()
    evaluator.add_binding(QueryBinding(id="P", query=query, tap=tap))

    delta = _evaluate_and_check_stability(evaluator, {color, size}, {color: "red"})
    assert _find_attr_for_grip(delta.added, out) is None


def test_query_evaluator_match_disappears_when_value_changes() -> None:
    registry = GripRegistry()
    color = registry.add("Color", "")
    out = registry.add("Out", 0)

    tap = DummyTap((out,))
    evaluator = QueryEvaluator()
    evaluator.add_binding(QueryBinding(id="M", query=with_one_of(color, "red", 10.0).build(), tap=tap))

    initial = _evaluate_and_check_stability(evaluator, {color}, {color: "red"})
    assert _find_attr_for_grip(initial.added, out) is not None

    delta = _evaluate_and_check_stability(evaluator, {color}, {color: "blue"})
    assert _find_attr_for_grip(delta.removed, out) is not None
    assert delta.added == {}


def test_query_evaluator_dynamic_registration_prefers_higher_scoring_tap() -> None:
    registry = GripRegistry()
    g = registry.add("G", "")
    out = registry.add("Out", 0)

    low = DummyTap((out,))
    high = DummyTap((out,))
    query = with_one_of(g, "x", 10.0).build()

    evaluator = QueryEvaluator()
    evaluator.add_binding(QueryBinding(id="LOW", query=query, tap=low, base_score=0.0))
    first = _evaluate_and_check_stability(evaluator, {g}, {g: "x"})
    assert _find_attr_for_grip(first.added, out).binding_id == "LOW"

    evaluator.add_binding(QueryBinding(id="HIGH", query=query, tap=high, base_score=5.0))
    delta = _evaluate_and_check_stability(evaluator, {g}, {g: "x"})
    assert _find_attr_for_grip(delta.added, out).binding_id == "HIGH"
    assert _find_attr_for_grip(delta.removed, out).binding_id == "LOW"


def test_query_evaluator_reflects_removal_with_no_grip_changes() -> None:
    registry = GripRegistry()
    color = registry.add("Color", "")
    out = registry.add("Out", 0)

    tap = DummyTap((out,))
    evaluator = QueryEvaluator()
    evaluator.add_binding(
        QueryBinding(id="A", query=with_one_of(color, "red", 10.0).build(), tap=tap, base_score=5.0)
    )

    initial = _evaluate_and_check_stability(evaluator, {color}, {color: "red"})
    assert _find_attr_for_grip(initial.added, out) is not None

    remove_result = evaluator.remove_binding("A")
    assert remove_result.removed_inputs == {color}

    delta = _evaluate_and_check_stability(evaluator, set(), {color: "red"})
    assert delta.added == {}
    assert _find_attr_for_grip(delta.removed, out) is not None


def test_query_evaluator_reflects_addition_with_no_grip_changes() -> None:
    registry = GripRegistry()
    color = registry.add("Color", "")
    out = registry.add("Out", 0)

    tap = DummyTap((out,))
    evaluator = QueryEvaluator()

    _evaluate_and_check_stability(evaluator, {color}, {color: "red"})
    evaluator.add_binding(
        QueryBinding(id="A", query=with_one_of(color, "red", 10.0).build(), tap=tap, base_score=5.0)
    )

    delta = _evaluate_and_check_stability(evaluator, set(), {color: "red"})
    assert delta.removed == {}
    assert _find_attr_for_grip(delta.added, out) is not None


def test_query_evaluator_removing_non_attributed_binding_is_noop_delta() -> None:
    registry = GripRegistry()
    g = registry.add("G", "")
    out = registry.add("Out", 0)

    low = DummyTap((out,))
    high = DummyTap((out,))
    query = with_one_of(g, "x", 10.0).build()

    evaluator = QueryEvaluator()
    evaluator.add_binding(QueryBinding(id="LOW", query=query, tap=low, base_score=0.0))
    _evaluate_and_check_stability(evaluator, {g}, {g: "x"})

    evaluator.add_binding(QueryBinding(id="HIGH", query=query, tap=high, base_score=5.0))
    _evaluate_and_check_stability(evaluator, {g}, {g: "x"})

    evaluator.remove_binding("LOW")
    delta = _evaluate_and_check_stability(evaluator, set(), {g: "x"})
    assert delta.added == {}
    assert delta.removed == {}
