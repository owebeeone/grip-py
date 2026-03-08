"""Grip context model."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .drip import Drip
from .grip import Grip
from .interfaces import Grok, GripContextLike as GripContextLikeProto, GripContextNode, Tap, TapFactory


@dataclass(frozen=True, slots=True)
class ParentContext:
    """Parent context reference with edge priority."""

    ctx: GripContext
    priority: int


GripContextLike = GripContextLikeProto


@dataclass(init=False, eq=False)
class GripContext:
    """Context node in the Grok DAG."""

    kind = "GripContext"
    _grok: Grok
    id: str
    _context_node: GripContextNode

    def __init__(self, engine: Grok, context_id: str | None = None):
        self._grok = engine
        self.id = context_id or f"ctx_{random.randint(1, 2_000_000_000):x}"
        self._context_node = engine.ensure_node(self)

    def get_grip_consumer_context(self) -> GripContext:
        """Return this context as the consumer-side context."""
        return self

    def get_grip_home_context(self) -> GripContext:
        """Return this context as the producer/home context."""
        return self

    def get_grok(self) -> Grok:
        """Return the owning Grok runtime."""
        return self._grok

    def get_node(self) -> GripContextNode:
        """Return the internal node backing this context."""
        return self._context_node

    def is_root(self) -> bool:
        """Return ``True`` when this context has no parents."""
        return len(self._context_node.get_parent_nodes()) == 0

    def submit_task(self, callback, priority: int = 0) -> None:
        """Submit a task to this context's scheduler queue."""
        self._context_node.submit_task(callback, priority)

    def submit_weak_task(self, callback) -> None:
        """Submit a weak task (ownership checked by runtime policy)."""
        self._context_node.submit_weak_task(callback)

    def get_parents(self) -> tuple[ParentContext, ...]:
        """Return current parent contexts with priorities."""
        if not hasattr(self, "_context_node"):
            return ()
        parents = []
        for ref in self._context_node.get_parents_with_priority():
            parent_ctx = ref.node.get_context()
            if parent_ctx is not None:
                parents.append(ParentContext(parent_ctx, ref.priority))
        return tuple(parents)

    def add_parent(self, parent_context: GripContextLike, priority: int = 0) -> GripContext:
        """Attach a parent context edge and re-resolve providers."""
        parent = parent_context.get_grip_home_context()
        if parent.get_grok() is not self._grok:
            raise ValueError("Contexts must belong to the same Grok")
        if parent is self:
            raise ValueError("Context cannot be its own parent")

        self._context_node.add_parent(parent._context_node, priority)
        if self._grok.has_cycle(self._context_node):
            self._context_node.remove_parent(parent._context_node)
            raise ValueError("Cycle detected in context DAG")

        self._grok.resolver.add_parent(self, parent)
        return self

    def unlink_parent(self, parent_context: GripContext) -> GripContext:
        """Remove a parent edge and re-resolve providers."""
        try:
            self._context_node.remove_parent(parent_context._context_node)
        except ValueError:
            return self
        self._grok.resolver.unlink_parent(self, parent_context)
        return self

    def create_child(self, *, priority: int = 0) -> GripContext:
        """Create a new child context linked to this context."""
        child = GripContext(self._grok)
        child.add_parent(self, priority)
        return child

    def get_live_drip_for_grip(self, grip: Grip):
        """Return a live consumer drip for ``grip`` when present."""
        return self._context_node.get_live_drip_for_grip(grip)

    def get_or_create_consumer(self, grip: Grip) -> Drip:
        """Return an existing consumer drip or create one for ``grip``."""
        return self._context_node.get_or_create_consumer(grip)

    def register_tap(self, tap: Tap | TapFactory) -> None:
        """Register a tap/factory on this context as its home context."""
        home_ctx = self.get_grip_home_context()
        self._grok.resolver.add_producer(home_ctx, tap)

    def unregister_tap(self, tap: Tap) -> None:
        """Unregister a tap from the runtime."""
        self._grok.unregister_tap(tap)

    def unregister_source(self, grip: Grip) -> None:
        """Disconnect producer routing for a specific grip in this context."""
        self._context_node.unregister_source(grip)

    def _get_context_node(self) -> GripContextNode:
        return self._context_node

    def get_context_node(self) -> GripContextNode:
        """Return the internal context node (public alias)."""
        return self._context_node
