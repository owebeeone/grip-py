import time

from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.function_tap import create_function_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def _wait_until(predicate, timeout: float = 1.0) -> None:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not satisfied before timeout")


def test_function_tap_computes_from_context_value():
    registry = GripRegistry()
    source = registry.add("Source", 2)
    out = registry.add("Out", 0)
    grok = Grok(registry)

    source_tap = create_atom_value_tap(source, initial=5)
    grok.main_home_context.register_tap(source_tap)

    def compute(ctx):
        src = grok.query(source, ctx).get() or 0
        return {out: src * 3}

    f_tap = create_function_tap(provides=[out], compute=compute)
    grok.main_home_context.register_tap(f_tap)

    ctx = grok.main_presentation_context.create_child("ctx_1")
    d = grok.query(out, ctx)
    assert d.get() == 15

    source_tap.set(7)
    # Trigger recomputation for now by explicit produce.
    f_tap.produce(dest_context=ctx)
    assert d.get() == 21


def test_function_tap_recomputes_from_destination_and_home_params():
    registry = GripRegistry()
    out = registry.add("Out", 0)
    home = registry.add("Home", 0)
    local = registry.add("Local", 0)
    grok = Grok(registry)

    home_tap = create_atom_value_tap(home, initial=100)
    grok.main_home_context.register_tap(home_tap)

    c1 = grok.main_presentation_context.create_child("ctx_2")
    c2 = grok.main_presentation_context.create_child("ctx_3")
    c1_local = create_atom_value_tap(local, initial=1)
    c2_local = create_atom_value_tap(local, initial=2)
    c1.register_tap(c1_local)
    c2.register_tap(c2_local)

    holder = {}

    def compute(ctx):
        tap = holder["tap"]
        home_value = tap.get_home_param_value(home) or 0
        local_value = tap.get_destination_param_value(ctx, local) or 0
        return {out: home_value + local_value}

    f_tap = create_function_tap(
        provides=[out],
        destination_param_grips=[local],
        home_param_grips=[home],
        compute=compute,
    )
    holder["tap"] = f_tap
    grok.main_home_context.register_tap(f_tap)

    d1 = grok.query(out, c1)
    d2 = grok.query(out, c2)
    assert d1.get() == 101
    assert d2.get() == 102

    c1_local.set(8)
    _wait_until(lambda: d1.get() == 108)
    assert d1.get() == 108
    assert d2.get() == 102

    home_tap.set(10)
    _wait_until(lambda: d1.get() == 18 and d2.get() == 12)
    assert d1.get() == 18
    assert d2.get() == 12
