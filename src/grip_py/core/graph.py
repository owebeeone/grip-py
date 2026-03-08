"""Graph and node internals for Grok."""

from __future__ import annotations

import time
import weakref
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .drip import Drip
from .grip import Grip
from .interfaces import Grok, GripContext, Tap, TapDestinationContext, TapFactory
from .task_queue import TaskHandleHolder


def _is_tap_factory(candidate: object) -> bool:
    """Return True when an object behaves like a tap factory."""
    build_fn = getattr(candidate, "build", None)
    produce_fn = getattr(candidate, "produce", None)
    return callable(build_fn) and not callable(produce_fn)


@dataclass(slots=True)
class ParentRef:
    """Internal parent-edge record with priority ordering."""

    node: GripContextNode
    priority: int


@dataclass(slots=True, eq=False)
class Destination:
    """Tap destination state for a single consumer context node."""

    _dest_context_node: GripContextNode
    _tap: Tap
    _producer: ProducerRecord
    _grips: set[Grip[Any]] = field(default_factory=set, init=False)
    _cleaned: bool = field(default=False, init=False)
    _tap_context: TapDestinationContext | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._tap_context = self._tap.create_destination_context(self)

    def get_grips(self) -> set[Grip[Any]]:
        return self._grips

    def get_tap_context(self) -> TapDestinationContext | None:
        return self._tap_context

    def get_dest_context(self) -> GripContext | None:
        return self._dest_context_node.get_context()

    def add_grip(self, grip: Grip[Any]) -> None:
        if grip in self._grips:
            return
        self._grips.add(grip)
        if self._tap_context is not None:
            cb = getattr(self._tap_context, "drip_added", None)
            if cb is not None:
                cb(grip)

    def remove_grip(self, grip: Grip[Any]) -> None:
        if grip not in self._grips:
            return
        self._grips.remove(grip)
        if self._tap_context is not None:
            cb = getattr(self._tap_context, "drip_removed", None)
            if cb is not None:
                cb(grip)

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        if self._tap_context is not None:
            cb = getattr(self._tap_context, "on_detach", None)
            if cb is not None:
                cb()


@dataclass(slots=True, init=False, eq=False)
class ProducerRecord:
    """Tap -> destinations mapping."""

    tap_factory: TapFactory | None
    tap: Tap
    outputs: set[Grip[Any]]
    _destinations: dict[GripContextNode, Destination]

    def __init__(self, tap_or_factory: Tap | TapFactory, outputs: Iterable[Grip[Any]] | None = None):
        if _is_tap_factory(tap_or_factory):
            self.tap_factory: TapFactory | None = tap_or_factory  # type: ignore[assignment]
            self.tap: Tap = tap_or_factory.build()  # type: ignore[union-attr]
        else:
            self.tap_factory = None
            self.tap = tap_or_factory  # type: ignore[assignment]
        self.outputs: set[Grip[Any]] = set(outputs or ())
        self._destinations: dict[GripContextNode, Destination] = {}

    def get_destinations(self) -> dict[GripContextNode, Destination]:
        return self._destinations

    def add_destination_grip(self, dest_node: GripContextNode, grip: Grip[Any]) -> None:
        destination = self._destinations.get(dest_node)
        if destination is None:
            destination = Destination(dest_node, self.tap, self)
            self._destinations[dest_node] = destination
            destination.add_grip(grip)
            dest_ctx = dest_node.get_context()
            if dest_ctx is not None:
                self.tap.on_connect(dest_ctx, grip)
            return

        destination.add_grip(grip)
        dest_ctx = dest_node.get_context()
        if dest_ctx is not None:
            self.tap.produce(dest_context=dest_ctx)

    def remove_destination_for_context(self, dest_node: GripContextNode) -> None:
        destination = self._destinations.pop(dest_node, None)
        if destination is None:
            return
        destination.cleanup()

    def remove_destination_grip_for_context(self, dest_node: GripContextNode, grip: Grip[Any]) -> None:
        destination = self._destinations.get(dest_node)
        if destination is None:
            return
        dest_ctx = dest_node.get_context()
        if dest_ctx is not None:
            self.tap.on_disconnect(dest_ctx, grip)
        destination.remove_grip(grip)
        if not destination.get_grips():
            self.remove_destination_for_context(dest_node)

    def detach(self) -> None:
        """Detach all destinations and release the tap."""
        for destination in tuple(self._destinations.values()):
            destination.cleanup()
        self._destinations.clear()
        self.tap.on_detach()

    def publish(
        self,
        values: dict[Grip[Any], Any],
        updater: Callable[[GripContext, Grip[Any], Any], None],
    ) -> int:
        count = 0
        dead_nodes: list[GripContextNode] = []
        for node, destination in tuple(self._destinations.items()):
            dest_ctx = node.get_context()
            if dest_ctx is None:
                dead_nodes.append(node)
                continue
            for grip in tuple(destination.get_grips()):
                if grip in self.outputs and grip in values:
                    updater(dest_ctx, grip, values[grip])
                    count += 1
        for node in dead_nodes:
            self.remove_destination_for_context(node)
        return count

    def publish_to_destination(
        self,
        dest_context: GripContext,
        values: dict[Grip[Any], Any],
        updater: Callable[[GripContext, Grip[Any], Any], None],
    ) -> int:
        for node, destination in self._destinations.items():
            if node.get_context() is dest_context:
                count = 0
                for grip in tuple(destination.get_grips()):
                    if grip in self.outputs and grip in values:
                        updater(dest_context, grip, values[grip])
                        count += 1
                return count
        return 0


