"""Protocol interfaces used to decouple core modules."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol, runtime_checkable

from .drip import Drip
from .grip import Grip
from .task_queue import TaskHandleContainer


@runtime_checkable
class ParentRef(Protocol):
    """Parent reference used by context nodes."""

    node: GripContextNode
    priority: int


@runtime_checkable
class Destination(Protocol):
    """Tap destination abstraction."""

    def get_grips(self) -> set[Grip[Any]]: ...

    def get_dest_context(self) -> GripContext | None: ...


@runtime_checkable
class TapDestinationContext(Protocol):
    """Optional per-destination lifecycle callbacks."""

    def drip_added(self, grip: Grip[Any]) -> None: ...

    def drip_removed(self, grip: Grip[Any]) -> None: ...

    def on_detach(self) -> None: ...


@runtime_checkable
class Tap(Protocol):
    """Producer interface for graph outputs."""

    provides: tuple[Grip[Any], ...]

    def on_attach(self, home_context: GripContext) -> None: ...

    def on_detach(self) -> None: ...

    def on_connect(self, dest_context: GripContext, grip: Grip[Any]) -> None: ...

    def produce(self, *, dest_context: GripContext | None = None) -> None: ...

    def get_home_context(self) -> GripContext | None: ...

    def get_provides(self) -> Iterable[Grip[Any]]: ...

    def create_destination_context(
        self,
        destination: Destination,
    ) -> TapDestinationContext | None: ...


@runtime_checkable
class TapFactory(Protocol):
    """Factory for creating taps."""

    def build(self) -> Tap: ...


@runtime_checkable
class ProducerRecord(Protocol):
    """Producer record abstraction."""

    tap: Tap
    outputs: set[Grip[Any]]

    def get_destinations(self) -> dict[GripContextNode, Destination]: ...

    def add_destination_grip(self, dest_node: GripContextNode, grip: Grip[Any]) -> None: ...

    def remove_destination_for_context(self, dest_node: GripContextNode) -> None: ...

    def remove_destination_grip_for_context(self, dest_node: GripContextNode, grip: Grip[Any]) -> None: ...

    def publish(
        self,
        values: dict[Grip[Any], Any],
        updater: Callable[[GripContext, Grip[Any], Any], None],
    ) -> int: ...

    def publish_to_destination(
        self,
        dest_context: GripContext,
        values: dict[Grip[Any], Any],
        updater: Callable[[GripContext, Grip[Any], Any], None],
    ) -> int: ...


@runtime_checkable
class GripContextLike(Protocol):
    """Context-like shape used by registration/query APIs."""

    def get_grip_consumer_context(self) -> GripContext: ...

    def get_grip_home_context(self) -> GripContext: ...

    def get_grok(self) -> Grok: ...


@runtime_checkable
class GripContext(GripContextLike, Protocol):
    """Context protocol used across modules."""

    id: str

    def get_context_node(self) -> GripContextNode: ...

    def _get_context_node(self) -> GripContextNode: ...


@runtime_checkable
class GripContextNode(Protocol):
    """Internal context-node protocol."""

    id: str
    parents: list[ParentRef]
    children: list[GripContextNode]
    consumers: dict[Grip[Any], Drip[Any]]

    def get_context(self) -> GripContext | None: ...

    def submit_task(self, callback: Callable[[], None], priority: int) -> None: ...

    def submit_weak_task(self, callback: Callable[[], None]) -> None: ...

    def get_parent_nodes(self) -> list[GripContextNode]: ...

    def get_parents_with_priority(self) -> tuple[ParentRef, ...]: ...

    def get_children_nodes(self) -> list[GripContextNode]: ...

    def is_root(self) -> bool: ...

    def add_parent(self, parent: GripContextNode, priority: int = 0) -> None: ...

    def remove_parent(self, parent: GripContextNode) -> None: ...

    def get_producers(self) -> dict[Grip[Any], ProducerRecord]: ...

    def get_consumers(self) -> dict[Grip[Any], Drip[Any]]: ...

    def get_resolved_providers(self) -> dict[Grip[Any], GripContextNode]: ...

    def set_resolved_provider(self, grip: Grip[Any], node: GripContextNode) -> None: ...

    def record_producer(self, grip: Grip[Any], rec: ProducerRecord) -> None: ...

    def get_or_create_producer_record(
        self,
        tap_or_factory: Tap | TapFactory,
        outputs: Iterable[Grip[Any]] | None = None,
    ) -> ProducerRecord: ...

    def _remove_tap(self, tap: Tap | TapFactory) -> list[Grip[Any]]: ...

    def get_or_create_consumer(self, grip: Grip[Any]) -> Drip[Any]: ...

    def get_live_drip_for_grip(self, grip: Grip[Any]) -> Drip[Any] | None: ...

    def notify_consumers(self, grip: Grip[Any], value: Any) -> int: ...

    def unregister_source(self, grip: Grip[Any]) -> None: ...

    def remove_destination_for_context(self, grip: Grip[Any], dest: GripContextNode) -> None: ...

    def remove_consumer_for_grip(self, grip: Grip[Any]) -> None: ...

    def purge_dangling_drips(self) -> int: ...


@runtime_checkable
class Resolver(Protocol):
    """Resolution interface."""

    def add_producer(self, context: GripContext, tap: Tap | TapFactory) -> None: ...

    def remove_producer(self, context: GripContext, tap: Tap) -> None: ...

    def add_consumer(self, context: GripContext, grip: Grip[Any]) -> None: ...

    def remove_consumer(self, context: GripContext, grip: Grip[Any]) -> None: ...

    def add_parent(self, child_context: GripContext, parent_context: GripContext) -> None: ...

    def unlink_parent(self, child_context: GripContext, parent_context: GripContext) -> None: ...

    def apply_producer_delta(self, context: GripContext, delta: dict[str, Any]) -> None: ...


@runtime_checkable
class Grok(Protocol):
    """Runtime interface implemented by GrokImpl."""

    resolver: Resolver
    root_context: GripContext
    main_home_context: GripContext
    main_presentation_context: GripContext

    def has_cycle(self, new_node: GripContextNode) -> bool: ...

    def submit_task(
        self,
        callback: Callable[[], None],
        priority: int,
        holder: TaskHandleContainer | None = None,
    ) -> None: ...

    def submit_weak_task(
        self,
        callback: Callable[[], None],
        holder: TaskHandleContainer | None = None,
    ) -> None: ...

    def ensure_node(self, ctx: GripContext) -> GripContextNode: ...

    def register_tap_at(self, ctx: GripContextLike, tap: Tap | TapFactory) -> None: ...

    def unregister_tap(self, tap: Tap) -> None: ...

    def notify_consumers(self, dest_ctx: GripContext, grip: Grip[Any], value: Any) -> int: ...

    def get_graph(self) -> dict[str, GripContextNode]: ...
