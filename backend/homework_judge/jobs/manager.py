from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine, Iterable
from typing import Any

from ..observability import bind_log_context, log_event

LOGGER = logging.getLogger("homework_judge.jobs")


class JobManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start(self, task_id: str, work: Coroutine[Any, Any, None]) -> bool:
        async with self._lock:
            current = self._tasks.get(task_id)
            if current and not current.done():
                work.close()
                with bind_log_context(job_id=task_id):
                    log_event(LOGGER, logging.INFO, "job_reused")
                return False
            task = asyncio.create_task(work, name=f"job-{task_id}")
            self._tasks[task_id] = task
            task.add_done_callback(lambda completed: self._finish(task_id, completed))
            with bind_log_context(job_id=task_id):
                log_event(LOGGER, logging.INFO, "job_started")
            return True

    def _finish(self, task_id: str, completed: asyncio.Task[None]) -> None:
        if self._tasks.get(task_id) is completed:
            self._tasks.pop(task_id, None)
        with bind_log_context(job_id=task_id):
            if completed.cancelled():
                log_event(LOGGER, logging.WARNING, "job_cancelled")
                return
            error = completed.exception()
            if error is None:
                log_event(LOGGER, logging.INFO, "job_finished")
            else:
                LOGGER.error("job_failed", exc_info=(type(error), error, error.__traceback__))

    def is_running(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        return bool(task and not task.done())

    async def cancel(self, task_ids: Iterable[str]) -> int:
        """Cancel only the named jobs and wait until their cleanup has completed."""
        async with self._lock:
            selected = [
                (task_id, task)
                for task_id in dict.fromkeys(task_ids)
                if (task := self._tasks.get(task_id)) is not None and not task.done()
            ]
            for _task_id, task in selected:
                task.cancel()
        if selected:
            await asyncio.gather(*(task for _key, task in selected), return_exceptions=True)
        async with self._lock:
            for task_id, task in selected:
                if self._tasks.get(task_id) is task:
                    self._tasks.pop(task_id, None)
        return len(selected)

    async def close(self) -> None:
        active = [task for task in self._tasks.values() if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self._tasks.clear()
