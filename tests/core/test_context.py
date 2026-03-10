import pytest

from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_context_create_child_and_parent_priority():
    grok = Grok(GripRegistry())
    parent_a = grok.main_presentation_context.create_child("parent-a", priority=0)
    parent_b = grok.main_presentation_context.create_child("parent-b", priority=0)
    child = grok.create_context(context_id="child")

    child.add_parent(parent_a, priority=5)
    child.add_parent(parent_b, priority=1)

    parents = child.get_parents()
    assert [p.ctx for p in parents] == [parent_b, parent_a]


def test_context_cycle_detection():
    grok = Grok(GripRegistry())
    a = grok.create_context(context_id="a")
    b = grok.create_context(a, context_id="b")

    with pytest.raises(ValueError, match="Cycle detected"):
        a.add_parent(b)


def test_get_or_create_child_returns_stable_named_context():
    grok = Grok(GripRegistry())

    first = grok.main_presentation_context.get_or_create_child("stable-child")
    second = grok.main_presentation_context.get_or_create_child("stable-child")

    assert first is second
    assert first.id == "main-presentation/stable-child"
