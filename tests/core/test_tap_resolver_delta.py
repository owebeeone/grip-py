from __future__ import annotations

from grip_py.core.atom_tap import create_multi_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry
from grip_py.core.query_evaluator import EvaluationDelta, TapAttribution


def _resolved_tap(ctx, grip):
    node = ctx.get_context_node()
    provider = node.get_resolved_providers().get(grip)
    if provider is None:
        return None
    producer = provider.get_producers().get(grip)
    return producer.tap if producer is not None else None


def test_apply_producer_delta_partial_transfer_between_taps_in_same_context() -> None:
    registry = GripRegistry()
    a = registry.add("a", 0)
    b = registry.add("b", 0)
    c = registry.add("c", 0)
    grok = Grok(registry)

    ctx = grok.create_context(grok.root_context, context_id="ctx")
    tap1 = create_multi_atom_value_tap({a: 1, b: 2})
    tap2 = create_multi_atom_value_tap({b: 20, c: 30})

    ctx.register_tap(tap1)

    da = grok.query(a, ctx)
    db = grok.query(b, ctx)
    dc = grok.query(c, ctx)
    assert da.get() == 1
    assert db.get() == 2
    assert dc.get() == 0

    delta = EvaluationDelta(
        added={
            tap2: TapAttribution(
                producer_tap=tap2,
                score=100,
                binding_id="new",
                attributed_grips={b, c},
            )
        },
        removed={
            tap1: TapAttribution(
                producer_tap=tap1,
                score=1,
                binding_id="old",
                attributed_grips={b},
            )
        },
    )

    grok.apply_producer_delta(ctx, delta)

    assert _resolved_tap(ctx, a) is tap1
    assert _resolved_tap(ctx, b) is tap2
    assert _resolved_tap(ctx, c) is tap2
    assert da.get() == 1
    assert db.get() == 20
    assert dc.get() == 30
