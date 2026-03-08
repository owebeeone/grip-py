"""Async tap implementations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
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
RetryOnError = Callable[[Exception], bool]


@dataclass(slots=True, frozen=True)
class RetryConfig:
    """Exponential backoff retry configuration."""

    max_retries: int = 3
    initial_delay_ms: int = 1000
    max_delay_ms: int = 30000
    backoff_multiplier: float = 2.0
    retry_on_error: RetryOnError | None = None


@dataclass(slots=True, frozen=True)
class RequestState:
    """Snapshot of a destination async lifecycle state."""

    type: str
    retry_at: float | None = None
    initiated_at: float | None = None
    retrieved_at: float | None = None
    failed_at: float | None = None
    refresh_initiated_at: float | None = None
    error: Exception | None = None


@dataclass(slots=True, frozen=True)
class StateHistoryEntry:
    """Historical state transition entry."""

    state: RequestState
    timestamp: float
    request_key: str | None
    transition_reason: str | None = None


@dataclass(slots=True, frozen=True)
class AsyncRequestState:
    """Public state object published through optional state grip."""

    state: RequestState
    request_key: str | None
    has_listeners: bool
    history: tuple[StateHistoryEntry, ...]


@dataclass(slots=True)
class AsyncTapController:
    """Per-destination control plane for async taps."""

    _retry: Callable[[bool], None]
    _refresh: Callable[[bool], None]
    _reset: Callable[[], None]
    _cancel_retry: Callable[[], None]
    _abort: Callable[[], None]

    def retry(self, force_refetch: bool = False) -> None:
        self._retry(force_refetch)

    def refresh(self, force_refetch: bool = False) -> None:
        self._refresh(force_refetch)

    def reset(self) -> None:
        self._reset()

    def cancel_retry(self) -> None:
        self._cancel_retry()

    def abort(self) -> None:
        self._abort()


@dataclass(slots=True, frozen=True)
class _CacheEntry:
    values: dict[Grip[Any], Any]
    expires_at_monotonic: float


@dataclass(slots=True)
class _DestinationState:
    history_size: int = 10
    current_state: RequestState = field(
        default_factory=lambda: RequestState(type="idle", retry_at=None)
    )
    request_key: str | None = None
    history: list[StateHistoryEntry] = field(default_factory=list)
    controller: AsyncTapController | None = None


@dataclass(init=False, eq=False)
class AsyncTap(BaseTap):
    """Destination-aware async tap with request sharing and optional state grips."""

    _fetcher: AsyncFetcher
    _request_key_of: RequestKeyFn | None
    _latest_only: bool
    _cache_ttl_ms: int
    _cleanup_delay_ms: int
    _deadline_ms: int | None
    _retry: RetryConfig | None
    _refresh_before_expiry_ms: int
    _keep_stale_data_on_transition: bool
    _history_size: int
    _state_grip: Grip[Any] | None
    _controller_grip: Grip[Any] | None

    _inflight_by_key: dict[str, asyncio.Task[dict[Grip[Any], Any]]]
    _cache_by_key: dict[str, _CacheEntry]
    _dest_key_by_id: dict[str, str]
    _dest_context_by_id: dict[str, GripContext]
    _key_dest_ids: dict[str, set[str]]
    _cleanup_tasks: dict[str, asyncio.Task[None]]
    _retry_tasks_by_key: dict[str, asyncio.Task[None]]
    _refresh_tasks_by_key: dict[str, asyncio.Task[None]]
    _retry_attempt_by_key: dict[str, int]
    _state_by_dest_id: dict[str, _DestinationState]

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
        deadline_ms: int | None = None,
        retry: RetryConfig | None = None,
        history_size: int = 10,
        refresh_before_expiry_ms: int = 0,
        keep_stale_data_on_transition: bool = False,
        state_grip: Grip[AsyncRequestState] | None = None,
        controller_grip: Grip[AsyncTapController] | None = None,
        fetcher: AsyncFetcher,
    ):
        provides_list = list(provides)
        if state_grip is not None and state_grip not in provides_list:
            provides_list.append(state_grip)
        if controller_grip is not None and controller_grip not in provides_list:
            provides_list.append(controller_grip)

        super().__init__(
            provides=provides_list,
            destination_param_grips=destination_param_grips,
            home_param_grips=home_param_grips,
        )
        self._fetcher = fetcher
        self._request_key_of = request_key_of
        self._latest_only = latest_only
        self._cache_ttl_ms = max(0, cache_ttl_ms)
        self._cleanup_delay_ms = max(0, cleanup_delay_ms)
        self._deadline_ms = max(0, deadline_ms) if deadline_ms is not None else None
        self._retry = retry
        self._history_size = max(0, history_size)
        self._refresh_before_expiry_ms = max(0, refresh_before_expiry_ms)
        self._keep_stale_data_on_transition = keep_stale_data_on_transition
        self._state_grip = state_grip
        self._controller_grip = controller_grip

        self._inflight_by_key = {}
        self._cache_by_key = {}
        self._dest_key_by_id = {}
        self._dest_context_by_id = {}
        self._key_dest_ids = {}
        self._cleanup_tasks = {}
        self._retry_tasks_by_key = {}
        self._refresh_tasks_by_key = {}
        self._retry_attempt_by_key = {}
        self._state_by_dest_id = {}

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

        for task in tuple(self._retry_tasks_by_key.values()):
            if not task.done():
                task.cancel()
        self._retry_tasks_by_key.clear()

        for task in tuple(self._refresh_tasks_by_key.values()):
            if not task.done():
                task.cancel()
        self._refresh_tasks_by_key.clear()
        self._retry_attempt_by_key.clear()

        self._cache_by_key.clear()
        self._dest_key_by_id.clear()
        self._dest_context_by_id.clear()
        self._key_dest_ids.clear()
        self._state_by_dest_id.clear()

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

    def _produce_for_destination(
        self,
        ctx: GripContext,
        *,
        force_refetch: bool = False,
    ) -> None:
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

        state = self._state_by_dest_id.setdefault(
            destination_id,
            _DestinationState(history_size=self._history_size),
        )
        state.request_key = key
        if state.controller is None:
            state.controller = self._create_controller(destination_id)
        self._publish_controller(destination_id)

        if not force_refetch and self._try_publish_cached(ctx, key):
            self._set_state(
                destination_id,
                RequestState(type="success", retrieved_at=time.time()),
                reason="cache_hit",
            )
            return

        task = self._inflight_by_key.get(key)
        if task is not None and not task.done():
            if self._latest_only:
                self._set_fetching_state(destination_id, reason="joined_inflight")
                return
            task.cancel()

        self._set_fetching_state(destination_id, reason="request_started")
        self._inflight_by_key[key] = loop.create_task(
            self._run_fetch_for_key(key, request_params)
        )

    async def _run_fetch_for_key(
        self,
        key: str,
        request_params: AsyncTapParams,
    ) -> dict[Grip[Any], Any]:
        values: dict[Grip[Any], Any] = {}
        error: Exception | None = None

        try:
            fetch_coro = self._fetcher(
                AsyncTapParams(
                    destination_params=dict(request_params.destination_params),
                    home_params=dict(request_params.home_params),
                )
            )
            if self._deadline_ms is not None and self._deadline_ms > 0:
                values = await asyncio.wait_for(fetch_coro, self._deadline_ms / 1000.0)
            else:
                values = await fetch_coro
        except asyncio.CancelledError:
            values = {}
        except Exception as exc:  # pragma: no cover - covered in retry tests
            error = exc
            values = {}

        try:
            if values and self._cache_ttl_ms > 0:
                ttl_seconds = self._cache_ttl_ms / 1000.0
                self._cache_by_key[key] = _CacheEntry(
                    values=dict(values),
                    expires_at_monotonic=time.monotonic() + ttl_seconds,
                )

            destination_ids = tuple(self._key_dest_ids.get(key, ()))
            if error is None:
                self._clear_retry_for_key(key)
                self._schedule_refresh_for_key(key)
                for destination_id in destination_ids:
                    if self._latest_only and self._dest_key_by_id.get(destination_id) != key:
                        continue
                    destination = self._dest_context_by_id.get(destination_id)
                    if destination is not None and values:
                        self.publish(values, dest_context=destination)
                    self._set_state(
                        destination_id,
                        RequestState(type="success", retrieved_at=time.time()),
                        reason="request_success",
                    )
                return values

            self._cancel_refresh_for_key(key)
            for destination_id in destination_ids:
                if self._latest_only and self._dest_key_by_id.get(destination_id) != key:
                    continue
                previous_state = self._state_by_dest_id.get(destination_id)
                previous = (
                    previous_state.current_state
                    if previous_state is not None
                    else RequestState(type="idle")
                )
                now = time.time()
                if self._has_data_state(previous) and self._keep_stale_data_on_transition:
                    next_state = RequestState(
                        type="stale-with-error",
                        retrieved_at=previous.retrieved_at or now,
                        error=error,
                        failed_at=now,
                        retry_at=self._current_retry_at_for_key(key),
                    )
                else:
                    next_state = RequestState(
                        type="error",
                        error=error,
                        failed_at=now,
                        retry_at=self._current_retry_at_for_key(key),
                    )
                self._set_state(
                    destination_id,
                    next_state,
                    reason="request_error",
                )

            if error is not None:
                self._schedule_retry_for_key(key, error)
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
        self._state_by_dest_id.pop(destination_id, None)

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

        retry_task = self._retry_tasks_by_key.pop(key, None)
        if retry_task is not None and not retry_task.done():
            retry_task.cancel()
        self._retry_attempt_by_key.pop(key, None)
        self._cancel_refresh_for_key(key)

        cleanup_task = self._cleanup_tasks.pop(key, None)
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()

    def _create_controller(self, destination_id: str) -> AsyncTapController:
        return AsyncTapController(
            _retry=lambda force_refetch=False, did=destination_id: self._controller_retry(
                did, force_refetch
            ),
            _refresh=lambda force_refetch=False, did=destination_id: self._controller_refresh(
                did, force_refetch
            ),
            _reset=lambda did=destination_id: self._controller_reset(did),
            _cancel_retry=lambda did=destination_id: self._controller_cancel_retry(did),
            _abort=lambda did=destination_id: self._controller_abort(did),
        )

    def _controller_retry(self, destination_id: str, force_refetch: bool = False) -> None:
        self._controller_abort(destination_id)
        self._controller_cancel_retry(destination_id)
        destination = self._dest_context_by_id.get(destination_id)
        if destination is None:
            return
        self._produce_for_destination(destination, force_refetch=force_refetch)

    def _controller_refresh(self, destination_id: str, force_refetch: bool = False) -> None:
        destination = self._dest_context_by_id.get(destination_id)
        if destination is None:
            return
        self._produce_for_destination(destination, force_refetch=force_refetch)

    def _controller_reset(self, destination_id: str) -> None:
        self._controller_abort(destination_id)
        self._controller_cancel_retry(destination_id)

        destination = self._dest_context_by_id.get(destination_id)
        if destination is None:
            return

        state = self._state_by_dest_id.setdefault(destination_id, _DestinationState())
        key = self._dest_key_by_id.get(destination_id)
        if key is not None:
            self._cancel_refresh_for_key(key)
        state.request_key = None
        self._set_state(
            destination_id,
            RequestState(type="idle", retry_at=None),
            reason="controller_reset",
        )

        reset_values = {
            grip: grip.default
            for grip in self.provides
            if grip not in {self._state_grip, self._controller_grip}
        }
        if reset_values:
            self.publish(reset_values, dest_context=destination)

    def _controller_cancel_retry(self, destination_id: str) -> None:
        key = self._dest_key_by_id.get(destination_id)
        if key is None:
            return
        task = self._retry_tasks_by_key.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
        self._clear_retry_at_for_key(key)

    def _controller_abort(self, destination_id: str) -> None:
        key = self._dest_key_by_id.get(destination_id)
        if key is None:
            return
        task = self._inflight_by_key.get(key)
        if task is not None and not task.done():
            task.cancel()

    def _publish_state(self, destination_id: str) -> None:
        if self._state_grip is None:
            return
        destination = self._dest_context_by_id.get(destination_id)
        if destination is None:
            return
        snapshot = self.get_request_state(destination)
        self.publish({self._state_grip: snapshot}, dest_context=destination)

    def _publish_controller(self, destination_id: str) -> None:
        if self._controller_grip is None:
            return
        destination = self._dest_context_by_id.get(destination_id)
        if destination is None:
            return
        controller = self._state_by_dest_id[destination_id].controller
        if controller is None:
            return
        self.publish({self._controller_grip: controller}, dest_context=destination)

    @staticmethod
    def _has_data_state(state: RequestState) -> bool:
        return state.type in {"success", "stale-while-revalidate", "stale-with-error"}

    def _publish_data_defaults(self, destination: GripContext) -> None:
        defaults = {
            grip: grip.default
            for grip in self.provides
            if grip not in {self._state_grip, self._controller_grip}
        }
        if defaults:
            self.publish(defaults, dest_context=destination)

    def _set_fetching_state(self, destination_id: str, *, reason: str) -> None:
        state = self._state_by_dest_id.setdefault(destination_id, _DestinationState())
        now = time.time()
        previous = state.current_state
        destination = self._dest_context_by_id.get(destination_id)

        if self._has_data_state(previous) and self._keep_stale_data_on_transition:
            retrieved_at = previous.retrieved_at or now
            next_state = RequestState(
                type="stale-while-revalidate",
                retrieved_at=retrieved_at,
                refresh_initiated_at=now,
            )
        else:
            if (
                destination is not None
                and self._has_data_state(previous)
                and not self._keep_stale_data_on_transition
            ):
                self._publish_data_defaults(destination)
            next_state = RequestState(type="loading", initiated_at=now)

        self._set_state(destination_id, next_state, reason=reason)

    def _set_state(
        self,
        destination_id: str,
        next_state: RequestState,
        *,
        reason: str | None = None,
    ) -> None:
        state = self._state_by_dest_id.setdefault(
            destination_id,
            _DestinationState(history_size=self._history_size),
        )
        if state.history_size > 0:
            history_entry = StateHistoryEntry(
                state=state.current_state,
                timestamp=time.time(),
                request_key=state.request_key,
                transition_reason=reason,
            )
            state.history.append(history_entry)
            if len(state.history) > state.history_size:
                state.history.pop(0)
        state.current_state = next_state
        self._publish_state(destination_id)

    def _current_retry_at_for_key(self, key: str) -> float | None:
        for destination_id in self._key_dest_ids.get(key, ()):
            state = self._state_by_dest_id.get(destination_id)
            if state is not None and state.current_state.retry_at is not None:
                return state.current_state.retry_at
        return None

    def _clear_retry_at_for_key(self, key: str) -> None:
        for destination_id in tuple(self._key_dest_ids.get(key, ())):
            state = self._state_by_dest_id.get(destination_id)
            if state is None:
                continue
            if state.current_state.retry_at is None:
                continue
            self._set_state(
                destination_id,
                RequestState(
                    type=state.current_state.type,
                    initiated_at=state.current_state.initiated_at,
                    retrieved_at=state.current_state.retrieved_at,
                    failed_at=state.current_state.failed_at,
                    refresh_initiated_at=state.current_state.refresh_initiated_at,
                    error=state.current_state.error,
                    retry_at=None,
                ),
                reason="retry_cleared",
            )

    def _clear_retry_for_key(self, key: str) -> None:
        task = self._retry_tasks_by_key.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
        self._retry_attempt_by_key.pop(key, None)
        self._clear_retry_at_for_key(key)

    def _cancel_refresh_for_key(self, key: str) -> None:
        task = self._refresh_tasks_by_key.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    def _schedule_refresh_for_key(self, key: str) -> None:
        if self._cache_ttl_ms <= 0:
            return
        if self._refresh_before_expiry_ms <= 0:
            return

        delay_ms = self._cache_ttl_ms - self._refresh_before_expiry_ms
        if delay_ms < 0:
            delay_ms = 0

        self._cancel_refresh_for_key(key)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def run_refresh() -> None:
            try:
                await asyncio.sleep(delay_ms / 1000.0)
            except asyncio.CancelledError:
                return
            destination_ids = tuple(self._key_dest_ids.get(key, ()))
            for destination_id in destination_ids:
                destination = self._dest_context_by_id.get(destination_id)
                if destination is not None:
                    self._produce_for_destination(destination, force_refetch=True)

        self._refresh_tasks_by_key[key] = loop.create_task(run_refresh())

    def _schedule_retry_for_key(self, key: str, error: Exception) -> None:
        config = self._retry
        if config is None:
            return

        if config.retry_on_error is not None and not config.retry_on_error(error):
            return

        attempt = self._retry_attempt_by_key.get(key, 0)
        if attempt >= max(0, config.max_retries):
            return

        delay_ms = min(
            int(config.initial_delay_ms * (config.backoff_multiplier ** attempt)),
            max(0, config.max_delay_ms),
        )
        retry_at = time.time() + (delay_ms / 1000.0)
        self._retry_attempt_by_key[key] = attempt + 1

        for destination_id in tuple(self._key_dest_ids.get(key, ())):
            state = self._state_by_dest_id.get(destination_id)
            if state is None:
                continue
            self._set_state(
                destination_id,
                RequestState(
                    type=state.current_state.type,
                    initiated_at=state.current_state.initiated_at,
                    retrieved_at=state.current_state.retrieved_at,
                    failed_at=state.current_state.failed_at,
                    refresh_initiated_at=state.current_state.refresh_initiated_at,
                    error=state.current_state.error,
                    retry_at=retry_at,
                ),
                reason="retry_scheduled",
            )

        existing = self._retry_tasks_by_key.get(key)
        if existing is not None and not existing.done():
            existing.cancel()

        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def run_retry() -> None:
            try:
                await asyncio.sleep(delay_ms / 1000.0)
            except asyncio.CancelledError:
                return
            destination_ids = tuple(self._key_dest_ids.get(key, ()))
            for destination_id in destination_ids:
                destination = self._dest_context_by_id.get(destination_id)
                if destination is not None:
                    self._produce_for_destination(destination, force_refetch=True)

        self._retry_tasks_by_key[key] = loop.create_task(run_retry())

    def get_request_state(self, dest_context: GripContext) -> AsyncRequestState:
        state = self._state_by_dest_id.get(dest_context.id)
        if state is None:
            return AsyncRequestState(
                state=RequestState(type="idle", retry_at=None),
                request_key=None,
                has_listeners=False,
                history=(),
            )
        return AsyncRequestState(
            state=state.current_state,
            request_key=state.request_key,
            has_listeners=dest_context.id in self._dest_key_by_id,
            history=tuple(state.history),
        )


def create_async_tap(
    *,
    provides: Iterable[Grip[Any]],
    destination_param_grips: Iterable[Grip[Any]] | None = None,
    home_param_grips: Iterable[Grip[Any]] | None = None,
    request_key_of: RequestKeyFn | None = None,
    latest_only: bool = True,
    cache_ttl_ms: int = 0,
    cleanup_delay_ms: int = 1000,
    deadline_ms: int | None = None,
    retry: RetryConfig | None = None,
    history_size: int = 10,
    refresh_before_expiry_ms: int = 0,
    keep_stale_data_on_transition: bool = False,
    state_grip: Grip[AsyncRequestState] | None = None,
    controller_grip: Grip[AsyncTapController] | None = None,
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
        deadline_ms=deadline_ms,
        retry=retry,
        history_size=history_size,
        refresh_before_expiry_ms=refresh_before_expiry_ms,
        keep_stale_data_on_transition=keep_stale_data_on_transition,
        state_grip=state_grip,
        controller_grip=controller_grip,
        fetcher=fetcher,
    )
