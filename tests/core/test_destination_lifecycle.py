from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry

from .helpers.test_taps import DestinationContextTap


def test_drip_added_callback_runs_when_grip_added():
    registry = GripRegistry()
    out1 = registry.add("Out1", 0)
    out2 = registry.add("Out2", 0)
    grok = Grok(registry)

    tap = DestinationContextTap({out1: 42, out2: 42})
    grok.main_home_context.register_tap(tap)

    ctx = grok.main_presentation_context.create_child()
    d1 = grok.query(out1, ctx)
    d2 = grok.query(out2, ctx)

    assert d1.get() == 42
    assert d2.get() == 42

    assert len(tap.contexts) == 1
    rec = tap.contexts[0]
    assert rec.added == [out1, out2]


def test_drip_removed_and_on_detach_callbacks():
    registry = GripRegistry()
    out1 = registry.add("Out1", 0)
    out2 = registry.add("Out2", 0)
    grok = Grok(registry)

    tap = DestinationContextTap({out1: 1, out2: 2})
    grok.main_home_context.register_tap(tap)

    ctx = grok.main_presentation_context.create_child()
    grok.query(out1, ctx)
    grok.query(out2, ctx)

    rec = tap.contexts[0]

    node = ctx.get_context_node()
    node.remove_consumer_for_grip(out1)
    grok.flush()
    assert rec.removed == [out1]
    assert rec.detached == 0

    node.remove_consumer_for_grip(out2)
    grok.flush()
    assert rec.removed == [out1, out2]
    assert rec.detached == 1


def test_multiple_destinations_have_independent_contexts():
    registry = GripRegistry()
    out = registry.add("Out", 0)
    grok = Grok(registry)

    tap = DestinationContextTap({out: 99})
    grok.main_home_context.register_tap(tap)

    c1 = grok.main_presentation_context.create_child()
    c2 = grok.main_presentation_context.create_child()

    grok.query(out, c1)
    grok.query(out, c2)

    assert len(tap.contexts) == 2
    assert tap.contexts[0] is not tap.contexts[1]
