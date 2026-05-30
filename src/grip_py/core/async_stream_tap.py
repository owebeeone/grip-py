"""Long-lived async stream tap implementation."""

from __future__ import annotations

import asyncio
import inspect
import random
import time
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .base_tap import BaseTap
from .grip import Grip
from .interfaces import GripContext


@dataclass(slots=True, frozen=True)
class AsyncStreamTapParams:
    """Streaming tap input containing resolved destination and home parameters."""

    destination_params: Mapping[Grip[Any], Any]
    home_params: Mapping[Grip[Any], Any]


@dataclass(slots=True, frozen=True)
class AsyncStreamRetryConfig:
    """Exponential backoff retry configuration for stream taps."""

    initial_delay_ms: int = 500
    max_delay_ms: int = 30_000
    backoff_multiplier: float = 2.0
    jitter_ratio: float = 0.5
    max_retries: int | None = None
    stable_reset_ms: int = 10_000
    retry_on_error: Callable[[BaseException], bool] | None = None
    random_fn: Callable[[], float] | None = None


RequestKeyFn = Callable[[AsyncStreamTapParams], str | None]
SubscribeFn = Callable[
    [AsyncStreamTapParams, asyncio.Event],
    AsyncIterable[Any] | Awaitable[AsyncIterable[Any]],
]
MapEventFn = Callable[[AsyncStreamTapParams, Any], Mapping[Grip[Any], Any]]
ResetFn = Callable[[AsyncStreamTapParams], Mapping[Grip[Any], Any]]


@dataclass(slots=True)
class _DestinationState:
    request_key: str | None = None


@dataclass(slots=True)
class _StreamState:
    request_key: str
    params: AsyncStreamTapParams
    destination_ids: set[str] = field(default_factory=set)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task[None] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    retry_task: asyncio.Task[None] | None = None
    stable_reset_task: asyncio.Task[None] | None = None
    retry_attempt: int = 0
    latest_event: Any = None
    has_latest_event: bool = False


