import asyncio
import threading
import time

import pytest

from grip_py import Drip


def test_drip_lazy_bind_without_running_loop():
    drip = Drip[int](0)
    assert drip.get() == 0
    drip.next(1)
    assert drip.get() == 1


def test_drip_subscribe_immediate_and_ts_elide():
    drip = Drip[object](None)
    seen: list[object | None] = []
    drip.subscribe_priority(seen.append)

    assert seen == [None]

    drip.next(None)
    assert seen == [None]

    shared = []
    drip.next(shared)
    assert seen[-1] is shared

    drip.next(shared)
    assert seen[-1] is shared
    assert len(seen) == 2

    drip.next([])
    assert len(seen) == 3


def test_drip_regular_subscribe_is_deferred():
    async def scenario():
        drip = Drip[int](0, elide_policy="none")
        seen: list[int | None] = []
        got_update = asyncio.Event()

        def on_value(v: int | None):
            seen.append(v)
            if v == 1:
                got_update.set()

        drip.subscribe(on_value)
        assert seen == [0]

        drip.next(1)
        assert seen == [0]

        await asyncio.wait_for(got_update.wait(), timeout=1.0)
        assert seen == [0, 1]

    asyncio.run(scenario())


def test_drip_first_and_zero_subscriber_callbacks():
    async def scenario():
        drip = Drip[int](0)
        first_count = 0
        zero_count = 0

        def on_first():
            nonlocal first_count
            first_count += 1

        def on_zero():
            nonlocal zero_count
            zero_count += 1

        drip.add_on_first_subscriber(on_first)
        drip.add_on_zero_subscribers(on_zero)

        unsubscribe = drip.subscribe(lambda _: None)
        assert first_count == 1
        assert zero_count == 0

        unsubscribe()
        await asyncio.sleep(0)
        assert zero_count == 1

    asyncio.run(scenario())


def test_drip_error_policy_log_continues():
    errors: list[Exception] = []
    drip = Drip[int](
        0,
        error_policy="log",
        elide_policy="none",
        callback_error_handler=errors.append,
    )

    def on_value(v: int | None):
        if v == 1:
            raise ValueError("boom")

    drip.subscribe_priority(on_value)
    drip.next(1)
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)


def test_drip_error_policy_raise_propagates():
    drip = Drip[int](0, error_policy="raise", elide_policy="none")

    def on_value(v: int | None):
        if v == 1:
            raise RuntimeError("fail-fast")

    drip.subscribe_priority(on_value)
    with pytest.raises(RuntimeError):
        drip.next(1)


def test_drip_error_policy_collect():
    drip = Drip[int](
        0,
        error_policy="collect",
        elide_policy="none",
        callback_error_handler=lambda _: None,
    )

    def on_value(v: int | None):
        if v == 2:
            raise ValueError("collect-me")

    drip.subscribe_priority(on_value)
    drip.next(2)

    errors = drip.get_callback_errors()
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)


def test_drip_next_threadsafe_from_worker_thread():
    async def scenario():
        drip = Drip[int](0, elide_policy="none")
        seen: list[int | None] = []
        done = asyncio.Event()

        def on_value(v: int | None):
            seen.append(v)
            if v == 5:
                done.set()

        drip.subscribe(on_value)

        def worker():
            for i in range(1, 6):
                drip.next_threadsafe(i)

        t = threading.Thread(target=worker)
        t.start()

        await asyncio.wait_for(done.wait(), timeout=1.0)
        t.join()
        assert seen[-1] == 5

    asyncio.run(scenario())


def test_drip_performance_messages_in_0p1_seconds():
    drip = Drip[int](0, error_policy="raise", elide_policy="none")
    callback_count = 0

    def on_value(_: int | None):
        nonlocal callback_count
        callback_count += 1

    unsubscribe = drip.subscribe_priority(on_value)
    initial_callbacks = callback_count

    start = time.perf_counter()
    sent = 0
    while time.perf_counter() - start < 0.1:
        sent += 1
        drip.next(sent)
    elapsed = time.perf_counter() - start

    delivered = callback_count - initial_callbacks
    print(f"drip throughput: {sent} messages delivered in {elapsed:.6f}s")

    assert sent > 0
    assert delivered == sent
    unsubscribe()


def test_drip_subscribe_rejects_async_callback_in_sync_apis():
    drip = Drip[int](0)

    async def async_cb(_: int | None):
        return None

    with pytest.raises(TypeError):
        drip.subscribe(async_cb)
    with pytest.raises(TypeError):
        drip.subscribe_priority(async_cb)


def test_drip_subscribe_async_immediate_and_ordered_delivery():
    async def scenario():
        drip = Drip[int](0, elide_policy="none")
        seen: list[int | None] = []
        done = asyncio.Event()

        async def on_value(v: int | None):
            seen.append(v)
            if len(seen) == 4:
                done.set()

        unsubscribe = drip.subscribe_async(on_value)
        drip.next(1)
        drip.next(2)
        drip.next(3)

        await asyncio.wait_for(done.wait(), timeout=1.0)
        assert seen == [0, 1, 2, 3]
        unsubscribe()

    asyncio.run(scenario())


def test_drip_subscribe_async_unsubscribe_stops_updates():
    async def scenario():
        drip = Drip[int](0, elide_policy="none")
        seen: list[int | None] = []

        async def on_value(v: int | None):
            seen.append(v)

        unsubscribe = drip.subscribe_async(on_value)
        await asyncio.sleep(0)
        assert seen == [0]

        unsubscribe()
        drip.next(1)
        await asyncio.sleep(0.01)
        assert seen == [0]

    asyncio.run(scenario())


def test_drip_subscribe_async_error_policy_collect():
    async def scenario():
        drip = Drip[int](
            0,
            error_policy="collect",
            elide_policy="none",
            callback_error_handler=lambda _: None,
        )

        async def on_value(v: int | None):
            if v == 1:
                raise ValueError("async-boom")

        unsubscribe = drip.subscribe_async(on_value)
        drip.next(1)
        await asyncio.sleep(0.01)
        unsubscribe()

        errors = drip.get_callback_errors()
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

    asyncio.run(scenario())
