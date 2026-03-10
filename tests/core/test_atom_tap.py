import asyncio

from grip_py.core.atom_tap import create_atom_value_tap, create_multi_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_atom_tap_set_propagates_to_consumers():
    registry = GripRegistry()
    out = registry.add("Out", 0)
    grok = Grok(registry)

    tap = create_atom_value_tap(out, initial=10)
    grok.main_home_context.register_tap(tap)

    ctx = grok.main_presentation_context.create_child("ctx_1")
    drip = grok.query(out, ctx)
    assert drip.get() == 10

    tap.set(77)
    assert drip.get() == 77


def test_atom_tap_update_uses_previous_value():
    registry = GripRegistry()
    out = registry.add("Out", 0)
    grok = Grok(registry)

    tap = create_atom_value_tap(out, initial=10)
    grok.main_home_context.register_tap(tap)
    ctx = grok.main_presentation_context.create_child("ctx_2")
    drip = grok.query(out, ctx)
    assert drip.get() == 10

    tap.update(lambda prev: prev + 5)
    assert tap.get() == 15
    assert drip.get() == 15


def test_multi_atom_tap_update_updates_single_grip():
    registry = GripRegistry()
    a = registry.add("A", 1)
    b = registry.add("B", 2)
    grok = Grok(registry)

    tap = create_multi_atom_value_tap({a: 10, b: 20})
    grok.main_home_context.register_tap(tap)

    ctx = grok.main_presentation_context.create_child("ctx_3")
    da = grok.query(a, ctx)
    db = grok.query(b, ctx)
    assert da.get() == 10
    assert db.get() == 20

    tap.update(a, lambda prev: prev * 3)
    assert tap.get(a) == 30
    assert tap.get(b) == 20
    assert da.get() == 30
    assert db.get() == 20


def test_atom_tap_update_async_applies_result():
    async def scenario() -> None:
        registry = GripRegistry()
        out = registry.add("Out", 0)
        grok = Grok(registry)

        tap = create_atom_value_tap(out, initial=5)
        grok.main_home_context.register_tap(tap)
        ctx = grok.main_presentation_context.create_child("ctx_4")
        drip = grok.query(out, ctx)

        async def updater(prev: int) -> int:
            await asyncio.sleep(0.01)
            return prev + 7

        await tap.update_async(updater)
        assert tap.get() == 12
        assert drip.get() == 12

    asyncio.run(scenario())
