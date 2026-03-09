"""Priority task queue for Grok internal scheduling."""

from __future__ import annotations

import asyncio
import heapq
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

TaskCallback = Callable[[], None]


class TaskHandle(Protocol):
    """Handle for a queued task."""

    def cancel(self) -> bool: ...

    def is_running(self) -> bool: ...

    def is_cancelled(self) -> bool: ...

    def is_pending(self) -> bool: ...

    def is_completed(self) -> bool: ...


class TaskHandleContainer(Protocol):
    """Container that tracks task handles."""

    def add(self, handle: TaskHandle) -> None: ...

    def remove(self, handle: TaskHandle) -> None: ...


@dataclass(slots=True, eq=False)
class TaskHandleHolder:
    """Simple task-handle holder."""

    _handles: list[_TaskHandleImpl] = field(default_factory=list)

    def add(self, handle: TaskHandle) -> None:
        if isinstance(handle, _TaskHandleImpl):
            self._handles.append(handle)

    def remove(self, handle: TaskHandle) -> None:
        if isinstance(handle, _TaskHandleImpl):
            try:
                self._handles.remove(handle)
            except ValueError:
                pass

    @property
    def size(self) -> int:
        return len(self._handles)

    def get_handles(self) -> tuple[TaskHandle, ...]:
        return tuple(self._handles)

    def cancel_all(self) -> None:
        while self._handles:
            self._handles[0].cancel()


@dataclass(order=True)
class _Task:
    """Internal queued task."""

    priority: int
    sequence: int
    callback: TaskCallback = field(compare=False)
    state: str = field(default="pending", compare=False)
    handle: _TaskHandleImpl | None = field(default=None, compare=False)


@dataclass(slots=True, eq=False)
class _TaskHandleImpl:
    """Concrete task handle."""

    _task: _Task
    _holder: TaskHandleContainer
    _removed: bool = False

    def cancel(self) -> bool:
        if self._task.state != "pending":
            return False
        self._task.state = "cancelled"
        self._notify_no_longer_pending()
        return True

    def is_running(self) -> bool:
        return self._task.state == "running"

    def is_cancelled(self) -> bool:
        return self._task.state == "cancelled"

    def is_pending(self) -> bool:
        return self._task.state == "pending"

    def is_completed(self) -> bool:
        return self._task.state == "completed"

    def _notify_no_longer_pending(self) -> None:
        if self._removed:
            return
        try:
            self._holder.remove(self)
        finally:
            self._removed = True


@dataclass(slots=True, init=False, eq=False)
class TaskQueue:
    """Priority task queue with deterministic ordering."""

    _heap: list[_Task]
    _next_sequence: int
    _scheduled: bool
    _is_flushing: bool
    _auto_flush: bool
    _loop: asyncio.AbstractEventLoop | None
    _lock: threading.RLock

    def __init__(
        self,
        *,
        auto_flush: bool = True,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._heap: list[_Task] = []
        self._next_sequence = 1
        self._scheduled = False
        self._is_flushing = False
        self._auto_flush = auto_flush
        self._loop = loop
        self._lock = threading.RLock()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._heap)

    def submit(
        self,
        callback: TaskCallback,
        priority: int = 0,
        holder: TaskHandleContainer | None = None,
    ) -> None:
        with self._lock:
            task = _Task(priority=priority, sequence=self._next_sequence, callback=callback)
            self._next_sequence += 1
            if holder is not None:
                task.handle = _TaskHandleImpl(task, holder)
                holder.add(task.handle)
            heapq.heappush(self._heap, task)
        self._schedule_flush_if_needed()

    def submit_weak_owner(
        self,
        owner_ref: Callable[[], object | None],
        callback: Callable[[object], None],
        priority: int = 0,
        holder: TaskHandleContainer | None = None,
    ) -> None:
        """Schedule callback only if owner still exists at execution time."""

        def run() -> None:
            owner = owner_ref()
            if owner is None:
                return
            callback(owner)

        self.submit(run, priority=priority, holder=holder)

    def flush(self) -> None:
        with self._lock:
            if self._is_flushing:
                return
            self._is_flushing = True
        try:
            while True:
                with self._lock:
                    if not self._heap:
                        self._scheduled = False
                        break
                    task = heapq.heappop(self._heap)

                if task.state == "cancelled":
                    continue

                if task.handle is not None and task.state == "pending":
                    task.handle._notify_no_longer_pending()

                try:
                    task.state = "running"
                    task.callback()
                except Exception:
                    # Keep queue draining; surface error through event loop later.
                    loop = self._loop
                    if loop is None:
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            loop = None
                    if loop is not None and loop.is_running():
                        loop.call_exception_handler(
                            {
                                "message": "TaskQueue task raised exception",
                            }
                        )
                finally:
                    if task.state != "cancelled":
                        task.state = "completed"
        finally:
            with self._lock:
                self._is_flushing = False

    def cancel_scheduled_flush(self) -> None:
        with self._lock:
            self._scheduled = False

    def _schedule_flush_if_needed(self) -> None:
        with self._lock:
            if not self._auto_flush or self._scheduled or self._is_flushing:
                return
            self._scheduled = True

        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                self._loop = loop

        if loop is None or not loop.is_running():
            # No loop available; caller can invoke flush() explicitly.
            return

        try:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is loop:
                loop.call_soon(self.flush)
            else:
                loop.call_soon_threadsafe(self.flush)
        except RuntimeError:
            with self._lock:
                self._scheduled = False
