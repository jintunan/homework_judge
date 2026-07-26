from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from typing import Any

from ..errors import AppError

JobHandler = Callable[[Any], Awaitable[None]]


class JobManager:
    def __init__(self, *, concurrency: int, max_queue_size: int = 200) -> None:
        self.concurrency = concurrency
        self.queue: asyncio.Queue[tuple[Hashable, Any] | None] = asyncio.Queue(
            maxsize=max_queue_size
        )
        self._keys: set[Hashable] = set()
        self._workers: list[asyncio.Task[None]] = []
        self._accepting = False
        self._lock = asyncio.Lock()

    async def start(self, handler: JobHandler) -> None:
        if self._workers:
            return
        self._accepting = True
        self._workers = [
            asyncio.create_task(self._worker(handler), name=f"job-worker-{index}")
            for index in range(self.concurrency)
        ]

    async def submit(self, key: Hashable, payload: Any) -> bool:
        if not self._accepting:
            raise AppError(503, "JOB_MANAGER_STOPPED", "后台任务管理器尚未启动")
        async with self._lock:
            if key in self._keys:
                return False
            if self.queue.full():
                raise AppError(503, "JOB_QUEUE_FULL", "后台任务队列已满，请稍后重试")
            self._keys.add(key)
            self.queue.put_nowait((key, payload))
        return True

    def state(self) -> dict[str, int]:
        pending = self.queue.qsize()
        return {
            "pending": pending,
            "active": max(0, len(self._keys) - pending),
        }

    async def _worker(self, handler: JobHandler) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                return
            key, payload = item
            try:
                await handler(payload)
            except Exception:
                # Handler persists its own domain failure. A worker must continue
                # so one damaged paper cannot stop the remaining class batch.
                pass
            finally:
                async with self._lock:
                    self._keys.discard(key)
                self.queue.task_done()

    async def shutdown(self, shutdown_timeout: float = 20.0) -> None:
        self._accepting = False
        if not self._workers:
            return
        try:
            await asyncio.wait_for(self.queue.join(), timeout=shutdown_timeout)
        except TimeoutError:
            pass
        for _worker in self._workers:
            await self.queue.put(None)
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._workers, return_exceptions=True),
                timeout=shutdown_timeout,
            )
        except TimeoutError:
            for worker in self._workers:
                worker.cancel()
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        async with self._lock:
            self._keys.clear()
