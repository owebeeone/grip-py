"""Grok orchestration kernel implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .async_loop import get_shared_async_loop
from .context import GripContext, GripContextLike
from .drip import Drip
from .grip import Grip, GripRegistry
from .graph import GripContextNode, GrokGraph
from .interfaces import Resolver, Tap
from .local_persistence import GrokLocalPersistence, LocalPersistenceAttachOptions
from .query_evaluator import EvaluationDelta
from .task_queue import TaskHandleContainer, TaskQueue
from .tap_resolver import SimpleResolver


@dataclass(frozen=True, slots=True)
class GraphSanity:
    """Sanity snapshot used to inspect graph integrity and GC state."""

    nodes: dict[str, GripContextNode]
    missing_nodes: set[GripContextNode]
    nodes_not_reaped: set[GripContextNode]


@dataclass(slots=True, init=False, eq=False)
class GrokImpl:
    """Central orchestrator for context graph and taps."""

    _registry: GripRegistry
    _graph: GrokGraph
    _async_loop: asyncio.AbstractEventLoop
    _task_queue: TaskQueue
    _closed: bool
    _origin_mutation_seq: int
    _local_persistence: GrokLocalPersistence | None
    _local_persistence_suppressed: bool
    resolver: Resolver
    root_context: GripContext
    main_home_context: GripContext
    main_presentation_context: GripContext

    def __init__(self, registry: GripRegistry):
        self._registry = registry
        self._async_loop = get_shared_async_loop()
        self._graph = GrokGraph(self)
        self._task_queue = TaskQueue(auto_flush=True, loop=self._async_loop)
        self._closed = False
        self._origin_mutation_seq = 0
        self._local_persistence = None
        self._local_persistence_suppressed = False
        self.resolver = SimpleResolver(self)

        self.root_context = GripContext(self, "root")
        self.main_home_context = GripContext(self, "main-home")
        self.main_home_context._get_context_node().add_parent(self.root_context._get_context_node(), 0)

        self.main_presentation_context = GripContext(self, "main-presentation")
        self.main_presentation_context._get_context_node().add_parent(
            self.main_home_context._get_context_node(), 0
        )

    def get_registry(self) -> GripRegistry:
        """Return the registry bound to this Grok runtime."""
        return self._registry

    def allocate_origin_mutation_seq(self) -> int:
        """Allocate and return the next local origin mutation sequence."""
        self._origin_mutation_seq += 1
        return self._origin_mutation_seq

    def get_last_origin_mutation_seq(self) -> int:
        """Return the last allocated local origin mutation sequence."""
        return self._origin_mutation_seq

    def run_with_local_persistence_suppressed(self, callback):
        """Run a callback while suppressing local persistence dirty marks."""
        previous = self._local_persistence_suppressed
        self._local_persistence_suppressed = True
        try:
            return callback()
        finally:
            self._local_persistence_suppressed = previous

    def note_local_persistence_dirty(self) -> None:
        """Mark the attached local persistence session dirty when enabled."""
        if self._local_persistence_suppressed:
            return
        if self._local_persistence is not None:
            self._local_persistence.mark_dirty()

    def attach_local_persistence(
        self,
        *,
        session_id: str,
        store,
        title: str | None = None,
        flush_delay_ms: int = 250,
    ) -> None:
        """Attach a local persistence store and hydrate the current runtime."""
        if self._local_persistence is not None:
            self._local_persistence.detach()
        local_persistence = GrokLocalPersistence(
            self,
            LocalPersistenceAttachOptions(
                session_id=session_id,
                title=title,
                store=store,
                flush_delay_ms=flush_delay_ms,
            ),
        )
        self._local_persistence = local_persistence
        local_persistence.attach()

    def detach_local_persistence(self) -> None:
        """Detach the active local persistence store."""
        if self._local_persistence is not None:
            self._local_persistence.detach()
            self._local_persistence = None

    def flush_local_persistence(self) -> None:
        """Flush the current local persistence snapshot immediately."""
        if self._local_persistence is not None:
            self._local_persistence.flush_now()

    def has_cycle(self, new_node: GripContextNode) -> bool:
        """Return ``True`` if the graph currently contains a cycle."""
        return self._graph.has_cycle(new_node)

    def submit_task(
        self,
        callback,
        priority: int,
        holder: TaskHandleContainer | None = None,
    ) -> None:
        """Submit a queued callback to the Grok task queue."""
        self._task_queue.submit(callback, priority=priority, holder=holder)

    def submit_weak_task(
        self,
        callback,
        holder: TaskHandleContainer | None = None,
    ) -> None:
        """Submit a weak task callback under the current weak-task policy."""
        # Simplified policy: weak ownership checks should live in callbacks.
        self._task_queue.submit(callback, priority=0, holder=holder)

    def get_task_queue(self) -> TaskQueue:
        """Return the internal task queue."""
        return self._task_queue

    def get_async_loop(self) -> asyncio.AbstractEventLoop:
        """Return the internal asyncio loop used for runtime subscriptions."""
        return self._async_loop

    def flush(self) -> None:
        """Synchronously flush queued tasks."""
        self._task_queue.flush()

    def ensure_node(self, ctx: GripContext) -> GripContextNode:
        """Return existing or create new internal node for ``ctx``."""
        return self._graph.ensure_node(ctx)

    def create_context(
        self,
        parent: GripContext | None = None,
        priority: int = 0,
        *,
        context_id: str,
    ) -> GripContext:
        """Create a context optionally parented to an existing context."""
        ctx = GripContext(self, context_id)
        if parent is not None:
            ctx.add_parent(parent, priority)
        return ctx

    def get_context_by_id(self, context_id: str) -> GripContext | None:
        """Return a live context by deterministic id when present."""
        node = self._graph.get_node_by_id(context_id)
        return node.get_context() if node is not None else None

    def unregister_tap(self, tap: Tap) -> None:
        """Detach a registered tap from the resolver."""
        home_ctx = tap.get_home_context()
        if home_ctx is not None:
            self.resolver.remove_producer(home_ctx, tap)

    def query(self, grip: Grip[Any], consumer_ctx: GripContextLike | None = None) -> Drip[Any]:
        """Resolve and return the consumer drip for ``grip`` in ``consumer_ctx``."""
        ctx_like = consumer_ctx or self.main_presentation_context
        ctx = ctx_like.get_grip_consumer_context()
        ctx_node = ctx._get_context_node()

        drip = ctx_node.get_live_drip_for_grip(grip)
        if drip is not None:
            if grip not in ctx_node.get_resolved_providers():
                self.resolver.add_consumer(ctx, grip)
            return drip

        drip = ctx_node.get_or_create_consumer(grip)
        self.resolver.add_consumer(ctx, grip)
        return drip

    def apply_producer_delta(
        self,
        context: GripContext,
        delta: dict[str, Any] | EvaluationDelta,
    ) -> None:
        """Apply matcher/evaluator attribution delta for a context."""
        self.resolver.apply_producer_delta(context, delta)

    def notify_consumers(self, dest_ctx: GripContext, grip: Grip[Any], value: Any) -> int:
        """Push a value to consumers and return notified consumer count."""
        return self._graph.notify_consumers(dest_ctx, grip, value)

    def get_graph(self) -> dict[str, GripContextNode]:
        """Return a snapshot of internal context nodes keyed by id."""
        return self._graph.snapshot()

    def get_graph_sanity_check(self) -> GraphSanity:
        """Return graph sanity details including potentially stale nodes."""
        nodes, missing_nodes, nodes_not_reaped = self._graph.snapshot_sanity_check()
        return GraphSanity(nodes, missing_nodes, nodes_not_reaped)

    def gc_sweep(self) -> None:
        """Run a graph GC sweep to drop unreachable nodes/drips."""
        self._graph.gc_sweep()

    def close(self) -> None:
        """Detach taps/subscriptions and release runtime-owned resources."""
        if self._closed:
            return
        self._closed = True
        if self._local_persistence is not None:
            self._local_persistence.flush_now()
            self._local_persistence.detach()
            self._local_persistence = None

        for node in tuple(self._graph.snapshot().values()):
            for record in set(node.producer_by_tap.values()):
                record.detach()
            node.producer_by_tap.clear()
            node.producer_stacks.clear()
            node.producers.clear()

            for drip in tuple(node.consumers.values()):
                drip.unsubscribe_all()
            node.consumers.clear()
            node.resolved_providers.clear()

    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        try:
            self.close()
        except Exception:
            pass
