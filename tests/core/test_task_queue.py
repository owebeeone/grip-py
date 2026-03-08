import asyncio

from grip_py.core.task_queue import TaskHandleHolder, TaskQueue


def test_task_queue_priority_ordering():
    queue = TaskQueue(auto_flush=False)
    seen: list[str] = []

    queue.submit(lambda: seen.append("mid"), priority=5)
    queue.submit(lambda: seen.append("high"), priority=0)
    queue.submit(lambda: seen.append("low"), priority=10)
    queue.flush()

    assert seen == ["high", "mid", "low"]


def test_task_queue_fifo_with_equal_priority():
    queue = TaskQueue(auto_flush=False)
    seen: list[int] = []
    for i in range(5):
        queue.submit(lambda v=i: seen.append(v), priority=1)
    queue.flush()
    assert seen == [0, 1, 2, 3, 4]


def test_task_queue_cancel_pending_task():
    queue = TaskQueue(auto_flush=False)
    holder = TaskHandleHolder()
    seen: list[str] = []

    queue.submit(lambda: seen.append("run"), priority=0, holder=holder)
    handles = holder.get_handles()
    assert len(handles) == 1
    assert handles[0].cancel() is True

    queue.flush()
    assert seen == []


def test_task_queue_cancel_all():
    queue = TaskQueue(auto_flush=False)
    holder = TaskHandleHolder()
    seen: list[int] = []

    for i in range(4):
        queue.submit(lambda v=i: seen.append(v), holder=holder)

    holder.cancel_all()
    queue.flush()
    assert seen == []


def test_task_queue_auto_flush_asyncio_loop():
    async def scenario():
        queue = TaskQueue(auto_flush=True)
        seen: list[str] = []
        queue.submit(lambda: seen.append("done"))
        await asyncio.sleep(0)
        assert seen == ["done"]

    asyncio.run(scenario())