@dataclass(init=False, eq=False)
class AsyncStreamMultiTap(BaseTap):
    """Multi-output tap for long-lived async streams shared by request key."""

    _request_key_of: RequestKeyFn
    _subscribe: SubscribeFn
    _map_event: MapEventFn
    _get_reset_updates: ResetFn | None
    _cache_latest: bool
    _cleanup_delay_ms: int
    _retry: AsyncStreamRetryConfig | None
    _on_error: Callable[[BaseException, str], None] | None

    _destination_states: dict[str, _DestinationState]
    _dest_context_by_id: dict[str, GripContext]
    _streams_by_key: dict[str, _StreamState]

    def __init__(
        self,
        *,
        provides: Iterable[Grip[Any]],
        destination_param_grips: Iterable[Grip[Any]] | None = None,
        home_param_grips: Iterable[Grip[Any]] | None = None,
        request_key_of: RequestKeyFn,
        subscribe: SubscribeFn,
        map_event: MapEventFn,
        get_reset_updates: ResetFn | None = None,
        cache_latest: bool = True,
        cleanup_delay_ms: int = 1000,
        retry: AsyncStreamRetryConfig | None = None,
        on_error: Callable[[BaseException, str], None] | None = None,
    ) -> None:
        super().__init__(
            provides=provides,
            destination_param_grips=destination_param_grips,
            home_param_grips=home_param_grips,
        )
        self._request_key_of = request_key_of
        self._subscribe = subscribe
        self._map_event = map_event
        self._get_reset_updates = get_reset_updates
        self._cache_latest = cache_latest
        self._cleanup_delay_ms = max(0, cleanup_delay_ms)
        self._retry = retry
        self._on_error = on_error
        self._destination_states = {}
        self._dest_context_by_id = {}
        self._streams_by_key = {}

    def on_disconnect(self, dest_context: GripContext, grip: Grip[Any]) -> None:
        super().on_disconnect(dest_context, grip)
        if self._producer is not None:
            for node, destination in self._producer.get_destinations().items():
                if node.id != dest_context.id:
                    continue
                if len(destination.get_grips()) > 1:
                    return
                break
        self._remove_destination(dest_context.id)

    def on_detach(self) -> None:
        for stream in tuple(self._streams_by_key.values()):
            self._close_stream(stream)
        self._streams_by_key.clear()
        self._destination_states.clear()
        self._dest_context_by_id.clear()
        super().on_detach()

    def produce(self, *, dest_context: GripContext | None = None) -> None:
        if dest_context is not None:
            self._sync_destination(dest_context)
            return
        if self._producer is None:
            return
        for node in tuple(self._producer.get_destinations().keys()):
            ctx = node.get_context()
            if ctx is not None:
                self._sync_destination(ctx)

    def produce_on_dest_params(self, dest_context: GripContext, grip: Grip[Any]) -> None:
        self._sync_destination(dest_context)

    def produce_on_home_params(self, grip: Grip[Any]) -> None:
        self.produce()

    def _sync_destination(self, ctx: GripContext) -> None:
        loop = self._get_runtime_loop()
        if loop is None:
            return

        destination_id = ctx.id
        params = self._get_stream_params(ctx)
        request_key = self._request_key_of(params)
        state = self._destination_states.setdefault(destination_id, _DestinationState())
        self._dest_context_by_id[destination_id] = ctx

        if request_key is None:
            self._remove_destination(destination_id)
            self._publish_reset(params, ctx)
            return

        request_key = str(request_key)
        if state.request_key == request_key:
            return

        self._detach_destination_from_stream(destination_id, state.request_key)
        state.request_key = request_key

        stream = self._streams_by_key.get(request_key)
        if stream is None:
            stream = _StreamState(request_key=request_key, params=params)
            self._streams_by_key[request_key] = stream

        self._cancel_task(stream.cleanup_task)
        stream.cleanup_task = None
        stream.destination_ids.add(destination_id)
        if stream.task is None or stream.task.done():
            if stream.retry_task is None or stream.retry_task.done():
                self._start_stream(stream, loop)
        if self._cache_latest and stream.has_latest_event:
            self._publish_event_to_destination(stream, destination_id, stream.latest_event)

    def _start_stream(self, stream: _StreamState, loop: asyncio.AbstractEventLoop) -> None:
        stream.cancel_event = asyncio.Event()
        stream.loop = loop
        stream.task = loop.create_task(self._run_stream(stream))

    async def _run_stream(self, stream: _StreamState) -> None:
        should_retry = False
        try:
            iterable = self._subscribe(stream.params, stream.cancel_event)
            if inspect.isawaitable(iterable):
                iterable = await iterable
            async for event in iterable:
                if stream.cancel_event.is_set():
                    break
                if self._cache_latest:
                    stream.latest_event = event
                    stream.has_latest_event = True
                self._mark_stream_stable(stream)
                for destination_id in tuple(stream.destination_ids):
                    self._publish_event_to_destination(stream, destination_id, event)
            should_retry = not stream.cancel_event.is_set() and bool(stream.destination_ids)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if not stream.cancel_event.is_set():
                self._on_error and self._on_error(exc, stream.request_key)
                for destination_id in tuple(stream.destination_ids):
                    destination = self._dest_context_by_id.get(destination_id)
                    if destination is not None:
                        self._publish_reset(stream.params, destination)
                should_retry = self._should_retry(exc)
        finally:
            self._cancel_task(stream.stable_reset_task)
            stream.stable_reset_task = None
            if (
                should_retry
                and stream.destination_ids
                and self._streams_by_key.get(stream.request_key) is stream
            ):
                self._schedule_retry(stream)
            elif not stream.destination_ids and self._streams_by_key.get(stream.request_key) is stream:
                self._streams_by_key.pop(stream.request_key, None)

    def _publish_event_to_destination(
        self,
        stream: _StreamState,
        destination_id: str,
        event: Any,
    ) -> None:
        destination = self._dest_context_by_id.get(destination_id)
        if destination is None:
            self._detach_destination_from_stream(destination_id, stream.request_key)
            return
        values = dict(self._map_event(stream.params, event))
        if values:
            self.publish(values, dest_context=destination)

    def _publish_reset(self, params: AsyncStreamTapParams, destination: GripContext) -> None:
        if self._get_reset_updates is not None:
            values = dict(self._get_reset_updates(params))
        else:
            values = {grip: grip.default for grip in self.provides}
        if values:
            self.publish(values, dest_context=destination)

    def _get_stream_params(self, ctx: GripContext) -> AsyncStreamTapParams:
        return AsyncStreamTapParams(
            destination_params={
                grip: self.get_destination_param_value(ctx, grip)
                for grip in self.destination_param_grips
            },
            home_params={
                grip: self.get_home_param_value(grip)
                for grip in self.home_param_grips
            },
        )

    def _remove_destination(self, destination_id: str) -> None:
        self._dest_context_by_id.pop(destination_id, None)
        state = self._destination_states.pop(destination_id, None)
        self._detach_destination_from_stream(destination_id, state.request_key if state else None)

    def _detach_destination_from_stream(
        self,
        destination_id: str,
        request_key: str | None,
    ) -> None:
        if request_key is None:
            return
        stream = self._streams_by_key.get(request_key)
        if stream is None:
            return
        stream.destination_ids.discard(destination_id)
        if stream.destination_ids:
            return
        self._schedule_stream_cleanup(stream)

    def _schedule_stream_cleanup(self, stream: _StreamState) -> None:
        if self._cleanup_delay_ms <= 0:
            self._close_stream(stream)
            return
        loop = stream.loop or self._get_runtime_loop()
        if loop is None:
            self._close_stream(stream)
            return

        self._cancel_task(stream.cleanup_task)

        async def cleanup() -> None:
            try:
                await asyncio.sleep(self._cleanup_delay_ms / 1000.0)
            except asyncio.CancelledError:
                return
            if not stream.destination_ids:
                self._close_stream(stream)

        stream.cleanup_task = loop.create_task(cleanup())

    def _close_stream(self, stream: _StreamState) -> None:
        self._cancel_task(stream.cleanup_task)
        self._cancel_task(stream.retry_task)
        self._cancel_task(stream.stable_reset_task)
        stream.cleanup_task = None
        stream.retry_task = None
        stream.stable_reset_task = None
        self._set_cancel_event(stream)
        self._cancel_task(stream.task)
        self._streams_by_key.pop(stream.request_key, None)

    def _should_retry(self, error: BaseException) -> bool:
        if self._retry is None:
            return False
        return self._retry.retry_on_error(error) if self._retry.retry_on_error else True

    def _schedule_retry(self, stream: _StreamState) -> None:
        retry = self._retry
        if retry is None:
            return
        if retry.max_retries is not None and stream.retry_attempt >= max(0, retry.max_retries):
            return
        loop = stream.loop or self._get_runtime_loop()
        if loop is None:
            return

        delay_ms = self._retry_delay_ms(stream.retry_attempt, retry)
        stream.retry_attempt += 1
        self._cancel_task(stream.retry_task)

        async def retry_later() -> None:
            try:
                await asyncio.sleep(delay_ms / 1000.0)
            except asyncio.CancelledError:
                return
            if not stream.destination_ids or self._streams_by_key.get(stream.request_key) is not stream:
                return
            self._start_stream(stream, loop)

        stream.retry_task = loop.create_task(retry_later())

    def _mark_stream_stable(self, stream: _StreamState) -> None:
        retry = self._retry
        if retry is None or stream.retry_attempt == 0 or stream.stable_reset_task is not None:
            return
        loop = stream.loop or self._get_runtime_loop()
        if loop is None:
            return

        async def reset_attempt() -> None:
            try:
                await asyncio.sleep(max(0, retry.stable_reset_ms) / 1000.0)
            except asyncio.CancelledError:
                return
            if stream.task is not None and not stream.task.done():
                stream.retry_attempt = 0
            stream.stable_reset_task = None

        stream.stable_reset_task = loop.create_task(reset_attempt())

    @staticmethod
    def _retry_delay_ms(attempt: int, retry: AsyncStreamRetryConfig) -> int:
        base_delay = min(
            max(0, retry.max_delay_ms),
            int(max(0, retry.initial_delay_ms) * (retry.backoff_multiplier ** attempt)),
        )
        jitter_ratio = min(1.0, max(0.0, retry.jitter_ratio))
        random_value = retry.random_fn() if retry.random_fn is not None else random.random()
        jitter_scale = 1 - jitter_ratio + random_value * jitter_ratio
        return max(0, round(base_delay * jitter_scale))

    @staticmethod
    def _cancel_task(task: asyncio.Task[Any] | None) -> None:
        if task is not None and not task.done():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            task_loop = task.get_loop()
            if running is task_loop:
                task.cancel()
            elif task_loop.is_running():
                task_loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()

    def _get_runtime_loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            pass
        if self._engine is None:
            return None
        loop = self._engine.get_async_loop()
        return loop if loop.is_running() else None

    @staticmethod
    def _set_cancel_event(stream: _StreamState) -> None:
        loop = stream.loop
        if loop is None:
            stream.cancel_event.set()
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            stream.cancel_event.set()
        elif loop.is_running():
            loop.call_soon_threadsafe(stream.cancel_event.set)
        else:
            stream.cancel_event.set()


def create_async_stream_multi_tap(
    *,
    provides: Iterable[Grip[Any]],
    destination_param_grips: Iterable[Grip[Any]] | None = None,
    home_param_grips: Iterable[Grip[Any]] | None = None,
    request_key_of: RequestKeyFn,
    subscribe: SubscribeFn,
    map_event: MapEventFn,
    get_reset_updates: ResetFn | None = None,
    cache_latest: bool = True,
    cleanup_delay_ms: int = 1000,
    retry: AsyncStreamRetryConfig | None = None,
    on_error: Callable[[BaseException, str], None] | None = None,
) -> AsyncStreamMultiTap:
    """Create an async stream multi-output tap."""
    return AsyncStreamMultiTap(
        provides=provides,
        destination_param_grips=destination_param_grips,
        home_param_grips=home_param_grips,
        request_key_of=request_key_of,
        subscribe=subscribe,
        map_event=map_event,
        get_reset_updates=get_reset_updates,
        cache_latest=cache_latest,
        cleanup_delay_ms=cleanup_delay_ms,
        retry=retry,
        on_error=on_error,
    )
