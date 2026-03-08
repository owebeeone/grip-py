from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_query_returns_same_drip_for_same_context_and_grip():
    registry = GripRegistry()
    value = registry.add("Value", 0)
    grok = Grok(registry)
    ctx = grok.main_presentation_context.create_child()

    tap = create_atom_value_tap(value, initial=42)
    grok.main_home_context.register_tap(tap)

    d1 = grok.query(value, ctx)
    d2 = grok.query(value, ctx)

    assert d1 is d2
    assert d1.get() == 42


def test_first_and_zero_subscriber_callbacks_fire():
    registry = GripRegistry()
    value = registry.add("Value", 1)
    grok = Grok(registry)
    drip = grok.query(value, grok.main_presentation_context)

    counts = {"first": 0, "zero": 0}
    drip.add_on_first_subscriber(lambda: counts.__setitem__("first", counts["first"] + 1))
    drip.add_on_zero_subscribers(lambda: counts.__setitem__("zero", counts["zero"] + 1))

    u1 = drip.subscribe(lambda _: None)
    u2 = drip.subscribe(lambda _: None)
    assert counts["first"] == 1

    u1()
    assert counts["zero"] == 0
    u2()
    grok.flush()
    assert counts["zero"] == 1


def test_register_unregister_re_resolves_to_fallback_provider():
    registry = GripRegistry()
    out = registry.add("Out", 33)
    grok = Grok(registry)

    a = grok.main_presentation_context.create_child()
    b = a.create_child()

    global_tap = create_atom_value_tap(out, initial=123)
    local_tap = create_atom_value_tap(out, initial=7)

    grok.main_home_context.register_tap(global_tap)
    b.register_tap(local_tap)

    d = grok.query(out, b)
    assert d.get() == 7

    grok.unregister_tap(local_tap)
    assert d.get() == 123


def test_proximity_wins_over_registration_order():
    registry = GripRegistry()
    out = registry.add("Out", 0)
    grok = Grok(registry)

    root_child = grok.main_presentation_context.create_child()
    leaf = root_child.create_child()

    far = create_atom_value_tap(out, initial=1)
    near = create_atom_value_tap(out, initial=2)

    grok.main_home_context.register_tap(far)
    root_child.register_tap(near)

    d = grok.query(out, leaf)
    assert d.get() == 2
