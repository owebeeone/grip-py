import pytest

from grip_py.core.atom_tap import create_atom_value_tap, create_multi_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry
from grip_py.core.query import with_one_of
from grip_py.core.query_evaluator import QueryBinding


def test_context_create_child_and_parent_priority():
    grok = Grok(GripRegistry())
    parent_a = grok.main_presentation_context.create_child(priority=0)
    parent_b = grok.main_presentation_context.create_child(priority=0)
    child = grok.create_context()

    child.add_parent(parent_a, priority=5)
    child.add_parent(parent_b, priority=1)

    parents = child.get_parents()
    assert [p.ctx for p in parents] == [parent_b, parent_a]


def test_context_cycle_detection():
    grok = Grok(GripRegistry())
    a = grok.create_context()
    b = grok.create_context(a)

    with pytest.raises(ValueError, match="Cycle detected"):
        a.add_parent(b)


def test_get_or_create_child_context_reuses_live_context_for_key():
    grok = Grok(GripRegistry())
    parent = grok.main_presentation_context
    init_calls = []

    child = parent.get_or_create_child_context(
        "left",
        init=lambda ctx: init_calls.append(ctx.id),
        context_id="left-child",
    )
    same = parent.get_or_create_child_context(
        "left",
        init=lambda ctx: init_calls.append(f"again:{ctx.id}"),
        context_id="ignored",
    )
    other = parent.get_or_create_child_context("right")

    assert same is child
    assert child.id == "left-child"
    assert other is not child
    assert init_calls == ["left-child"]
    assert [p.ctx for p in child.get_parents()] == [parent]


def test_get_or_create_matching_context_reuses_live_context_and_init_binding():
    registry = GripRegistry()
    mode = registry.add("Mode", "mock")
    out = registry.add("Out", "")
    grok = Grok(registry)
    parent = grok.main_presentation_context
    init_calls = 0

    mode_source = create_atom_value_tap(mode, initial="mock")

    def init(ctx):
        nonlocal init_calls
        init_calls += 1
        ctx.get_grip_home_context().register_tap(mode_source)
        ctx.add_binding(
            QueryBinding(
                id="mock",
                query=with_one_of(mode, "mock", score=10).build(),
                tap=create_multi_atom_value_tap({out: "selected"}),
                base_score=1,
            )
        )

    matching = parent.get_or_create_matching_context("coin:left", init=init)
    same = parent.get_or_create_matching_context("coin:left", init=lambda _ctx: None)

    drip = grok.query(out, matching)

    assert same is matching
    assert init_calls == 1
    assert drip.get() == "selected"
