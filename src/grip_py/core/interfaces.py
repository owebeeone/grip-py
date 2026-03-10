"""Protocol interfaces used to decouple core modules."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from .drip import Drip
from .grip import Grip
from .task_queue import TaskHandleContainer

if False:  # pragma: no cover
    from .query_evaluator import EvaluationDelta

TapExecutionMode = Literal["replicated", "origin-primary", "negotiated-primary"]
TapExecutionRole = Literal["primary", "follower"]


@dataclass(slots=True)
class SharedProjectionTapSpec:
    tap_id: str
    tap_type: str
    home_path: str
    mode: str
    role: str | None = None
    provides: list[str] = field(default_factory=list)
    home_param_grips: list[str] = field(default_factory=list)
    destination_param_grips: list[str] = field(default_factory=list)
    purpose: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None


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

    id: str
    provides: tuple[Grip[Any], ...]
    destination_param_grips: tuple[Grip[Any], ...]
    home_param_grips: tuple[Grip[Any], ...]

    def get_execution_mode(self) -> TapExecutionMode: ...

    def get_execution_role(self) -> TapExecutionRole: ...

    def set_execution_role(self, role: TapExecutionRole) -> None: ...

    def on_attach(self, home_context: GripContext) -> None: ...

    def on_detach(self) -> None: ...

    def on_connect(self, dest_context: GripContext, grip: Grip[Any]) -> None: ...

    def on_disconnect(self, dest_context: GripContext, grip: Grip[Any]) -> None: ...

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

    provides: tuple[Grip[Any], ...]

    def build(self) -> Tap: ...


@runtime_checkable
class SharedValueTap(Protocol):
    def set_shared_grip_value(self, grip: Grip[Any], value: Any) -> bool | None: ...


@runtime_checkable
class TapMaterializationRegistry(Protocol):
    def materialize_tap(self, grok: Grok, spec: SharedProjectionTapSpec) -> Tap: ...


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

    def get_child(self, name: str) -> GripContext | None: ...

    def create_child(self, name: str, *, priority: int = 0) -> GripContext: ...

    def get_or_create_child(self, name: str, *, priority: int = 0) -> GripContext: ...

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

    def apply_producer_delta(
        self,
        context: GripContext,
        delta: dict[str, Any] | EvaluationDelta,  # type: ignore[name-defined]
    ) -> None: ...


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

    def get_context_by_id(self, context_id: str) -> GripContext | None: ...

    def get_registry(self) -> GripRegistry: ...

    def get_tap_materialization_registry(self) -> TapMaterializationRegistry: ...

    def set_tap_materialization_registry(self, registry: TapMaterializationRegistry) -> None: ...

    def register_tap_at(self, context: GripContextLike, tap: Tap) -> None: ...

    def unregister_tap(self, tap: Tap) -> None: ...

    def notify_consumers(self, dest_ctx: GripContext, grip: Grip[Any], value: Any) -> int: ...

    def get_graph(self) -> dict[str, GripContextNode]: ...

    def get_async_loop(self) -> asyncio.AbstractEventLoop: ...

    def allocate_origin_mutation_seq(self) -> int: ...

    def get_last_origin_mutation_seq(self) -> int: ...
