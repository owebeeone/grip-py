from __future__ import annotations

import time
from dataclasses import dataclass

from grip_py.core.atom_tap import create_atom_value_tap, create_multi_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry
from grip_py.core.matcher import TapMatcher
from grip_py.core.query import with_one_of
from grip_py.core.query_evaluator import QueryBinding


@dataclass(eq=False)
class CountingFactory:
    provides: tuple[Grip[int], ...]
    value: int
    build_count: int = 0

    def build(self):
        self.build_count += 1
        return create_multi_atom_value_tap({self.provides[0]: self.value})


def _resolved_tap(ctx, grip: Grip[int]):
    node = ctx.get_context_node()
    provider = node.get_resolved_providers().get(grip)
    if provider is None:
        return None
    producer = provider.get_producers().get(grip)
    return producer.tap if producer is not None else None


def _wait_until(predicate, timeout: float = 1.0) -> None:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not satisfied before timeout")


def test_tap_matcher_switches_taps_based_on_home_query_values() -> None:
    registry = GripRegistry()
    selector = registry.add("Selector", "none")
    out = registry.add("Out", 0)
    grok = Grok(registry)

    home = grok.main_home_context
    presentation = grok.main_presentation_context.create_child()

    selector_source = create_atom_value_tap(selector, initial="none")
    home.register_tap(selector_source)

    tap_a = create_multi_atom_value_tap({out: 1})
    tap_b = create_multi_atom_value_tap({out: 2})

    matcher = TapMatcher(home, presentation)
    matcher.add_binding(
        QueryBinding(id="a", query=with_one_of(selector, "a").build(), tap=tap_a, base_score=1)
    )
    matcher.add_binding(
        QueryBinding(id="b", query=with_one_of(selector, "b").build(), tap=tap_b, base_score=1)
    )

    drip = grok.query(out, presentation)
    assert drip.get() == 0

    selector_source.set("a")
    _wait_until(lambda: drip.get() == 1 and _resolved_tap(presentation, out) is tap_a)
    assert drip.get() == 1
    assert _resolved_tap(presentation, out) is tap_a

    selector_source.set("b")
    _wait_until(lambda: drip.get() == 2 and _resolved_tap(presentation, out) is tap_b)
    assert drip.get() == 2
    assert _resolved_tap(presentation, out) is tap_b

    selector_source.set("none")
    _wait_until(lambda: _resolved_tap(presentation, out) is None)
    assert _resolved_tap(presentation, out) is None


def test_tap_matcher_supports_factory_bindings() -> None:
    registry = GripRegistry()
    selector = registry.add("Selector", "none")
    out = registry.add("Out", 0)
    grok = Grok(registry)

    home = grok.main_home_context
    presentation = grok.main_presentation_context.create_child()

    selector_source = create_atom_value_tap(selector, initial="none")
    home.register_tap(selector_source)

    factory = CountingFactory(provides=(out,), value=9)

    matcher = TapMatcher(home, presentation)
    matcher.add_binding(
        QueryBinding(
            id="factory",
            query=with_one_of(selector, "x").build(),
            tap=factory,
            base_score=1,
        )
    )

    drip = grok.query(out, presentation)
    assert drip.get() == 0

    selector_source.set("x")
    _wait_until(lambda: drip.get() == 9)
    assert drip.get() == 9
    assert factory.build_count == 1

    selector_source.set("none")
    _wait_until(lambda: _resolved_tap(presentation, out) is None)
    assert _resolved_tap(presentation, out) is None

    selector_source.set("x")
    _wait_until(lambda: drip.get() == 9 and factory.build_count == 2)
    assert drip.get() == 9
    assert factory.build_count == 2
