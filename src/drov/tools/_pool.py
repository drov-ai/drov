"""Bounded admission ahead of ThreadPoolExecutor's otherwise unbounded queue."""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from drov.errors import CapacityExceeded, ConfigurationError, ShutdownIncomplete


class Admission:
    def __init__(self, pool: "ToolPool") -> None:
        self.pool = pool
        self.used = False
        self.acquired = False

    async def wait(self, seconds: float) -> None:
        await asyncio.wait_for(self.pool._workers.acquire(), seconds)
        self.acquired = True

    def release(self) -> None:
        if not self.used:
            self.used = True
            self.pool._release(self.acquired)


class ToolPool:
    def __init__(self, workers: int, queue_size: int) -> None:
        self._capacity = workers + queue_size
        self._count = 0
        self._gate = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="drov-tool")
        self._closed = False
        self._workers = asyncio.Semaphore(workers)
        self._loop = asyncio.get_running_loop()

    def reserve(self) -> Admission:
        with self._gate:
            if self._closed:
                raise ConfigurationError("Tool executor is closed")
            if self._count >= self._capacity:
                raise CapacityExceeded("Tool thread/queue capacity reached")
            self._count += 1
        return Admission(self)

    def _release(self, worker: bool) -> None:
        with self._gate:
            self._count -= 1
        if worker:
            self._loop.call_soon_threadsafe(self._workers.release)

    def submit(self, admission: Admission, function: Callable[[], Any]) -> Future[Any]:
        if admission.pool is not self or admission.used or not admission.acquired:
            raise ConfigurationError("Invalid tool executor admission")
        try:
            future = self._executor.submit(function)
        except BaseException:
            admission.release()
            raise
        admission.used = True
        future.add_done_callback(lambda _: self._release(True))
        return future

    async def aclose(self) -> None:
        with self._gate:
            if self._count:
                raise ShutdownIncomplete("Tool calls/admissions still own executor capacity")
            self._closed = True
        # Only join after actual functions have ended; callbacks enqueue loop notices.
        self._executor.shutdown(wait=True)
