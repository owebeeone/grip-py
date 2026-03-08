from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.base_tap import BaseTap
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry


@dataclass
class ProduceEvent:
    dest_context_id: str | None
    home_value: Any
    dest_value: Any


class ParamEchoTap(BaseTap):
    def __init__(
        self,
        *,
        output: Grip[int],
        destination_param: Grip[int] | None = None,
        home_param: Grip[int] | None = None,
    ):
        destination_param_grips = (destination_param,) if destination_param is not None else ()
        home_param_grips = (home_param,) if home_param is not None else ()
        super().__init__(
            provides=(output,),
            destination_param_grips=destination_param_grips,
            home_param_grips=home_param_grips,
        )
        self.output = output
        self.destination_param = destination_param
        self.home_param = home_param
        self.events: list[ProduceEvent] = []

    def produce(self, *, dest_context=None) -> None:
        home_value = self.get_home_param_value(self.home_param) if self.home_param else None
        dest_value = None
        if self.destination_param is not None and dest_context is not None:
            dest_value = self.get_destination_param_value(dest_context, self.destination_param)

        event = ProduceEvent(
            dest_context_id=dest_context.id if dest_context is not None else None,
            home_value=home_value,
            dest_value=dest_value,
        )
        self.events.append(event)

        total = int(home_value or 0) + int(dest_value or 0)
        self.publish({self.output: total}, dest_context=dest_context)


def test_destination_param_change_recomputes_only_affected_destination():
    registry = GripRegistry()
    out = registry.add("Out", 0)
    dest_param = registry.add("DestParam", 0)
    grok = Grok(registry)

    c1 = grok.main_presentation_context.create_child()
    c2 = grok.main_presentation_context.create_child()

    c1_source = create_atom_value_tap(dest_param, initial=10)
    c2_source = create_atom_value_tap(dest_param, initial=20)
    c1.register_tap(c1_source)
    c2.register_tap(c2_source)

    tap = ParamEchoTap(output=out, destination_param=dest_param)
    grok.main_home_context.register_tap(tap)

    d1 = grok.query(out, c1)
    d2 = grok.query(out, c2)
    assert d1.get() == 10
    assert d2.get() == 20

    tap.events.clear()
    c1_source.set(33)

    assert d1.get() == 33
    assert d2.get() == 20
    assert tap.events
    assert all(event.dest_context_id == c1.id for event in tap.events)


def test_home_param_change_recomputes_all_destinations():
    registry = GripRegistry()
    out = registry.add("Out", 0)
    home_param = registry.add("HomeParam", 0)
    grok = Grok(registry)

    home_source = create_atom_value_tap(home_param, initial=7)
    grok.main_home_context.register_tap(home_source)

    tap = ParamEchoTap(output=out, home_param=home_param)
    grok.main_home_context.register_tap(tap)

    c1 = grok.main_presentation_context.create_child()
    c2 = grok.main_presentation_context.create_child()
    d1 = grok.query(out, c1)
    d2 = grok.query(out, c2)
    assert d1.get() == 7
    assert d2.get() == 7

    tap.events.clear()
    home_source.set(12)

    assert d1.get() == 12
    assert d2.get() == 12
    assert tap.events
    assert {event.dest_context_id for event in tap.events} == {None}
