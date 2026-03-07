import asyncio

from grip_py import Drip, watch_drip


def test_watch_drip_emit_initial_and_updates():
    async def scenario():
        drip = Drip[int](10, elide_policy="none")
        updates: list[int | None] = []

        async def consume():
            async for value in watch_drip(
                drip,
                emit_initial=True,
                priority=True,
                queue_size=8,
            ):
                updates.append(value)
                if len(updates) == 3:
                    break

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        drip.next(11)
        drip.next(12)
        await asyncio.wait_for(task, timeout=1.0)

        assert updates == [10, 11, 12]

    asyncio.run(scenario())


def test_watch_drip_drop_oldest_queue_size_one():
    async def scenario():
        drip = Drip[int](0, elide_policy="none")
        updates: list[int | None] = []

        async def consume():
            async for value in watch_drip(
                drip,
                emit_initial=False,
                priority=True,
                queue_size=1,
                overflow="drop_oldest",
            ):
                updates.append(value)
                if len(updates) == 2:
                    break
                await asyncio.sleep(0.05)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)

        drip.next(1)
        await asyncio.sleep(0.01)
        drip.next(2)
        drip.next(3)
        drip.next(4)

        await asyncio.wait_for(task, timeout=1.0)
        assert updates == [1, 4]

    asyncio.run(scenario())


def test_watch_drip_drop_newest_queue_size_one():
    async def scenario():
        drip = Drip[int](0, elide_policy="none")
        updates: list[int | None] = []

        async def consume():
            async for value in watch_drip(
                drip,
                emit_initial=False,
                priority=True,
                queue_size=1,
                overflow="drop_newest",
            ):
                updates.append(value)
                if len(updates) == 2:
                    break
                await asyncio.sleep(0.05)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)

        drip.next(1)
        await asyncio.sleep(0.01)
        drip.next(2)
        drip.next(3)
        drip.next(4)

        await asyncio.wait_for(task, timeout=1.0)
        assert updates == [1, 2]

    asyncio.run(scenario())
