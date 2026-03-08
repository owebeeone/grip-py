from __future__ import annotations

from grip_py.core.grip import GripRegistry
from grip_py.core.query import Query, with_any_of, with_one_of


def test_query_match_score_uses_value_to_score_maps() -> None:
    registry = GripRegistry()
    color = registry.add("color", "")
    size = registry.add("size", "")

    query = Query(
        {
            color: {"red": 10.0, "blue": 1.0},
            size: {"L": 5.0},
        }
    )

    values = {color: "red", size: "L"}

    assert query.match_score(values.get) == 15.0


def test_query_builder_one_of_any_of_defaults_and_scoring() -> None:
    registry = GripRegistry()
    mode = registry.add("mode", "")

    one = with_one_of(mode, "x").build()
    assert one.match_score({mode: "x"}.get) == 100.0
    assert one.match_score({mode: "y"}.get) is None

    any_query = with_any_of(mode, ["a", "b"], score=7.0).build()
    assert any_query.match_score({mode: "a"}.get) == 7.0
    assert any_query.match_score({mode: "b"}.get) == 7.0
    assert any_query.match_score({mode: "c"}.get) is None


def test_empty_query_does_not_match() -> None:
    query = Query({})
    assert query.match_score(lambda _grip: "anything") is None
