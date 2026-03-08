from grip_py.core.atom_tap import create_multi_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry


def _resolved_context_id(ctx, grip: Grip):
    node = ctx.get_context_node()
    provider = node.get_resolved_providers().get(grip)
    provider_ctx = provider.get_context() if provider is not None else None
    return provider_ctx.id if provider_ctx is not None else None


def _register(context, values):
    tap = create_multi_atom_value_tap(values)
    context.register_tap(tap)
    return tap


def _add_consumer(grok: Grok, context, grip: Grip):
    grok.query(grip, context)


def test_resolver_scenario_1_simple_case():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(ca, context_id="cb")
    _register(ca, {a: "A"})
    _add_consumer(grok, cb, a)

    assert _resolved_context_id(cb, a) == "ca"


def test_resolver_scenario_2_transitive_case():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(ca, context_id="cb")
    cc = grok.create_context(cb, context_id="cc")
    _register(ca, {a: "A"})
    _add_consumer(grok, cc, a)

    assert _resolved_context_id(cc, a) == "ca"


def test_resolver_scenario_3_shadowing_by_ancestor():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(ca, context_id="cb")
    cc = grok.create_context(cb, context_id="cc")
    _register(ca, {a: "A1"})
    _register(cb, {a: "A2"})
    _add_consumer(grok, cc, a)

    assert _resolved_context_id(cc, a) == "cb"


def test_resolver_scenario_4_priority_based_resolution():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(grok.root_context, context_id="cb")
    cc = grok.create_context(ca, context_id="cc")
    cd = grok.create_context(cc, context_id="cd")
    cd.add_parent(cb, priority=1)

    _register(cb, {a: "B"})
    _register(cc, {a: "C"})
    _add_consumer(grok, cd, a)

    assert _resolved_context_id(cd, a) == "cc"


def test_resolver_scenario_5_shadowing_by_key():
    registry = GripRegistry()
    m = registry.add("m", "m")
    n = registry.add("n", "n")
    o = registry.add("o", "o")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(ca, context_id="cb")
    cc = grok.create_context(cb, context_id="cc")

    _register(ca, {m: "M", n: "N", o: "O"})
    _register(cb, {n: "N2"})

    _add_consumer(grok, cc, n)
    _add_consumer(grok, cc, o)

    assert _resolved_context_id(cc, n) == "cb"
    assert _resolved_context_id(cc, o) == "ca"


def test_resolver_scenario_6_dynamic_reparenting():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(grok.root_context, context_id="cb")
    cc = grok.create_context(context_id="cc")
    cc.add_parent(ca, priority=1)

    _register(ca, {a: "A"})
    _register(cb, {a: "B"})
    _add_consumer(grok, cc, a)
    assert _resolved_context_id(cc, a) == "ca"

    cc.add_parent(cb, priority=0)
    assert _resolved_context_id(cc, a) == "cb"


def test_resolver_scenario_7_parent_removal_relinking():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(grok.root_context, context_id="cb")
    cc = grok.create_context(ca, context_id="cc")
    cc.add_parent(cb, priority=1)

    _register(ca, {a: "A"})
    _register(cb, {a: "B"})
    _add_consumer(grok, cc, a)
    assert _resolved_context_id(cc, a) == "ca"

    cc.unlink_parent(ca)
    assert _resolved_context_id(cc, a) == "cb"


def test_resolver_scenario_8_producer_removal():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(grok.root_context, context_id="cb")
    cc = grok.create_context(ca, context_id="cc")
    cc.add_parent(cb, priority=1)

    tap_a = _register(ca, {a: "A"})
    _register(cb, {a: "B"})
    _add_consumer(grok, cc, a)
    assert _resolved_context_id(cc, a) == "ca"

    grok.unregister_tap(tap_a)
    assert _resolved_context_id(cc, a) == "cb"


def test_resolver_scenario_9_add_shadowing_producer():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(ca, context_id="cb")

    _register(ca, {a: "A"})
    _add_consumer(grok, cb, a)
    assert _resolved_context_id(cb, a) == "ca"

    _register(cb, {a: "B"})
    assert _resolved_context_id(cb, a) == "cb"


def test_resolver_scenario_10_no_provider_available():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(ca, context_id="cb")
    _add_consumer(grok, cb, a)

    assert _resolved_context_id(cb, a) is None


def test_resolver_scenario_11_consumer_same_context_as_producer():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    _register(ca, {a: "A"})
    _add_consumer(grok, ca, a)

    assert _resolved_context_id(ca, a) == "ca"


def test_resolver_scenario_12_remove_consumer():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(ca, context_id="cb")

    _register(ca, {a: "A"})
    _add_consumer(grok, cb, a)
    assert _resolved_context_id(cb, a) == "ca"

    grok.resolver.remove_consumer(cb, a)
    assert _resolved_context_id(cb, a) is None


def test_resolver_scenario_13_fallback_after_self_producer_removal():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    cb = grok.create_context(grok.root_context, context_id="cb")
    ca = grok.create_context(cb, context_id="ca")

    _register(cb, {a: "B"})
    tap_a = _register(ca, {a: "A"})

    _add_consumer(grok, ca, a)
    assert _resolved_context_id(ca, a) == "ca"

    grok.unregister_tap(tap_a)
    assert _resolved_context_id(ca, a) == "cb"


def test_resolver_scenario_14_root_parent_deprioritized():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    root = grok.root_context
    cb = grok.create_context(root, context_id="cb")
    cd = grok.create_context(root, context_id="cd")
    cd.add_parent(cb, priority=1)

    _register(root, {a: "ROOT"})
    _register(cb, {a: "CB"})
    _add_consumer(grok, cd, a)

    assert _resolved_context_id(cd, a) == "cb"


def test_resolver_scenario_15_diamond_dependency_resolution():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    root = grok.root_context
    cb = grok.create_context(root, context_id="cb")
    cc = grok.create_context(root, context_id="cc")
    cd = grok.create_context(cb, context_id="cd")
    cd.add_parent(cc)

    _register(root, {a: "ROOT"})
    _register(cc, {a: "CC"})
    _add_consumer(grok, cd, a)

    assert _resolved_context_id(cd, a) == "cc"


def test_resolver_scenario_16_cascading_relink_after_producer_removal():
    registry = GripRegistry()
    a = registry.add("a", "a")
    grok = Grok(registry)

    ca = grok.create_context(grok.root_context, context_id="ca")
    cb = grok.create_context(ca, context_id="cb")
    cc = grok.create_context(cb, context_id="cc")
    cd = grok.create_context(cc, context_id="cd")

    _register(ca, {a: "CA"})
    tap_b = _register(cb, {a: "CB"})

    _add_consumer(grok, cc, a)
    _add_consumer(grok, cd, a)

    assert _resolved_context_id(cc, a) == "cb"
    assert _resolved_context_id(cd, a) == "cb"

    grok.unregister_tap(tap_b)

    assert _resolved_context_id(cc, a) == "ca"
    assert _resolved_context_id(cd, a) == "ca"
