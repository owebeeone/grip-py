from __future__ import annotations

from dataclasses import dataclass

from grip_py.core.atom_tap import create_multi_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry
from grip_py.core.query_evaluator import EvaluationDelta, TapAttribution


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


def test_register_tap_factory_builds_and_publishes() -> None:
    registry = GripRegistry()
    out = registry.add("Out", 0)
    grok = Grok(registry)

    factory = CountingFactory(provides=(out,), value=7)
    grok.main_home_context.register_tap(factory)

    ctx = grok.main_presentation_context.create_child("ctx_1")
    drip = grok.query(out, ctx)

    assert drip.get() == 7
    assert factory.build_count == 1


def test_delta_add_remove_with_factory_key_and_producer_tap_fallback() -> None:
    registry = GripRegistry()
    out = registry.add("Out", 0)
    grok = Grok(registry)

    factory = CountingFactory(provides=(out,), value=42)
    ctx = grok.main_presentation_context.create_child("ctx_2")
    drip = grok.query(out, ctx)

    add_delta = EvaluationDelta(
        added={
            factory: TapAttribution(
                producer_tap=factory,
                score=1,
                binding_id="add",
                attributed_grips={out},
            )
        },
        removed={},
    )
    grok.apply_producer_delta(ctx, add_delta)

    assert drip.get() == 42
    assert factory.build_count == 1
    built_tap = _resolved_tap(ctx, out)
    assert built_tap is not None

    remove_delta = EvaluationDelta(
        added={},
        removed={
            factory: TapAttribution(
                producer_tap=built_tap,
                score=1,
                binding_id="remove",
                attributed_grips={out},
            )
        },
    )
    grok.apply_producer_delta(ctx, remove_delta)

    assert _resolved_tap(ctx, out) is None

    grok.apply_producer_delta(ctx, add_delta)
    assert factory.build_count == 2