@dataclass(init=False, eq=False)
class GripContextNode:
    """Internal node for a GripContext."""

    grok: Grok
    id: str
    context_ref: weakref.ReferenceType[GripContext]
    parents: list[ParentRef]
    children: list[GripContextNode]
    handle_holder: TaskHandleHolder
    producers: dict[Grip[Any], ProducerRecord]
    producer_stacks: dict[Grip[Any], list[ProducerRecord]]
    producer_by_tap: dict[object, ProducerRecord]
    consumers: dict[Grip[Any], Drip[Any]]
    resolved_providers: dict[Grip[Any], GripContextNode]
    last_seen: float

    def __init__(self, grok: Grok, ctx: GripContext):
        self.grok = grok
        self.id = ctx.id
        self.context_ref: weakref.ReferenceType[GripContext] = weakref.ref(ctx)
        self.parents: list[ParentRef] = []
        self.children: list[GripContextNode] = []
        self.handle_holder = TaskHandleHolder()

        self.producers: dict[Grip[Any], ProducerRecord] = {}
        self.producer_stacks: dict[Grip[Any], list[ProducerRecord]] = {}
        self.producer_by_tap: dict[object, ProducerRecord] = {}
        self.consumers: dict[Grip[Any], Drip[Any]] = {}
        self.resolved_providers: dict[Grip[Any], GripContextNode] = {}
        self.last_seen = time.time()

    def touch(self) -> None:
        self.last_seen = time.time()

    def get_last_seen(self) -> float:
        return self.last_seen

    def submit_task(self, callback: Callable[[], None], priority: int) -> None:
        self.grok.submit_task(callback, priority, self.handle_holder)

    def submit_weak_task(self, callback: Callable[[], None]) -> None:
        # Simplified policy: strongly schedule callback for deterministic delivery.
        self.grok.submit_weak_task(callback, self.handle_holder)

    def get_context(self) -> GripContext | None:
        return self.context_ref()

    def get_parent_nodes(self) -> list[GripContextNode]:
        return [p.node for p in self.parents]

    def get_parents_with_priority(self) -> tuple[ParentRef, ...]:
        return tuple(self.parents)

    def get_children_nodes(self) -> list[GripContextNode]:
        return self.children

    def is_root(self) -> bool:
        return len(self.parents) == 0

    def add_parent(self, parent: GripContextNode, priority: int = 0) -> None:
        existing = next((p for p in self.parents if p.node is parent), None)
        if existing is not None:
            existing.priority = priority
            self.parents.sort(key=lambda p: p.priority)
            return
        self.parents.append(ParentRef(parent, priority))
        self.parents.sort(key=lambda p: p.priority)
        if self not in parent.children:
            parent.children.append(self)

    def remove_parent(self, parent: GripContextNode) -> None:
        idx = next((i for i, p in enumerate(self.parents) if p.node is parent), -1)
        if idx < 0:
            raise ValueError(f"Parent {parent.id} is not a parent of {self.id}")
        self.parents.pop(idx)
        try:
            parent.children.remove(self)
        except ValueError:
            pass

    def get_producers(self) -> dict[Grip[Any], ProducerRecord]:
        return self.producers

    def get_consumers(self) -> dict[Grip[Any], Drip[Any]]:
        return self.consumers

    def get_resolved_providers(self) -> dict[Grip[Any], GripContextNode]:
        return self.resolved_providers

    def set_resolved_provider(self, grip: Grip[Any], node: GripContextNode) -> None:
        self.resolved_providers[grip] = node

    def record_producer(self, grip: Grip[Any], rec: ProducerRecord) -> None:
        stack = self.producer_stacks.get(grip)
        if stack is None:
            stack = []
            self.producer_stacks[grip] = stack
        if rec in stack:
            stack.remove(rec)
        stack.append(rec)
        self.producers[grip] = rec
        self.touch()

    def get_or_create_producer_record(
        self,
        tap_or_factory: Tap | TapFactory,
        outputs: Iterable[Grip[Any]] | None = None,
    ) -> ProducerRecord:
        rec = self.producer_by_tap.get(tap_or_factory)
        if rec is not None:
            return rec

        rec = ProducerRecord(tap_or_factory, outputs)
        self.producer_by_tap[tap_or_factory] = rec
        self.producer_by_tap[rec.tap] = rec
        return rec

    def get_producer_record(self, tap_or_factory: Tap | TapFactory) -> ProducerRecord | None:
        return self.producer_by_tap.get(tap_or_factory)

    def _remove_tap(self, tap: Tap | TapFactory) -> list[Grip[Any]]:
        rec = self.producer_by_tap.get(tap)
        if rec is None:
            return []

        affected: list[Grip[Any]] = []
        for grip, stack in tuple(self.producer_stacks.items()):
            if rec not in stack:
                continue
            stack = [producer for producer in stack if producer is not rec]
            affected.append(grip)
            if stack:
                self.producer_stacks[grip] = stack
                self.producers[grip] = stack[-1]
            else:
                self.producer_stacks.pop(grip, None)
                self.producers.pop(grip, None)

        self.producer_by_tap.pop(tap, None)
        self.producer_by_tap.pop(rec.tap, None)
        if rec.tap_factory is not None:
            self.producer_by_tap.pop(rec.tap_factory, None)
        rec.detach()
        return affected

    def record_consumer(self, grip: Grip[Any], drip: Drip[Any]) -> None:
        self.consumers[grip] = drip
        self.touch()

        def on_first() -> None:
            ctx = self.get_context()
            if ctx is not None:
                self.grok.resolver.add_consumer(ctx, grip)

        drip.add_on_first_subscriber(on_first)

    def get_or_create_consumer(self, grip: Grip[Any]) -> Drip[Any]:
        live = self.get_live_drip_for_grip(grip)
        if live is not None:
            return live

        drip = Drip(initial=grip.default)
        self.record_consumer(grip, drip)

        def on_zero() -> None:
            self.remove_consumer_for_grip(grip)

        drip.add_on_zero_subscribers(on_zero)
        return drip

    def get_live_drip_for_grip(self, grip: Grip[Any]) -> Drip[Any] | None:
        return self.consumers.get(grip)

    def notify_consumers(self, grip: Grip[Any], value: Any) -> int:
        drip = self.get_live_drip_for_grip(grip)
        if drip is None:
            return 0
        drip.next(value)
        return 1

    def unregister_source(self, grip: Grip[Any]) -> None:
        provider = self.resolved_providers.get(grip)
        if provider is not None:
            provider.remove_destination_for_context(grip, self)

    def remove_destination_for_context(self, grip: Grip[Any], dest: GripContextNode) -> None:
        producer = self.producers.get(grip)
        if producer is not None:
            producer.remove_destination_grip_for_context(dest, grip)

    def remove_consumer_for_grip(self, grip: Grip[Any]) -> None:
        self.consumers.pop(grip, None)
        self.unregister_source(grip)
        self.resolved_providers.pop(grip, None)

    def purge_dangling_drips(self) -> int:
        return len(self.consumers)


