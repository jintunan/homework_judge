import asyncio

from homework_judge.jobs.manager import JobManager


async def test_completed_callback_does_not_remove_replacement_task() -> None:
    manager = JobManager()
    release = asyncio.Event()

    async def completed() -> None:
        return

    async def replacement() -> None:
        await release.wait()

    assert await manager.start("same-key", completed())
    await asyncio.sleep(0)
    assert await manager.start("same-key", replacement())
    await asyncio.sleep(0)
    assert manager.is_running("same-key")
    release.set()
    await manager.close()


async def test_cancel_waits_for_only_the_named_jobs() -> None:
    manager = JobManager()
    first_cleanup = asyncio.Event()
    second_release = asyncio.Event()

    async def first() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            first_cleanup.set()

    async def second() -> None:
        await second_release.wait()

    assert await manager.start("task-a", first())
    assert await manager.start("task-b", second())
    await asyncio.sleep(0)
    assert await manager.cancel(["task-a", "missing"]) == 1
    assert first_cleanup.is_set()
    assert not manager.is_running("task-a")
    assert manager.is_running("task-b")
    second_release.set()
    await manager.close()
