from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_gc_cleanup_removes_stale_children_references():
    registry = GripRegistry()
    grok = Grok(registry)

    main = grok.main_presentation_context
    child1 = main.create_child()
    child2 = main.create_child()

    main_node = main.get_context_node()
    graph = grok._graph  # internal test access

    child1_node = graph.get_node(child1)
    child2_node = graph.get_node(child2)

    assert child1_node is not None
    assert child2_node is not None

    graph.clear_context_node(child1_node)
    graph.clear_context_node(child2_node)
    graph._nodes.pop(child1.id, None)
    graph._nodes.pop(child2.id, None)

    graph.gc_sweep()

    assert len(main_node.get_children_nodes()) == 0


def test_snapshot_sanity_detects_orphaned_node_refs():
    registry = GripRegistry()
    grok = Grok(registry)

    main = grok.main_presentation_context
    child = main.create_child()

    graph = grok._graph
    main_node = main.get_context_node()
    graph._nodes.pop(child.id, None)

    sanity = grok.get_graph_sanity_check()
    assert any(node.id == child.id for node in sanity.missing_nodes)
    assert all(node.id != child.id for node in main_node.get_children_nodes())