@dataclass(slots=True, init=False, eq=False)
class GrokGraph:
    """Graph container for context nodes."""

    _grok: Grok
    _nodes: dict[str, GripContextNode]
    _weak_nodes: dict[str, weakref.ReferenceType[GripContextNode]]

    def __init__(self, grok: Grok):
        self._grok = grok
        self._nodes: dict[str, GripContextNode] = {}
        self._weak_nodes: dict[str, weakref.ReferenceType[GripContextNode]] = {}

    def ensure_node(self, ctx: GripContext) -> GripContextNode:
        node = self._nodes.get(ctx.id)
        if node is None:
            node = GripContextNode(self._grok, ctx)
            self._nodes[ctx.id] = node
            self._weak_nodes[ctx.id] = weakref.ref(node)
            for parent in ctx.get_parents():
                parent_node = self.ensure_node(parent.ctx)
                node.add_parent(parent_node, parent.priority)
        node.touch()
        return node

    def get_node(self, ctx: GripContext) -> GripContextNode | None:
        return self._nodes.get(ctx.id)

    def get_node_by_id(self, node_id: str) -> GripContextNode | None:
        return self._nodes.get(node_id)

    def snapshot(self) -> dict[str, GripContextNode]:
        return dict(self._nodes)

    def has_cycle(self, new_node: GripContextNode) -> bool:
        todo = set(new_node.get_children_nodes())
        seen: set[GripContextNode] = set()
        stack: list[GripContextNode] = []

        def visit(node: GripContextNode) -> bool:
            todo.discard(node)
            if node in seen:
                return False
            seen.add(node)
            stack.append(node)
            for parent in node.get_parent_nodes():
                if parent in stack:
                    return True
                if visit(parent):
                    return True
            stack.pop()
            return False

        while todo:
            node = next(iter(todo))
            if visit(node):
                return True
        return False

    def notify_consumers(self, dest_ctx: GripContext, grip: Grip[Any], value: Any) -> int:
        node = self.get_node(dest_ctx)
        if node is None:
            return 0
        return node.notify_consumers(grip, value)

    def snapshot_sanity_check(
        self,
    ) -> tuple[dict[str, GripContextNode], set[GripContextNode], set[GripContextNode]]:
        all_nodes: dict[str, GripContextNode] = {}
        missing_nodes: set[GripContextNode] = set()

        to_visit = list(self._nodes.values())
        while to_visit:
            node = to_visit.pop()
            if node.id in all_nodes:
                continue
            all_nodes[node.id] = node

            valid_children: list[GripContextNode] = []
            for child in node.children:
                if child.id not in self._nodes and child.id not in all_nodes:
                    missing_nodes.add(child)
                else:
                    valid_children.append(child)
                    if child.id not in all_nodes:
                        to_visit.append(child)
            if len(valid_children) != len(node.children):
                node.children[:] = valid_children

        nodes_not_reaped: set[GripContextNode] = set()
        stale_keys: list[str] = []
        for node_id, ref in self._weak_nodes.items():
            node = ref()
            if node is None:
                stale_keys.append(node_id)
            elif node.id not in all_nodes:
                nodes_not_reaped.add(node)
        for key in stale_keys:
            self._weak_nodes.pop(key, None)

        return all_nodes, missing_nodes, nodes_not_reaped

    def gc_sweep(self) -> None:
        nodes_to_delete: list[str] = []
        for node_id, node in self._nodes.items():
            context_gone = node.get_context() is None
            no_consumers = node.purge_dangling_drips() == 0
            if context_gone and no_consumers and len(node.children) == 0:
                nodes_to_delete.append(node_id)

        for node_id in nodes_to_delete:
            node = self._nodes.get(node_id)
            if node is None:
                continue
            self.clear_context_node(node)
            self._nodes.pop(node_id, None)

        for node in self._nodes.values():
            valid_children = [child for child in node.children if child.id in self._nodes]
            if len(valid_children) != len(node.children):
                node.children[:] = valid_children

    def clear_context_node(self, node: GripContextNode) -> None:
        for drip in tuple(node.consumers.values()):
            drip.unsubscribe_all()
        node.consumers.clear()

        for parent_ref in tuple(node.parents):
            try:
                node.remove_parent(parent_ref.node)
            except ValueError:
                pass

        for child in tuple(node.children):
            try:
                child.remove_parent(node)
            except ValueError:
                try:
                    node.children.remove(child)
                except ValueError:
                    pass

        node.parents.clear()
        node.children.clear()
