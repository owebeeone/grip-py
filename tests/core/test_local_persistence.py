from glial_local.in_memory import InMemoryGripSessionStore

from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.function_tap import create_function_tap
from grip_py.core.grok import Grok
from grip_py.core.graph_dump import GripGraphDumper
from grip_py.core.grip import GripRegistry
from grip_py.core.local_persistence import (
    apply_shared_projection_snapshot,
    build_shared_projection_snapshot,
)


def test_local_persistence_writes_atom_state_to_store() -> None:
    registry = GripRegistry()
    count = registry.add("Count", 0)
    grok = Grok(registry)
    tap = create_atom_value_tap(count, initial=1)
    grok.main_home_context.register_tap(tap)

    store = InMemoryGripSessionStore()
    grok.attach_local_persistence(
        session_id="session-local-a",
        title="Local test",
        store=store,
        flush_delay_ms=0,
    )

    tap.set(9)
    grok.flush_local_persistence()

    hydrated = store.hydrate("session-local-a")
    assert hydrated.snapshot.contexts[grok.main_home_context.id].drips[count.key].value == 9


def test_local_persistence_hydrates_atom_state_into_fresh_runtime() -> None:
    store = InMemoryGripSessionStore()

    registry_a = GripRegistry()
    count_a = registry_a.add("Count", 0)
    grok_a = Grok(registry_a)
    tap_a = create_atom_value_tap(count_a, initial=2)
    grok_a.main_home_context.register_tap(tap_a)
    grok_a.attach_local_persistence(
        session_id="session-local-b",
        title="Hydrate test",
        store=store,
        flush_delay_ms=0,
    )
    tap_a.set(11)
    grok_a.flush_local_persistence()

    registry_b = GripRegistry()
    count_b = registry_b.add("Count", 0)
    grok_b = Grok(registry_b)
    tap_b = create_atom_value_tap(count_b, initial=2)
    grok_b.main_home_context.register_tap(tap_b)
    grok_b.attach_local_persistence(
        session_id="session-local-b",
        title="Hydrate test",
        store=store,
        flush_delay_ms=0,
    )

    assert tap_b.get() == 11
    assert grok_b.query(count_b, grok_b.main_presentation_context).get() == 11


def test_shared_projection_hydrates_passive_taps_in_fresh_runtime() -> None:
    source_registry = GripRegistry()
    input_grip = source_registry.add("Shared.Input", 0)
    output_grip = source_registry.add("Shared.Output", 0)
    source_grok = Grok(source_registry)
    source_context = source_grok.main_presentation_context.create_child("shared-dest")

    source_tap = create_atom_value_tap(input_grip, initial=4)
    source_grok.main_home_context.register_tap(source_tap)
    function_tap = create_function_tap(
        provides=[output_grip],
        home_param_grips=[input_grip],
        compute=lambda args: {output_grip: (args.get_home_param(input_grip) or 0) * 5},
    )
    source_grok.main_home_context.register_tap(function_tap)

    source_drip = source_grok.query(output_grip, source_context)
    source_grok.flush()
    assert source_drip.get() == 20

    shared_projection = build_shared_projection_snapshot(source_grok, "shared-session-a")

    follower_registry = GripRegistry()
    follower_grok = Grok(follower_registry)
    apply_shared_projection_snapshot(follower_grok, shared_projection)

    follower_input = follower_registry.get_by_key(input_grip.key)
    follower_output = follower_registry.get_by_key(output_grip.key)
    assert follower_input is not None
    assert follower_output is not None

    follower_context = follower_grok.get_context_by_id(source_context.id)
    assert follower_context is not None
    assert follower_grok.query(follower_input, follower_grok.main_home_context).get() == 4
    assert follower_grok.query(follower_output, follower_context).get() == 20

    dump = GripGraphDumper(follower_grok).dump()
    assert any(node.class_name == "PassiveTap" for node in dump.nodes.taps)
