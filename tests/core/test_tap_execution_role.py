from grip_py.core.async_tap import create_async_tap
from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.function_tap import create_function_tap
from grip_py.core.graph_dump import GripGraphDumper
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_default_execution_mode_and_role_by_tap_type() -> None:
    registry = GripRegistry()
    input_grip = registry.add("Mode.Input", 0)
    output_grip = registry.add("Mode.Output", 0)

    atom = create_atom_value_tap(input_grip, initial=1)
    fn = create_function_tap(
        provides=[output_grip],
        home_param_grips=[input_grip],
        compute=lambda args: {output_grip: args.get_home_param(input_grip) or 0},
    )
    async_tap = create_async_tap(
        provides=[output_grip],
        destination_param_grips=[input_grip],
        fetcher=lambda params: _async_value((params.destination_params.get(input_grip) or 0) * 10),
    )

    assert atom.get_execution_mode() == "replicated"
    assert atom.get_execution_role() == "primary"
    assert fn.get_execution_mode() == "origin-primary"
    assert fn.get_execution_role() == "primary"
    assert async_tap.get_execution_mode() == "origin-primary"
    assert async_tap.get_execution_role() == "primary"


def test_follower_role_suppresses_function_tap_publication() -> None:
    registry = GripRegistry()
    input_grip = registry.add("Role.Input", 0)
    output_grip = registry.add("Role.Output", 0)
    grok = Grok(registry)
    ctx = grok.main_presentation_context.create_child()

    source = create_atom_value_tap(input_grip, initial=3)
    grok.main_home_context.register_tap(source)

    fn = create_function_tap(
        provides=[output_grip],
        compute=lambda args: {
            output_grip: grok.query(input_grip, grok.main_home_context).get() or 0
        },
    )
    stable_id = fn.id
    fn.set_execution_role("follower")
    grok.main_home_context.register_tap(fn)

    drip = grok.query(output_grip, ctx)
    grok.flush()
    assert drip.get() == 0

    fn.set_execution_role("primary")
    fn.produce()
    grok.flush()
    assert fn.id == stable_id
    assert drip.get() == 3

    source.set(5)
    fn.produce(dest_context=ctx)
    grok.flush()
    assert drip.get() == 5

    fn.set_execution_role("follower")
    source.set(7)
    fn.produce(dest_context=ctx)
    grok.flush()
    assert drip.get() == 5


def test_graph_dump_includes_execution_ownership_metadata() -> None:
    registry = GripRegistry()
    input_grip = registry.add("Dump.Input", 0)
    grok = Grok(registry)
    grok.main_home_context.register_tap(create_atom_value_tap(input_grip, initial=1))

    dump = GripGraphDumper(grok).dump()
    atom_nodes = [node for node in dump.nodes.taps if node.class_name == "AtomValueTap"]
    assert atom_nodes
    assert atom_nodes[0].execution_mode == "replicated"
    assert atom_nodes[0].execution_role == "primary"


async def _async_value(value: int) -> dict:
    return {}
