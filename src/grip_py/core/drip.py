"""Drip value stream primitives."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

ErrorPolicy = Literal["log", "raise", "collect"]
ElidePolicy = Literal["ts", "equality", "none"]
Subscriber = Callable[[T | None], None]
AsyncSubscriber = Callable[[T | None], Awaitable[None]]
VoidCallback = Callable[[], None]

_SCALAR_TYPES = (type(None), bool, int, float, str, bytes)
_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, eq=False)
class _AsyncSubscription(Generic[T]):
    """Internal async callback subscription state."""

    fn: AsyncSubscriber[T]
    queue: asyncio.Queue[T | None] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None


@dataclass(slots=True, init=False, eq=False)
class Drip(Generic[T]):
    """Subscribable value stream with sync and queued notification modes."""

    _value: T | None
    _error_policy: ErrorPolicy
    _elide_policy: ElidePolicy
    _callback_error_handler: Callable[[Exception], None] | None
    _subs: set[Subscriber[T]]
    _priority_subs: set[Subscriber[T]]
    _async_subs: set[_AsyncSubscription[T]]
    _first_sub_callbacks: set[VoidCallback]
    _zero_sub_callbacks: set[VoidCallback]
    _enqueued: bool
    _zero_check_scheduled: bool
    _loop: asyncio.AbstractEventLoop | None
    _callback_errors: list[Exception]
    _lock: threading.RLock

    def __init__(
        self,
        initial: T | None = None,
        *,
        error_policy: ErrorPolicy = "log",
        elide_policy: ElidePolicy = "ts",
        callback_error_handler: Callable[[Exception], None] | None = None,
    ) -> None:
        if error_policy not in {"log", "raise", "collect"}:
            raise ValueError(f"Unsupported error_policy: {error_policy!r}")
        if elide_policy not in {"ts", "equality", "none"}:
            raise ValueError(f"Unsupported elide_policy: {elide_policy!r}")

        self._value: T | None = initial
        self._error_policy: ErrorPolicy = error_policy
        self._elide_policy: ElidePolicy = elide_policy
        self._callback_error_handler = callback_error_handler

        self._subs: set[Subscriber[T]] = set()
        self._priority_subs: set[Subscriber[T]] = set()
        self._async_subs: set[_AsyncSubscription[T]] = set()
        self._first_sub_callbacks: set[VoidCallback] = set()
        self._zero_sub_callbacks: set[VoidCallback] = set()

        self._enqueued = False
        self._zero_check_scheduled = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._callback_errors: list[Exception] = []
        self._lock = threading.RLock()

    def get(self) -> T | None:
        """Return the current value snapshot."""
        with self._lock:
            return self._value

    def get_callback_errors(self) -> tuple[Exception, ...]:
        """Return collected callback exceptions."""
        with self._lock:
            return tuple(self._callback_errors)

    def next(self, value: T | None) -> None:
        """Update value and notify subscribers."""
        with self._lock:
            current = self._value
            if self._is_same_value(current, value):
                return
            self._value = value
            priority_subs = tuple(self._priority_subs)
            regular_subs_exist = bool(self._subs)
            async_subs = tuple(self._async_subs)
            should_enqueue = regular_subs_exist and not self._enqueued
            if should_enqueue:
                self._enqueued = True

        self._invoke_subscribers(priority_subs, value)
        if async_subs:
            self._schedule(
                lambda v=value, subscribers=async_subs: self._dispatch_async_subscribers(
                    subscribers, v
                )
            )
        if should_enqueue:
            self._schedule(self._task_queue_callback)

    def next_threadsafe(self, value: T | None) -> None:
        """Thread-safe value update helper."""
        loop = self._get_or_bind_loop()
        if loop and loop.is_running():
            loop.call_soon_threadsafe(self.next, value)
            return
        self.next(value)

    def has_subscribers(self) -> bool:
        """Return True when either subscriber queue is non-empty."""
        with self._lock:
            return self._has_any_subscribers_unlocked()

    def subscribe(self, fn: Subscriber[T]) -> Callable[[], None]:
        """Subscribe using queued notifications."""
        if inspect.iscoroutinefunction(fn):
            raise TypeError(
                "Async callback passed to subscribe; use subscribe_async instead"
            )
        return self._subscribe_with(self._subs, fn)

    def subscribe_priority(self, fn: Subscriber[T]) -> Callable[[], None]:
        """Subscribe using synchronous notifications."""
        if inspect.iscoroutinefunction(fn):
            raise TypeError(
                "Async callback passed to subscribe_priority; use subscribe_async instead"
            )
        return self._subscribe_with(self._priority_subs, fn)

    def subscribe_async(self, fn: AsyncSubscriber[T]) -> Callable[[], None]:
        """Subscribe using an async callback with per-subscriber ordered delivery."""
        loop = self._require_bound_running_loop()
        subscription: _AsyncSubscription[T] = _AsyncSubscription(fn)

        with self._lock:
            was_empty = not self._has_any_subscribers_unlocked()
            self._async_subs.add(subscription)
            first_callbacks = tuple(self._first_sub_callbacks) if was_empty else ()
            current = self._value

        subscription.task = loop.create_task(self._run_async_subscription(subscription))
        if first_callbacks:
            self._invoke_void_callbacks(first_callbacks)
        subscription.queue.put_nowait(current)

        def unsubscribe() -> None:
            removed = self._remove_async_subscriber(subscription)
            if removed:
                task = subscription.task
                if task is not None and not task.done():
                    task.cancel()
                self._schedule_zero_check_if_needed()

        return unsubscribe

    def add_on_first_subscriber(self, fn: VoidCallback) -> None:
        with self._lock:
            self._first_sub_callbacks.add(fn)

    def add_on_zero_subscribers(self, fn: VoidCallback) -> None:
        with self._lock:
            self._zero_sub_callbacks.add(fn)

    def unsubscribe_all(self) -> None:
        with self._lock:
            had_subscribers = self._has_any_subscribers_unlocked()
            self._subs.clear()
            self._priority_subs.clear()
            async_subs = tuple(self._async_subs)
            self._async_subs.clear()
            self._enqueued = False
            zero_callbacks = tuple(self._zero_sub_callbacks)
        for subscription in async_subs:
            if subscription.task is not None and not subscription.task.done():
                subscription.task.cancel()
        if had_subscribers:
            self._invoke_void_callbacks(zero_callbacks)

    def _subscribe_with(
        self,
        queue: set[Subscriber[T]],
        fn: Subscriber[T],
    ) -> Callable[[], None]:
        with self._lock:
            was_empty = not self._has_any_subscribers_unlocked()
            queue.add(fn)
            first_callbacks = tuple(self._first_sub_callbacks) if was_empty else ()
            current = self._value

        if first_callbacks:
            self._invoke_void_callbacks(first_callbacks)

        try:
            self._invoke_subscribers((fn,), current)
        except Exception:
            removed = self._remove_subscriber(queue, fn)
            if removed:
                self._schedule_zero_check_if_needed()
            raise

        def unsubscribe() -> None:
            removed_inner = self._remove_subscriber(queue, fn)
            if removed_inner:
                self._schedule_zero_check_if_needed()

        return unsubscribe

    def _remove_subscriber(self, queue: set[Subscriber[T]], fn: Subscriber[T]) -> bool:
        with self._lock:
            if fn not in queue:
                return False
            queue.remove(fn)
            return True

    def _remove_async_subscriber(self, subscription: _AsyncSubscription[T]) -> bool:
        with self._lock:
            if subscription not in self._async_subs:
                return False
            self._async_subs.remove(subscription)
            return True

    async def _run_async_subscription(self, subscription: _AsyncSubscription[T]) -> None:
        try:
            while True:
                value = await subscription.queue.get()
                try:
                    result = subscription.fn(value)
                    if not inspect.isawaitable(result):
                        raise TypeError(
                            "subscribe_async callback must return an awaitable result"
                        )
                    await result
                except Exception as exc:
                    self._handle_callback_exception(exc)
        except asyncio.CancelledError:
            return
        finally:
            removed = self._remove_async_subscriber(subscription)
            if removed:
                self._schedule_zero_check_if_needed()

    def _dispatch_async_subscribers(
        self,
        subscriptions: tuple[_AsyncSubscription[T], ...],
        value: T | None,
    ) -> None:
        for subscription in subscriptions:
            task = subscription.task
            if task is None or task.done():
                continue
            subscription.queue.put_nowait(value)

    def _schedule_zero_check_if_needed(self) -> None:
        with self._lock:
            if self._has_any_subscribers_unlocked() or self._zero_check_scheduled:
                return
            self._zero_check_scheduled = True
        self._schedule(self._zero_check_callback)

    def _zero_check_callback(self) -> None:
        with self._lock:
            self._zero_check_scheduled = False
            if self._has_any_subscribers_unlocked():
                return
            callbacks = tuple(self._zero_sub_callbacks)
        self._invoke_void_callbacks(callbacks)

    def _task_queue_callback(self) -> None:
        with self._lock:
            self._enqueued = False
            subs = tuple(self._subs)
            value = self._value
        self._invoke_subscribers(subs, value)

    def _invoke_subscribers(
        self,
        subscribers: tuple[Subscriber[T], ...],
        value: T | None,
    ) -> None:
        for fn in subscribers:
            try:
                result = fn(value)
                if inspect.isawaitable(result):
                    self._close_awaitable_if_needed(result)
                    raise TypeError(
                        "Async callback passed to subscribe/subscribe_priority; use subscribe_async"
                    )
            except Exception as exc:  # pragma: no cover - branch tested via policies
                self._handle_callback_exception(exc)

    def _invoke_void_callbacks(self, callbacks: tuple[VoidCallback, ...]) -> None:
        for fn in callbacks:
            try:
                result = fn()
                if inspect.isawaitable(result):
                    self._close_awaitable_if_needed(result)
                    raise TypeError("Lifecycle callbacks must be synchronous callables")
            except Exception as exc:  # pragma: no cover - branch tested via policies
                self._handle_callback_exception(exc)

    def _handle_callback_exception(self, exc: Exception) -> None:
        if self._error_policy == "raise":
            raise exc
        if self._error_policy == "collect":
            with self._lock:
                self._callback_errors.append(exc)
            self._report_callback_error(exc)
            return
        self._report_callback_error(exc)

    def _report_callback_error(self, exc: Exception) -> None:
        if self._callback_error_handler is not None:
            try:
                self._callback_error_handler(exc)
                return
            except Exception:
                _LOGGER.exception("Drip callback_error_handler failed")
        _LOGGER.exception("Drip callback raised", exc_info=exc)

    def _schedule(self, callback: Callable[[], None]) -> None:
        loop = self._get_or_bind_loop()
        if loop and loop.is_running():
            try:
                running = None
                try:
                    running = asyncio.get_running_loop()
                except RuntimeError:
                    running = None
                if running is loop:
                    loop.call_soon(callback)
                else:
                    loop.call_soon_threadsafe(callback)
                return
            except RuntimeError:
                pass
        callback()

    def _require_bound_running_loop(self) -> asyncio.AbstractEventLoop:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                "subscribe_async requires an active running asyncio event loop"
            ) from exc
        loop = self._get_or_bind_loop()
        if loop is None or not loop.is_running() or loop is not running:
            raise RuntimeError(
                "subscribe_async must be called from the drip's bound event loop thread"
            )
        return loop

    def _get_or_bind_loop(self) -> asyncio.AbstractEventLoop | None:
        with self._lock:
            if self._loop is not None:
                return self._loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        with self._lock:
            if self._loop is None:
                self._loop = loop
            return self._loop

    def _has_any_subscribers_unlocked(self) -> bool:
        return bool(self._subs or self._priority_subs or self._async_subs)

    @staticmethod
    def _close_awaitable_if_needed(value: object) -> None:
        if inspect.iscoroutine(value):
            value.close()

    def _is_same_value(self, old: T | None, new: T | None) -> bool:
        if self._elide_policy == "none":
            return False
        if self._elide_policy == "equality":
            return old == new

        if isinstance(old, _SCALAR_TYPES) and isinstance(new, _SCALAR_TYPES):
            return old == new
        return old is new
