from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.function_tap import create_function_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_function_tap_computes_from_context_value():
    registry = GripRegistry()
    source = registry.add("Source", 2)
    out = registry.add("Out", 0)
    grok = Grok(registry)

    source_tap = create_atom_value_tap(source, initial=5)
    grok.register_tap(source_tap)

    def compute(ctx):
        src = grok.query(source, ctx).get() or 0
        return {out: src * 3}

    f_tap = create_function_tap(provides=[out], compute=compute)
    grok.register_tap(f_tap)

    ctx = grok.main_presentation_context.create_child()
    d = grok.query(out, ctx)
    assert d.get() == 15

    source_tap.set(7)
    # Trigger recomputation for now by explicit produce.
    f_tap.produce(dest_context=ctx)
    assert d.get() == 21
