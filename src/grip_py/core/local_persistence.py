"""Runtime-owned local persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock, Timer
from typing import Any, Protocol, cast

from glial_local.types import ContextState, DripState, NewSessionRequest, SessionSnapshot, TapExport

from .context import GripContext
from .grip import Grip

if False:  # pragma: no cover
    from .grok_impl import GrokImpl
    from .interfaces import Tap


class PersistableTap(Protocol):
    """Duck-typed tap persistence hooks."""

    provides: tuple[Grip[Any], ...]

    def get_home_context(self) -> GripContext | None: ...

    def get_execution_mode(self) -> str: ...

    def get_execution_role(self) -> str: ...

    def get_persisted_grip_values(self) -> dict[Grip[Any], Any]: ...

    def restore_persisted_grip_value(self, grip: Grip[Any], value: Any) -> bool | None: ...


def _get_context_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _is_json_persistable(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, list):
        return all(_is_json_persistable(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_persistable(item) for key, item in value.items()
        )
    return False


def _build_tap_export(tap: PersistableTap) -> TapExport:
    return TapExport(
        tap_id=getattr(tap, "id", "tap"),
        tap_type=type(tap).__name__,
        mode=tap.get_execution_mode(),
        role=tap.get_execution_role(),
        provides=[grip.key for grip in tap.provides],
    )


def _ensure_snapshot_context(
    snapshot: SessionSnapshot,
    path: str,
    children: list[str] | None = None,
) -> ContextState:
    existing = snapshot.contexts.get(path)
    if existing is not None:
        if children is not None:
            existing.children = list(children)
        return existing
    context = ContextState(
        path=path,
        name=_get_context_name(path),
        children=list(children or ()),
        drips={},
    )
    snapshot.contexts[path] = context
    return context


def build_local_persistence_snapshot(grok: GrokImpl, session_id: str) -> SessionSnapshot:
    snapshot = SessionSnapshot(session_id=session_id, contexts={})
    seen_taps: set[Any] = set()

    for node in sorted(grok.get_graph().values(), key=lambda entry: entry.id):
        context = _ensure_snapshot_context(
            snapshot,
            node.id,
            sorted(child.id for child in node.get_children_nodes()),
        )
        for producer in node.producer_by_tap.values():
            tap = producer.tap
            if tap in seen_taps:
                continue
            seen_taps.add(tap)
            if not hasattr(tap, "get_persisted_grip_values"):
                continue
            home_context = tap.get_home_context()
            if home_context is None:
                continue
            persisted_context = _ensure_snapshot_context(snapshot, home_context.id)
            tap_export = _build_tap_export(cast(PersistableTap, tap))
            for grip, value in cast(PersistableTap, tap).get_persisted_grip_values().items():
                if not _is_json_persistable(value):
                    continue
                persisted_context.drips[grip.key] = DripState(
                    grip_id=grip.key,
                    name=grip.name,
                    value=value,
                    taps=[tap_export],
                )
        context.drips = dict(sorted(context.drips.items(), key=lambda entry: entry[0]))

    return snapshot


def _ensure_context_for_path(grok: GrokImpl, path: str) -> GripContext:
    existing = grok.get_context_by_id(path)
    if existing is not None:
        return existing
    if "/" not in path:
        return grok.create_context(context_id=path)
    parent_path, child_name = path.rsplit("/", 1)
    parent = _ensure_context_for_path(grok, parent_path)
    return parent.get_or_create_child(child_name)


def _find_persistable_tap_for_grip(
    grok: GrokImpl,
    path: str,
    grip: Grip[Any],
) -> PersistableTap | None:
    seen_taps: set[Any] = set()
    for node in grok.get_graph().values():
        for producer in node.producer_by_tap.values():
            tap = producer.tap
            if tap in seen_taps:
                continue
            seen_taps.add(tap)
            if not hasattr(tap, "restore_persisted_grip_value"):
                continue
            home_context = tap.get_home_context()
            if home_context is None or home_context.id != path:
                continue
            if grip in tap.provides:
                return cast(PersistableTap, tap)
    return None


def apply_local_persistence_snapshot(grok: GrokImpl, snapshot: SessionSnapshot) -> None:
    paths = sorted(snapshot.contexts.keys(), key=lambda path: (path.count("/"), path))
    for path in paths:
        _ensure_context_for_path(grok, path)

    for path in paths:
        context = _ensure_context_for_path(grok, path)
        context_state = snapshot.contexts[path]
        for drip_state in context_state.drips.values():
            grip = grok.get_registry().get_by_key(drip_state.grip_id)
            if grip is None:
                continue
            tap = _find_persistable_tap_for_grip(grok, path, grip)
            restored = tap.restore_persisted_grip_value(grip, drip_state.value) if tap else False
            if tap is not None and restored is not False:
                continue
            context.get_or_create_consumer(grip).next(drip_state.value)


@dataclass(slots=True)
class LocalPersistenceAttachOptions:
    session_id: str
    store: Any
    title: str | None = None
    flush_delay_ms: int = 250


@dataclass(slots=True)
class GrokLocalPersistence:
    """Owns one local persistence attachment for a Grok runtime."""

    grok: GrokImpl
    options: LocalPersistenceAttachOptions
    _dirty: bool = False
    _flush_timer: Timer | None = None
    _lock: Lock = Lock()

    def attach(self) -> None:
        existing = self.options.store.get_session(self.options.session_id)
        if existing is None:
            self.options.store.new_session(
                NewSessionRequest(
                    session_id=self.options.session_id,
                    title=self.options.title,
                    initial_snapshot=build_local_persistence_snapshot(
                        self.grok, self.options.session_id
                    ),
                )
            )
        hydrated = self.options.store.hydrate(self.options.session_id)
        self.grok.run_with_local_persistence_suppressed(
            lambda: apply_local_persistence_snapshot(self.grok, hydrated.snapshot)
        )

    def detach(self) -> None:
        with self._lock:
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None

    def mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            if self.options.flush_delay_ms <= 0:
                return
            timer = Timer(self.options.flush_delay_ms / 1000.0, self.flush_now)
            timer.daemon = True
            self._flush_timer = timer
            timer.start()

    def flush_now(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            self._dirty = False
        self.grok.flush()
        snapshot = build_local_persistence_snapshot(self.grok, self.options.session_id)
        self.options.store.replace_snapshot(self.options.session_id, snapshot, "collapse")
