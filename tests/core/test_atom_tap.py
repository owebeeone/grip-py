from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_atom_tap_set_propagates_to_consumers():
    registry = GripRegistry()
    out = registry.add("Out", 0)
    grok = Grok(registry)

    tap = create_atom_value_tap(out, initial=10)
    grok.register_tap(tap)

    ctx = grok.main_presentation_context.create_child()
    drip = grok.query(out, ctx)
    assert drip.get() == 10

    tap.set(77)
    assert drip.get() == 77
