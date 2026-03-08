import pytest

from grip_py.core.atom_tap import create_multi_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry


def _register(context, values):
    tap = create_multi_atom_value_tap(values)
    context.register_tap(tap)
    return tap


def _consume(grok: Grok, context, *grips: Grip):
    for grip in grips:
        grok.query(grip, context)


def _resolved_context_id(ctx, grip: Grip):
    node = ctx.get_context_node()
    provider = node.get_resolved_providers().get(grip)
    provider_ctx = provider.get_context() if provider is not None else None
    return provider_ctx.id if provider_ctx is not None else None


def _resolved_tap(ctx, grip: Grip):
    node = ctx.get_context_node()
    provider = node.get_resolved_providers().get(grip)
    if provider is None:
        return None
    producer = provider.get_producers().get(grip)
    return producer.tap if producer is not None else None


def test_complex_hierarchy_multi_parent_partial_resolution():
    registry = GripRegistry()
    a = registry.add("a", 0)
    b = registry.add("b", 0)
    c = registry.add("c", 0)
    grok = Grok(registry)

    root = grok.root_context
    left = grok.create_context(root, context_id="left")
    right = grok.create_context(root, context_id="right")
    leaf = grok.create_context(left, context_id="leaf")
    leaf.add_parent(right, priority=1)

    _register(root, {a: 1, b: 2, c: 3})
    _register(left, {b: 20})
    _register(right, {c: 30})

    _consume(grok, leaf, a, b, c)

    assert _resolved_context_id(leaf, a) == "root"
    assert _resolved_context_id(leaf, b) == "left"
    assert _resolved_context_id(leaf, c) == "right"


def test_complex_hierarchy_priority_flip_switches_conflicting_grip_only():
    registry = GripRegistry()
    a = registry.add("a", 0)
    b = registry.add("b", 0)
    c = registry.add("c", 0)
    grok = Grok(registry)

    root = grok.root_context
    pa = grok.create_context(root, context_id="pa")
    pb = grok.create_context(root, context_id="pb")
    leaf = grok.create_context(pa, context_id="leaf")
    leaf.add_parent(pb, priority=1)

    _register(root, {a: 1})
    _register(pa, {b: 10})
    _register(pb, {b: 100, c: 300})

    _consume(grok, leaf, a, b, c)

    assert _resolved_context_id(leaf, a) == "root"
    assert _resolved_context_id(leaf, b) == "pa"
    assert _resolved_context_id(leaf, c) == "pb"

    leaf.add_parent(pb, priority=0)
    leaf.add_parent(pa, priority=1)

    assert _resolved_context_id(leaf, a) == "root"
    assert _resolved_context_id(leaf, b) == "pb"
    assert _resolved_context_id(leaf, c) == "pb"


def test_complex_hierarchy_parent_removal_fallback_is_per_grip():
    registry = GripRegistry()
    a = registry.add("a", 0)
    b = registry.add("b", 0)
    c = registry.add("c", 0)
    grok = Grok(registry)

    root = grok.root_context
    p1 = grok.create_context(root, context_id="p1")
    p2 = grok.create_context(root, context_id="p2")
    leaf = grok.create_context(p1, context_id="leaf")
    leaf.add_parent(p2, priority=1)

    _register(root, {a: 1, b: 2, c: 3})
    _register(p1, {b: 20})
    _register(p2, {c: 30})

    _consume(grok, leaf, a, b, c)

    assert _resolved_context_id(leaf, a) == "root"
    assert _resolved_context_id(leaf, b) == "p1"
    assert _resolved_context_id(leaf, c) == "p2"

    leaf.unlink_parent(p1)

    assert _resolved_context_id(leaf, a) == "root"
    assert _resolved_context_id(leaf, b) == "root"
    assert _resolved_context_id(leaf, c) == "p2"


def test_complex_hierarchy_branch_overrides_are_isolated():
    registry = GripRegistry()
    b = registry.add("b", 0)
    grok = Grok(registry)

    root = grok.root_context
    branch_a = grok.create_context(root, context_id="branch_a")
    branch_b = grok.create_context(root, context_id="branch_b")
    leaf_a = grok.create_context(branch_a, context_id="leaf_a")
    leaf_b = grok.create_context(branch_b, context_id="leaf_b")

    _register(root, {b: 1})
    _register(branch_a, {b: 10})

    _consume(grok, leaf_a, b)
    _consume(grok, leaf_b, b)

    assert _resolved_context_id(leaf_a, b) == "branch_a"
    assert _resolved_context_id(leaf_b, b) == "root"


@pytest.mark.xfail(
    strict=True,
    reason="Known gap: same-context overlapping grips do not restore previous producer on removal",
)
def test_same_context_partial_override_restores_previous_provider_on_remove():
    registry = GripRegistry()
    a = registry.add("a", 0)
    b = registry.add("b", 0)
    c = registry.add("c", 0)
    grok = Grok(registry)

    ctx = grok.create_context(grok.root_context, context_id="ctx")

    tap1 = _register(ctx, {a: 1, b: 2, c: 3})
    _consume(grok, ctx, a, b, c)

    tap2 = _register(ctx, {b: 200})

    assert _resolved_tap(ctx, a) is tap1
    assert _resolved_tap(ctx, b) is tap2
    assert _resolved_tap(ctx, c) is tap1

    grok.unregister_tap(tap2)

    assert _resolved_tap(ctx, a) is tap1
    assert _resolved_tap(ctx, b) is tap1
    assert _resolved_tap(ctx, c) is tap1


@pytest.mark.xfail(
    strict=True,
    reason="Known gap: same-context overlap stack does not restore previous winner per grip",
)
def test_same_context_multi_level_overlap_restores_stack_order_on_remove():
    registry = GripRegistry()
    b = registry.add("b", 0)
    grok = Grok(registry)

    ctx = grok.create_context(grok.root_context, context_id="ctx")

    tap1 = _register(ctx, {b: 1})
    _consume(grok, ctx, b)
    assert _resolved_tap(ctx, b) is tap1

    tap2 = _register(ctx, {b: 2})
    assert _resolved_tap(ctx, b) is tap2

    tap3 = _register(ctx, {b: 3})
    assert _resolved_tap(ctx, b) is tap3

    grok.unregister_tap(tap3)
    assert _resolved_tap(ctx, b) is tap2

    grok.unregister_tap(tap2)
    assert _resolved_tap(ctx, b) is tap1
