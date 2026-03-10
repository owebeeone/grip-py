from glial_local.in_memory import InMemoryGripSessionStore

from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


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
