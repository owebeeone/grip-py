import asyncio

from grip_py.core.async_tap import create_async_tap
from grip_py.core.grok import Grok
from grip_py.core.grip import GripRegistry


def test_async_tap_fetch_and_publish():
    async def scenario():
        registry = GripRegistry()
        out = registry.add("Out", 0)
        grok = Grok(registry)

        async def fetcher(ctx):
            await asyncio.sleep(0.01)
            return {out: 123}

        tap = create_async_tap(provides=[out], fetcher=fetcher)
        grok.register_tap(tap)

        ctx = grok.main_presentation_context.create_child()
        drip = grok.query(out, ctx)

        done = asyncio.Event()

        def on_value(v):
            if v == 123:
                done.set()

        drip.subscribe_priority(on_value)
        await asyncio.wait_for(done.wait(), timeout=1.0)
        assert drip.get() == 123

    asyncio.run(scenario())
