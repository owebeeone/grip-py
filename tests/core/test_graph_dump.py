from __future__ import annotations

from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.graph_dump import GraphDumpKeyRegistry, GripGraphDumper
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_graph_dump_includes_contexts_taps_and_drips() -> None:
    registry = GripRegistry()
    source = registry.add("Source", 0)
    out = registry.add("Out", 0)
    grok = Grok(registry)

    home_source = create_atom_value_tap(source, initial=5)
    out_source = create_atom_value_tap(out, initial=7)
    grok.main_home_context.register_tap(home_source)
    grok.main_home_context.register_tap(out_source)

    ctx = grok.main_presentation_context.create_child()
    drip = grok.query(out, ctx)
    assert drip.get() == 7

    dump = GripGraphDumper(grok).dump()

    assert dump.summary.context_count >= 3
    assert dump.summary.tap_count >= 1
    assert dump.summary.drip_count >= 1

    out_drips = [d for d in dump.nodes.drips if d.grip == "Grip(Out)"]
    assert out_drips
    assert out_drips[0].provider_tap is not None

    taps = dump.nodes.taps
    assert any(t.destinations for t in taps)


def test_graph_dump_key_registry_is_stable_across_dumps() -> None:
    registry = GripRegistry()
    out = registry.add("Out", 0)
    grok = Grok(registry)

    grok.main_home_context.register_tap(create_atom_value_tap(out, initial=1))
    ctx = grok.main_presentation_context.create_child()
    grok.query(out, ctx)

    keys = GraphDumpKeyRegistry()
    dumper = GripGraphDumper(grok, keys=keys)

    d1 = dumper.dump()
    d2 = dumper.dump()

    assert sorted(c.key for c in d1.nodes.contexts) == sorted(c.key for c in d2.nodes.contexts)
    assert sorted(t.key for t in d1.nodes.taps) == sorted(t.key for t in d2.nodes.taps)
    assert sorted(d.key for d in d1.nodes.drips) == sorted(d.key for d in d2.nodes.drips)
