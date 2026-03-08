"""Async tap implementations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .base_tap import BaseTap
from .grip import Grip
from .interfaces import GripContext


@dataclass(slots=True, frozen=True)
class AsyncTapParams:
    destination_params: Mapping[Grip[Any], Any]
    home_params: Mapping[Grip[Any], Any]


AsyncFetcher = Callable[[AsyncTapParams], Awaitable[dict[Grip[Any], Any]]]
RequestKeyFn = Callable[[AsyncTapParams], str | None]


@dataclass(slots=True, frozen=True)
class _CacheEntry:
    values: dict[Grip[Any], Any]
    expires_at_monotonic: float


@dataclass(init=False, eq=False)
class AsyncTap(BaseTap):
    """Destination-aware async tap with request-key sharing and TTL cache."""

    _fetcher: AsyncFetcher
    _request_key_of: RequestKeyFn | None
    _latest_only: bool
    _cache_ttl_ms: int
    _cleanup_delay_ms: int
    _inflight_by_key: dict[str, asyncio.Task[dict[Grip[Any], Any]]]
    _cache_by_key: dict[str, _CacheEntry]
    _dest_key_by_id: dict[str, str]
    _dest_context_by_id: dict[str, GripContext]
    _key_dest_ids: dict[str, set[str]]
    _cleanup_tasks: dict[str, asyncio.Task[None]]

    def __init__(
        self,
        *,
        provides: Iterable[Grip[Any]],
        destination_param_grips: Iterable[Grip[Any]] | None = None,
        home_param_grips: Iterable[Grip[Any]] | None = None,
        request_key_of: RequestKeyFn | None = None,
        latest_only: bool = True,
        cache_ttl_ms: int = 0,
        cleanup_delay_ms: int = 1000,
        fetcher: AsyncFetcher,
    ):
        super().__init__(
            provides=provides,
            destination_param_grips=destination_param_grips,
            home_param_grips=home_param_grips,
        )
        self._fetcher = fetcher
        self._request_key_of = request_key_of
        self._latest_only = latest_only
        self._cache_ttl_ms = max(0, cache_ttl_ms)
        self._cleanup_delay_ms = max(0, cleanup_delay_ms)
        self._inflight_by_key: dict[str, asyncio.Task[dict[Grip[Any], Any]]] = {}
        self._cache_by_key: dict[str, _CacheEntry] = {}
        self._dest_key_by_id: dict[str, str] = {}
        self._dest_context_by_id: dict[str, GripContext] = {}
        self._key_dest_ids: dict[str, set[str]] = {}
        self._cleanup_tasks: dict[str, asyncio.Task[None]] = {}

    def on_disconnect(self, dest_context: GripContext, grip: Grip[Any]) -> None:
        super().on_disconnect(dest_context, grip)
        if self._producer is not None:
            has_destination = any(
                node.id == dest_context.id for node in self._producer.get_destinations()
            )
            if has_destination:
                return
        self._detach_destination(dest_context.id)

    def on_detach(self) -> None:
        for task in tuple(self._inflight_by_key.values()):
            if not task.done():
                task.cancel()
        self._inflight_by_key.clear()
        self._cache_by_key.clear()
        self._dest_key_by_id.clear()
        self._dest_context_by_id.clear()
        self._key_dest_ids.clear()
        for cleanup_task in tuple(self._cleanup_tasks.values()):
            if not cleanup_task.done():
                cleanup_task.cancel()
        self._cleanup_tasks.clear()
        super().on_detach()

    def produce(self, *, dest_context: GripContext | None = None) -> None:
        if dest_context is not None:
            self._produce_for_destination(dest_context)
            return

        if self._producer is None:
            return
        for node in tuple(self._producer.get_destinations().keys()):
            ctx = node.get_context()
            if ctx is not None:
                self._produce_for_destination(ctx)

    def _produce_for_destination(self, ctx: GripContext) -> None:
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        destination_id = ctx.id
        request_params = self._get_request_params(ctx)
        key = self._build_request_key(request_params)
        self._dest_context_by_id[destination_id] = ctx
        previous_key = self._dest_key_by_id.get(destination_id)
        if previous_key != key:
            self._detach_destination_from_key(destination_id, previous_key)
            self._attach_destination_to_key(destination_id, key)

        if self._try_publish_cached(ctx, key):
            return

        task = self._inflight_by_key.get(key)
        if task is not None and not task.done():
            if self._latest_only:
                return
            task.cancel()

        self._inflight_by_key[key] = loop.create_task(
            self._run_fetch_for_key(key, request_params)
        )

    async def _run_fetch_for_key(
        self,
        key: str,
        request_params: AsyncTapParams,
    ) -> dict[Grip[Any], Any]:
        try:
            values = await self._fetcher(
                AsyncTapParams(
                    destination_params=dict(request_params.destination_params),
                    home_params=dict(request_params.home_params),
                )
            )
        except asyncio.CancelledError:
            values = {}
        except Exception:
            values = {}

        try:
            if values and self._cache_ttl_ms > 0:
                ttl_seconds = self._cache_ttl_ms / 1000.0
                self._cache_by_key[key] = _CacheEntry(
                    values=dict(values),
                    expires_at_monotonic=time.monotonic() + ttl_seconds,
                )

            destination_ids = tuple(self._key_dest_ids.get(key, ()))
            for destination_id in destination_ids:
                if self._latest_only and self._dest_key_by_id.get(destination_id) != key:
                    continue
                destination = self._dest_context_by_id.get(destination_id)
                if destination is not None and values:
                    self.publish(values, dest_context=destination)
            return values
        finally:
            current = asyncio.current_task()
            if self._inflight_by_key.get(key) is current:
                self._inflight_by_key.pop(key, None)

    def _build_request_key(self, request_params: AsyncTapParams) -> str:
        if not self.destination_param_grips and not self.home_param_grips:
            return "__shared__"
        if self._request_key_of is not None:
            key = self._request_key_of(request_params)
            if key is not None:
                return str(key)
        parts = [
            f"h:{grip.key}:{request_params.home_params.get(grip)!r}"
            for grip in self.home_param_grips
        ]
        parts.extend(
            f"d:{grip.key}:{request_params.destination_params.get(grip)!r}"
            for grip in self.destination_param_grips
        )
        return "|".join(parts)

    def _get_destination_params(self, ctx: GripContext) -> dict[Grip[Any], Any]:
        if not self.destination_param_grips:
            return {}
        return {
            grip: self.get_destination_param_value(ctx, grip)
            for grip in self.destination_param_grips
        }

    def _get_home_params(self) -> dict[Grip[Any], Any]:
        if not self.home_param_grips:
            return {}
        return {
            grip: self.get_home_param_value(grip)
            for grip in self.home_param_grips
        }

    def _get_request_params(self, ctx: GripContext) -> AsyncTapParams:
        return AsyncTapParams(
            destination_params=self._get_destination_params(ctx),
            home_params=self._get_home_params(),
        )

    def _try_publish_cached(self, ctx: GripContext, key: str) -> bool:
        if self._cache_ttl_ms <= 0:
            return False
        entry = self._cache_by_key.get(key)
        if entry is None:
            return False
        if entry.expires_at_monotonic < time.monotonic():
            self._cache_by_key.pop(key, None)
            return False
        self.publish(entry.values, dest_context=ctx)
        return True

    def _attach_destination_to_key(self, destination_id: str, key: str) -> None:
        self._dest_key_by_id[destination_id] = key
        destination_set = self._key_dest_ids.setdefault(key, set())
        destination_set.add(destination_id)
        cleanup_task = self._cleanup_tasks.pop(key, None)
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()

    def _detach_destination_from_key(self, destination_id: str, key: str | None) -> None:
        if key is None:
            return
        destination_set = self._key_dest_ids.get(key)
        if destination_set is None:
            return
        destination_set.discard(destination_id)
        if destination_set:
            return
        self._key_dest_ids.pop(key, None)
        self._schedule_key_cleanup(key)

    def _detach_destination(self, destination_id: str) -> None:
        self._dest_context_by_id.pop(destination_id, None)
        key = self._dest_key_by_id.pop(destination_id, None)
        self._detach_destination_from_key(destination_id, key)

    def _schedule_key_cleanup(self, key: str) -> None:
        if self._cleanup_delay_ms <= 0:
            self._cleanup_key_state(key)
            return

        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._cleanup_key_state(key)
            return

        existing = self._cleanup_tasks.pop(key, None)
        if existing is not None and not existing.done():
            existing.cancel()

        async def run_cleanup() -> None:
            try:
                await asyncio.sleep(self._cleanup_delay_ms / 1000.0)
            except asyncio.CancelledError:
                return
            if key in self._key_dest_ids:
                return
            self._cleanup_key_state(key)

        self._cleanup_tasks[key] = loop.create_task(run_cleanup())

    def _cleanup_key_state(self, key: str) -> None:
        task = self._inflight_by_key.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
        self._cache_by_key.pop(key, None)
        cleanup_task = self._cleanup_tasks.pop(key, None)
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()


def create_async_tap(
    *,
    provides: Iterable[Grip[Any]],
    destination_param_grips: Iterable[Grip[Any]] | None = None,
    home_param_grips: Iterable[Grip[Any]] | None = None,
    request_key_of: RequestKeyFn | None = None,
    latest_only: bool = True,
    cache_ttl_ms: int = 0,
    cleanup_delay_ms: int = 1000,
    fetcher: AsyncFetcher,
) -> AsyncTap:
    return AsyncTap(
        provides=provides,
        destination_param_grips=destination_param_grips,
        home_param_grips=home_param_grips,
        request_key_of=request_key_of,
        latest_only=latest_only,
        cache_ttl_ms=cache_ttl_ms,
        cleanup_delay_ms=cleanup_delay_ms,
        fetcher=fetcher,
    )
