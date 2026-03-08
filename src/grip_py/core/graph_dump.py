"""Graph dump helpers for debugging and visualization."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .drip import Drip
from .graph import GripContextNode, ProducerRecord
from .grok_impl import GrokImpl
from .grip import Grip


@dataclass(slots=True)
class GraphDumpSummary:
    context_count: int
    tap_count: int
    drip_count: int
    collected_context_count: int
    live_context_count: int
    missing_nodes: list[str]
    nodes_not_reaped: list[str]


@dataclass(slots=True)
class GraphDumpNodeContext:
    key: str
    type: str
    label: str | None
    parents: list[dict[str, Any]]
    children: list[str]
    taps: list[str]
    metadata: dict[str, Any]
    gc_status: str
    consumer_drips: list[str]


@dataclass(slots=True)
class GraphDumpTapDestinationDrip:
    drip: str
    value: Any | None = None


@dataclass(slots=True)
class GraphDumpTapDestination:
    context: str
    dest_parameter_drips: list[str | GraphDumpTapDestinationDrip]
    output_drips: list[str | GraphDumpTapDestinationDrip]


@dataclass(slots=True)
class GraphDumpNodeTap:
    key: str
    type: str
    class_name: str
    provides_grips: list[str]
    publisher_context: str
    destinations: list[GraphDumpTapDestination]
    state: dict[str, Any] | None = None


@dataclass(slots=True)
class GraphDumpNodeDrip:
    key: str
    type: str
    grip: str
    provider_tap: str | None
    dest_context: str
    value: Any | None
    value_preview: Any | None
    subscriber_count: int | None


@dataclass(slots=True)
class GraphDumpNodes:
    contexts: list[GraphDumpNodeContext] = field(default_factory=list)
    taps: list[GraphDumpNodeTap] = field(default_factory=list)
    drips: list[GraphDumpNodeDrip] = field(default_factory=list)


@dataclass(slots=True)
class GraphDump:
    timestamp_iso: str
    summary: GraphDumpSummary
    nodes: GraphDumpNodes


@dataclass(slots=True)
class GraphDumpOptions:
    include_values: bool = True
    max_value_length: int = 200
    include_tap_values: bool = False


@dataclass(slots=True)
class GraphDumpKeyRegistry:
    """Stable key allocation for graph objects across dump calls."""

    _context_seq: int = 1
    _tap_seq: int = 1
    _drip_seq: int = 1
    _context_to_key: dict[Any, str] = field(default_factory=dict)
    _tap_to_key: dict[Any, str] = field(default_factory=dict)
    _drip_to_key: dict[Any, str] = field(default_factory=dict)

    def get_context_key(self, node: GripContextNode) -> str:
        existing = self._context_to_key.get(node)
        if existing is not None:
            return existing
        key = f"kCtxt{self._context_seq}"
        self._context_seq += 1
        self._context_to_key[node] = key
        return key

    def get_tap_key(self, tap: Any) -> str:
        existing = self._tap_to_key.get(tap)
        if existing is not None:
            return existing
        key = f"kTap{self._tap_seq}"
        self._tap_seq += 1
        self._tap_to_key[tap] = key
        return key

    def get_drip_key(self, drip: Drip[Any]) -> str:
        existing = self._drip_to_key.get(drip)
        if existing is not None:
            return existing
        key = f"kDrip{self._drip_seq}"
        self._drip_seq += 1
        self._drip_to_key[drip] = key
        return key


@dataclass(slots=True, init=False)
class GripGraphDumper:
    """Read-only graph dumper for the current Grok state."""

    _grok: GrokImpl
    _keys: GraphDumpKeyRegistry
    _opts: GraphDumpOptions

    def __init__(
        self,
        grok: GrokImpl,
        *,
        keys: GraphDumpKeyRegistry | None = None,
        opts: GraphDumpOptions | None = None,
    ) -> None:
        self._grok = grok
        self._keys = keys or GraphDumpKeyRegistry()
        self._opts = opts or GraphDumpOptions()

    def dump(self) -> GraphDump:
        sanity = self._grok.get_graph_sanity_check()
        nodes = GraphDumpNodes()

        seen_taps: set[Any] = set()
        seen_drips: set[Drip[Any]] = set()

        for node in sanity.nodes.values():
            nodes.contexts.append(self._build_context_node(node))

            for rec in set(node.producer_by_tap.values()):
                tap = rec.tap
                if tap in seen_taps:
                    continue
                nodes.taps.append(self._build_tap_node(node, tap, rec, seen_drips, nodes.drips))
                seen_taps.add(tap)

            for grip, drip in node.get_consumers().items():
                if drip in seen_drips:
                    continue
                nodes.drips.append(self._build_drip_node(node, grip, drip))
                seen_drips.add(drip)

        summary = GraphDumpSummary(
            context_count=len(nodes.contexts),
            tap_count=len(nodes.taps),
            drip_count=len(nodes.drips),
            collected_context_count=sum(1 for c in nodes.contexts if c.gc_status == "collected"),
            live_context_count=sum(1 for c in nodes.contexts if c.gc_status == "live"),
            missing_nodes=[self._keys.get_context_key(n) for n in sanity.missing_nodes],
            nodes_not_reaped=[self._keys.get_context_key(n) for n in sanity.nodes_not_reaped],
        )

        import datetime as _dt

        return GraphDump(
            timestamp_iso=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            summary=summary,
            nodes=nodes,
        )

    def _build_context_node(self, node: GripContextNode) -> GraphDumpNodeContext:
        context = node.get_context()
        parents = [
            {
                "ctx": self._keys.get_context_key(parent.node),
                "priority": parent.priority,
            }
            for parent in node.get_parents_with_priority()
        ]
        children = [self._keys.get_context_key(child) for child in node.get_children_nodes()]

        taps = [
            self._keys.get_tap_key(rec.tap)
            for rec in {record for record in node.producer_by_tap.values()}
        ]

        consumer_drips = [self._keys.get_drip_key(drip) for drip in node.get_consumers().values()]

        return GraphDumpNodeContext(
            key=self._keys.get_context_key(node),
            type="Context",
            label=context.id if context is not None else node.id,
            parents=parents,
            children=children,
            taps=taps,
            metadata={"id": node.id, "is_root": node.is_root()},
            gc_status="live" if context is not None else "collected",
            consumer_drips=consumer_drips,
        )

    def _build_tap_node(
        self,
        home_node: GripContextNode,
        tap: Any,
        rec: ProducerRecord,
        seen_drips: set[Drip[Any]],
        drips_out: list[GraphDumpNodeDrip],
    ) -> GraphDumpNodeTap:
        destinations: list[GraphDumpTapDestination] = []

        destination_param_grips = tuple(getattr(tap, "destination_param_grips", tuple()) or tuple())

        for dest_node, destination in rec.get_destinations().items():
            dest_context_key = self._keys.get_context_key(dest_node)

            dest_param_entries: list[str | GraphDumpTapDestinationDrip] = []
            output_entries: list[str | GraphDumpTapDestinationDrip] = []

            for param_grip in destination_param_grips:
                drip = dest_node.get_consumers().get(param_grip)
                if drip is None:
                    continue
                drip_key = self._keys.get_drip_key(drip)
                if self._opts.include_tap_values:
                    dest_param_entries.append(
                        GraphDumpTapDestinationDrip(
                            drip=drip_key,
                            value=self._preview_value(drip.get()) if self._opts.include_values else None,
                        )
                    )
                else:
                    dest_param_entries.append(drip_key)

            for out_grip in tuple(destination.get_grips()):
                drip = dest_node.get_consumers().get(out_grip)
                if drip is None:
                    continue
                drip_key = self._keys.get_drip_key(drip)
                if self._opts.include_tap_values:
                    output_entries.append(
                        GraphDumpTapDestinationDrip(
                            drip=drip_key,
                            value=self._preview_value(drip.get()) if self._opts.include_values else None,
                        )
                    )
                else:
                    output_entries.append(drip_key)

                if drip not in seen_drips:
                    drips_out.append(self._build_drip_node(dest_node, out_grip, drip))
                    seen_drips.add(drip)

            destinations.append(
                GraphDumpTapDestination(
                    context=dest_context_key,
                    dest_parameter_drips=dest_param_entries,
                    output_drips=output_entries,
                )
            )

        state = self._extract_tap_state(tap)

        provides = tuple(getattr(tap, "provides", tuple()) or tuple())
        return GraphDumpNodeTap(
            key=self._keys.get_tap_key(tap),
            type="Tap",
            class_name=getattr(tap.__class__, "__name__", "Tap"),
            provides_grips=[self._describe_grip(g) for g in provides],
            publisher_context=self._keys.get_context_key(home_node),
            destinations=destinations,
            state=state,
        )

    def _extract_tap_state(self, tap: Any) -> dict[str, Any] | None:
        try:
            get_fn = getattr(tap, "get", None)
            if callable(get_fn):
                value = get_fn()
                if value is not None:
                    return {"simple_value": self._preview_value(value)}
        except Exception:
            return None
        return None

    def _build_drip_node(
        self,
        dest_node: GripContextNode,
        grip: Grip[Any],
        drip: Drip[Any],
    ) -> GraphDumpNodeDrip:
        value_preview = self._preview_value(drip.get()) if self._opts.include_values else None
        return GraphDumpNodeDrip(
            key=self._keys.get_drip_key(drip),
            type="Drip",
            grip=self._describe_grip(grip),
            provider_tap=self._find_provider_tap_key(dest_node, grip),
            dest_context=self._keys.get_context_key(dest_node),
            value=value_preview,
            value_preview=value_preview,
            subscriber_count=self._count_subscribers(drip),
        )

    def _find_provider_tap_key(self, dest_node: GripContextNode, grip: Grip[Any]) -> str | None:
        provider = dest_node.get_resolved_providers().get(grip)
        if provider is None:
            return None
        record = provider.get_producers().get(grip)
        if record is None:
            return None
        return self._keys.get_tap_key(record.tap)

    @staticmethod
    def _describe_grip(grip: Grip[Any]) -> str:
        label = grip.name if getattr(grip, "name", None) else grip.key
        return f"Grip({label})"

    def _preview_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (bool, int, float, str)):
            text = value if isinstance(value, str) else str(value)
            if isinstance(value, str) and len(text) > self._opts.max_value_length:
                return text[: self._opts.max_value_length] + "..."
            return value
        try:
            encoded = json.dumps(value)
            if len(encoded) > self._opts.max_value_length:
                encoded = encoded[: self._opts.max_value_length] + "..."
            return json.loads(encoded)
        except Exception:
            text = str(value)
            if len(text) > self._opts.max_value_length:
                return text[: self._opts.max_value_length] + "..."
            return text

    @staticmethod
    def _count_subscribers(drip: Drip[Any]) -> int | None:
        try:
            return (
                len(getattr(drip, "_subs", ()))
                + len(getattr(drip, "_priority_subs", ()))
                + len(getattr(drip, "_async_subs", ()))
            )
        except Exception:
            return None
