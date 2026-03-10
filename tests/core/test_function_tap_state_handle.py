from __future__ import annotations

from typing import Any

from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.function_tap import create_function_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry


def test_function_tap_handle_publishes_and_updates_state() -> None:
    registry = GripRegistry()
    out = registry.add("Out", 0)
    local = registry.add("Local", 0)
    state_counter = registry.add("StateCounter", 0)
    handle_grip = registry.add("FnHandle", value_type=object)
    grok = Grok(registry)

    ctx = grok.main_presentation_context.create_child("ctx_1")
    local_source = create_atom_value_tap(local, initial=3)
    ctx.register_tap(local_source)

    def compute(args: Any) -> dict[Grip[Any], Any]:
        return {
            out: int(args.get_state(state_counter) or 0)
            + int(args.get_destination_param(local) or 0)
        }

    tap = create_function_tap(
        provides=[out],
        destination_param_grips=[local],
        state_grips=[state_counter],
        initial_state={state_counter: 10},
        handle_grip=handle_grip,
        compute=compute,
    )
    grok.main_home_context.register_tap(tap)

    out_drip = grok.query(out, ctx)
    handle_drip = grok.query(handle_grip, ctx)

    assert out_drip.get() == 13
    handle = handle_drip.get()
    assert handle is not None

    handle.set_state(state_counter, 5)
    assert handle.get_state(state_counter) == 5
    assert out_drip.get() == 8


def test_function_tap_compute_result_can_update_state_without_publishing_state_grip() -> None:
    registry = GripRegistry()
    out = registry.add("Out", 0)
    state_counter = registry.add("StateCounter", 0)
    grok = Grok(registry)

    def compute(args: Any) -> dict[Grip[Any], Any]:
        current = int(args.get_state(state_counter) or 0)
        return {
            out: current,
            state_counter: current + 1,
        }

    tap = create_function_tap(
        provides=[out],
        state_grips=[state_counter],
        initial_state={state_counter: 0},
        compute=compute,
    )
    grok.main_home_context.register_tap(tap)

    ctx = grok.main_presentation_context.create_child("ctx_2")
    out_drip = grok.query(out, ctx)

    assert out_drip.get() == 0
    tap.produce(dest_context=ctx)
    assert out_drip.get() == 1
    tap.produce(dest_context=ctx)
    assert out_drip.get() == 2
