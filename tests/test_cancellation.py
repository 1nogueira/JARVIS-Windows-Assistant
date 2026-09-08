from __future__ import annotations

import asyncio

import pytest

from backend.core.cancellation import TaskManager


async def _wait_forever() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_unknown_request_id_does_not_cancel_other_tasks() -> None:
    manager = TaskManager()
    task_a = asyncio.create_task(_wait_forever())
    task_b = asyncio.create_task(_wait_forever())
    await manager.track("a", task_a)
    await manager.track("b", task_b)

    assert await manager.cancel("missing") == 0
    assert not task_a.done()
    assert not task_b.done()

    assert await manager.cancel("a") == 1
    with pytest.raises(asyncio.CancelledError):
        await task_a
    assert not task_b.done()
    await manager.cancel("b")
    with pytest.raises(asyncio.CancelledError):
        await task_b


@pytest.mark.asyncio
async def test_none_request_id_is_explicit_global_cancel() -> None:
    manager = TaskManager()
    tasks = [asyncio.create_task(_wait_forever()) for _ in range(2)]
    await manager.track("a", tasks[0])
    await manager.track("b", tasks[1])

    assert await manager.cancel() == 2
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)


@pytest.mark.asyncio
async def test_duplicate_active_request_id_is_rejected() -> None:
    manager = TaskManager()
    first = asyncio.create_task(_wait_forever())
    second = asyncio.create_task(_wait_forever())
    await manager.track("same", first)
    with pytest.raises(ValueError, match="request_id"):
        await manager.track("same", second)
    second.cancel()
    await manager.cancel("same")
    await asyncio.gather(first, second, return_exceptions=True)
