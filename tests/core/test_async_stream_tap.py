from __future__ import annotations

import asyncio
import time
from typing import Any

from grip_py.core.atom_tap import create_atom_value_tap
from grip_py.core.async_stream_tap import (
    AsyncStreamRetryConfig,
    AsyncStreamTapParams,
    create_async_stream_multi_tap,
)
from grip_py.core.drip import Drip
from grip_py.core.grok import Grok
from grip_py.core.grip import Grip, GripRegistry


class StreamController:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.cancelled = False

    async def iterable(self, cancel_event: asyncio.Event):
        try:
            while not cancel_event.is_set():
                item = await self.queue.get()
                if item is None:
                    return
                yield item
        finally:
            self.cancelled = True

    def push(self, value: str) -> None:
        self.queue.put_nowait(value)

    def close(self) -> None:
        self.queue.put_nowait(None)


async def _wait_for_value(drip: Drip[Any], expected: Any, timeout: float = 1.0) -> None:
    if drip.get() == expected:
        return
    done = asyncio.Event()

    def on_value(value: Any) -> None:
        if value == expected:
            done.set()

    unsubscribe = drip.subscribe_priority(on_value)
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
    finally:
        unsubscribe()


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not satisfied before timeout")


def test_async_stream_tap_publishes_events_and_aborts_without_listeners() -> None:
    async def scenario() -> None:
        registry = GripRegistry()
        out = registry.add("Out", "")
        grok = Grok(registry)
        controller = StreamController()

        tap = create_async_stream_multi_tap(
            provides=[out],
            request_key_of=lambda _params: "shared",
            subscribe=lambda _params, cancel_event: controller.iterable(cancel_event),
            map_event=lambda _params, event: {out: event},
            cleanup_delay_ms=0,
        )
        grok.main_home_context.register_tap(tap)

        ctx = grok.main_presentation_context.create_child()
        drip = grok.query(out, ctx)
        unsubscribe = drip.subscribe_priority(lambda _value: None)

        controller.push("one")
        await _wait_for_value(drip, "one")

        unsubscribe()
        await _wait_until(lambda: controller.cancelled)

    asyncio.run(scenario())


def test_async_stream_tap_shares_one_stream_for_matching_request_keys() -> None:
    async def scenario() -> None:
        registry = GripRegistry()
        request_id = registry.add("RequestId", "same")
        out = registry.add("Out", "")
        grok = Grok(registry)
        controller = StreamController()
        subscribe_count = 0

        def subscribe(_params: AsyncStreamTapParams, cancel_event: asyncio.Event):
            nonlocal subscribe_count
            subscribe_count += 1
            return controller.iterable(cancel_event)

        tap = create_async_stream_multi_tap(
            provides=[out],
            destination_param_grips=[request_id],
            request_key_of=lambda params: str(params.destination_params[request_id]),
            subscribe=subscribe,
            map_event=lambda _params, event: {out: event},
        )
        grok.main_home_context.register_tap(tap)

        c1 = grok.main_presentation_context.create_child()
        c2 = grok.main_presentation_context.create_child()
        c1.register_tap(create_atom_value_tap(request_id, initial="same"))
        c2.register_tap(create_atom_value_tap(request_id, initial="same"))

        d1 = grok.query(out, c1)
        d2 = grok.query(out, c2)
        d1.subscribe_priority(lambda _value: None)
        d2.subscribe_priority(lambda _value: None)

        controller.push("tick")
        await _wait_for_value(d1, "tick")
        await _wait_for_value(d2, "tick")
        assert subscribe_count == 1

    asyncio.run(scenario())


def test_async_stream_tap_switches_stream_when_destination_params_change() -> None:
    async def scenario() -> None:
        registry = GripRegistry()
        request_id = registry.add("RequestId", "a")
        out = registry.add("Out", "")
        grok = Grok(registry)
        streams = {"a": StreamController(), "b": StreamController()}

        tap = create_async_stream_multi_tap(
            provides=[out],
            destination_param_grips=[request_id],
            request_key_of=lambda params: str(params.destination_params[request_id]),
            subscribe=lambda params, cancel_event: streams[
                str(params.destination_params[request_id])
            ].iterable(cancel_event),
            map_event=lambda params, event: {
                out: f"{params.destination_params[request_id]}:{event}"
            },
            cleanup_delay_ms=0,
        )
        grok.main_home_context.register_tap(tap)

        ctx = grok.main_presentation_context.create_child()
        request_tap = create_atom_value_tap(request_id, initial="a")
        ctx.register_tap(request_tap)
        drip = grok.query(out, ctx)
        drip.subscribe_priority(lambda _value: None)

        streams["a"].push("one")
        await _wait_for_value(drip, "a:one")

        request_tap.set("b")
        await asyncio.sleep(0)
        streams["b"].push("two")
        await _wait_for_value(drip, "b:two")
        assert streams["a"].cancelled

    asyncio.run(scenario())


def test_async_stream_tap_retries_failed_streams_until_detached() -> None:
    async def scenario() -> None:
        registry = GripRegistry()
        out = registry.add("Out", "")
        grok = Grok(registry)
        controller = StreamController()
        subscribe_count = 0

        def subscribe(_params: AsyncStreamTapParams, cancel_event: asyncio.Event):
            nonlocal subscribe_count
            subscribe_count += 1
            if subscribe_count == 1:
                raise RuntimeError("temporary")
            return controller.iterable(cancel_event)

        tap = create_async_stream_multi_tap(
            provides=[out],
            request_key_of=lambda _params: "shared",
            subscribe=subscribe,
            map_event=lambda _params, event: {out: event},
            retry=AsyncStreamRetryConfig(
                initial_delay_ms=1,
                max_delay_ms=1,
                jitter_ratio=0,
            ),
        )
        grok.main_home_context.register_tap(tap)

        ctx = grok.main_presentation_context.create_child()
        drip = grok.query(out, ctx)
        unsubscribe = drip.subscribe_priority(lambda _value: None)
        await asyncio.sleep(0.02)
        assert subscribe_count == 2

        controller.push("after-retry")
        await _wait_for_value(drip, "after-retry")

        unsubscribe()
        await asyncio.sleep(0)

    asyncio.run(scenario())
