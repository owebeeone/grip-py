"""Shared asyncio loop used by Grok internal async-first subscriptions."""

from __future__ import annotations

import atexit
import asyncio
import threading
from dataclasses import dataclass


@dataclass(slots=True, init=False, eq=False)
class _AsyncLoopThread:
    """Dedicated background thread that runs a single asyncio loop."""

    _ready: threading.Event
    _thread: threading.Thread
    _loop: asyncio.AbstractEventLoop | None
    _closed: bool

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._loop = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._run_loop,
            name="grip-py-async-loop",
            daemon=True,
        )
        self._thread.start()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()
        pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    def get_loop(self) -> asyncio.AbstractEventLoop:
        self._ready.wait()
        loop = self._loop
        if loop is None:  # pragma: no cover - defensive guard
            raise RuntimeError("Async loop failed to initialize")
        return loop

    def shutdown(self, timeout_s: float = 1.0) -> None:
        if self._closed:
            return
        self._closed = True

        loop = self._loop
        if loop is None:
            return
        if loop.is_running():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if self._thread.is_alive():
            self._thread.join(timeout=timeout_s)


_SHARED_LOOP_THREAD: _AsyncLoopThread | None = None
_SHARED_LOOP_LOCK = threading.Lock()


def get_shared_async_loop() -> asyncio.AbstractEventLoop:
    """Return the shared background asyncio loop for Grok internals."""
    global _SHARED_LOOP_THREAD
    with _SHARED_LOOP_LOCK:
        if _SHARED_LOOP_THREAD is None:
            _SHARED_LOOP_THREAD = _AsyncLoopThread()
        return _SHARED_LOOP_THREAD.get_loop()


def _shutdown_shared_async_loop() -> None:
    global _SHARED_LOOP_THREAD
    with _SHARED_LOOP_LOCK:
        thread = _SHARED_LOOP_THREAD
        _SHARED_LOOP_THREAD = None
    if thread is not None:
        thread.shutdown()


atexit.register(_shutdown_shared_async_loop)
