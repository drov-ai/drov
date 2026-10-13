"""Real scheduling/cancellation/underlying-call ownership; not an Agent loop."""

import asyncio
import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from drov import Limits, Runtime, SQLiteStorage
from drov.errors import CapacityExceeded, ConfigurationError, ShutdownIncomplete, StorageInUse
from drov.runtime.core import _Work
from tests.test_runtime import definition


@pytest.mark.asyncio
async def test_cancelled_waiter_and_lost_handle_do_not_cancel_or_drop_task() -> None:
    entered, finish = asyncio.Event(), asyncio.Event()
    async with Runtime(agents=[definition()], limits=Limits(active_slots=1)) as runtime:

        async def execute(work: _Work[str]) -> str:
            entered.set()
            await finish.wait()
            return "real-worker-result"

        work = runtime._spawn(runtime._reserve(), execute)
        task_ref = weakref.ref(work.task)
        waiter = asyncio.create_task(work.wait())
        await entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not work.task.cancelled()
        del waiter, work
        gc.collect()
        task = task_ref()
        assert task is not None and not task.done()
        assert runtime.active_slots == 1
        with pytest.raises(CapacityExceeded):
            runtime._reserve()
        finish.set()
        assert await task == "real-worker-result"
        await asyncio.sleep(0)
        assert runtime.active_slots == 0


@pytest.mark.asyncio
async def test_concurrent_reservations_are_bounded_and_unclaimed_release_is_idempotent() -> None:
    async with Runtime(agents=[definition()], limits=Limits(active_slots=2)) as runtime:

        async def reserve() -> object:
            return runtime._reserve()

        results = await asyncio.gather(*(reserve() for _ in range(20)), return_exceptions=True)
        assert sum(isinstance(item, CapacityExceeded) for item in results) == 18
        assert runtime.active_slots == 2
        for slot in tuple(runtime._slots):
            slot.release()
            slot.release()
        assert runtime.active_slots == 0


@pytest.mark.asyncio
async def test_failed_schedule_releases_slot_and_rejects_duplicate_or_foreign_slot() -> None:
    async with Runtime(agents=[definition()]) as runtime:

        async def execute(work: _Work[int]) -> int:
            return 1

        slot = runtime._reserve()
        with patch("drov.runtime.core.asyncio.create_task", side_effect=RuntimeError("scheduler")):
            with pytest.raises(RuntimeError, match="scheduler"):
                runtime._spawn(slot, execute)
        assert runtime.active_slots == 0
        with pytest.raises(ConfigurationError):
            runtime._spawn(slot, execute)
        async with Runtime(agents=[definition()]) as other:
            foreign = other._reserve()
            with pytest.raises(ConfigurationError):
                runtime._spawn(foreign, execute)
            foreign.release()
        slot = runtime._reserve()
        work = runtime._spawn(slot, execute)
        with pytest.raises(ConfigurationError, match="owns this slot"):
            slot.release()
        with pytest.raises(ConfigurationError):
            runtime._spawn(slot, execute)
        assert await work.wait() == 1


@pytest.mark.asyncio
async def test_explicit_cancel_before_dispatch_never_calls_worker() -> None:
    called = 0
    async with Runtime(agents=[definition()]) as runtime:

        async def execute(work: _Work[None]) -> None:
            nonlocal called
            called += 1

        work = runtime._spawn(runtime._reserve(), execute)
        work.request_cancel()
        work.request_cancel()
        with pytest.raises(asyncio.CancelledError):
            await work.wait()
        await asyncio.sleep(0)
        assert called == 0 and runtime.active_slots == 0


@pytest.mark.asyncio
async def test_cancel_refusing_work_keeps_slot_and_blocks_new_dispatch(tmp_path: Path) -> None:
    entered, refused, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    storage = SQLiteStorage(tmp_path / "refused.db")
    runtime = Runtime(
        agents=[definition()],
        storage=storage,
        limits=Limits(active_slots=1, shutdown_grace_seconds=0.02),
    )
    await runtime.open()

    async def execute(work: _Work[None]) -> None:
        entered.set()
        try:
            await finish.wait()
        except asyncio.CancelledError:
            refused.set()
            with pytest.raises(asyncio.CancelledError):
                work.checkpoint()
            await finish.wait()

    work = runtime._spawn(runtime._reserve(), execute)
    await entered.wait()
    work.request_cancel()
    await refused.wait()
    with pytest.raises(CapacityExceeded):
        runtime._reserve()
    try:
        with pytest.raises(ShutdownIncomplete, match="Active"):
            await runtime.aclose()
        assert runtime.active_slots == 1 and storage.is_open
        with pytest.raises(ConfigurationError, match="accepting"):
            runtime._reserve()
        with pytest.raises(StorageInUse):
            await SQLiteStorage(storage.path).open()
    finally:
        finish.set()
        await work.wait()
        await runtime.aclose()
    assert runtime.active_slots == 0 and not storage.is_open


@pytest.mark.asyncio
async def test_real_thread_outlives_cancelled_task_and_retains_storage_lock(tmp_path: Path) -> None:
    thread_started, release = threading.Event(), threading.Event()
    tracked = asyncio.Event()
    storage = SQLiteStorage(tmp_path / "thread.db")
    runtime = Runtime(
        agents=[definition()],
        storage=storage,
        limits=Limits(active_slots=1, shutdown_grace_seconds=0.02),
    )
    await runtime.open()
    executor = ThreadPoolExecutor(max_workers=1)

    def blocking_call() -> str:
        thread_started.set()
        assert release.wait(10), "Test must always release the actual thread"
        return "actual-thread-result"

    async def execute(work: _Work[str]) -> str:
        future = executor.submit(blocking_call)
        work.track(future)
        tracked.set()
        return await asyncio.shield(asyncio.wrap_future(future))

    work = runtime._spawn(runtime._reserve(), execute)
    try:
        await tracked.wait()
        assert await asyncio.to_thread(thread_started.wait, 5)
        work.request_cancel()
        with pytest.raises(asyncio.CancelledError):
            await work.wait()
        assert work.task.done() and runtime.active_slots == 1
        with pytest.raises(CapacityExceeded):
            runtime._reserve()
        with pytest.raises(ShutdownIncomplete):
            await runtime.aclose()
        assert storage.is_open
        with pytest.raises(StorageInUse):
            await SQLiteStorage(storage.path).open()
    finally:
        release.set()
        await asyncio.to_thread(executor.shutdown, wait=True)
        await runtime.aclose()
    assert runtime.active_slots == 0 and not storage.is_open
    async with SQLiteStorage(storage.path):
        pass


@pytest.mark.asyncio
async def test_completed_wrapper_does_not_release_unfinished_async_call() -> None:
    call = asyncio.get_running_loop().create_future()
    async with Runtime(agents=[definition()], limits=Limits(active_slots=1)) as runtime:

        async def execute(work: _Work[str]) -> str:
            work.track(call)
            return "wrapper-has-stopped"

        work = runtime._spawn(runtime._reserve(), execute)
        assert await work.wait() == "wrapper-has-stopped"
        assert runtime.active_slots == 1
        with pytest.raises(CapacityExceeded):
            runtime._reserve()
        call.set_result(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert runtime.active_slots == 0


@pytest.mark.asyncio
async def test_failure_is_observed_without_waiter_but_preserved_for_later_wait() -> None:
    async with Runtime(agents=[definition()]) as runtime:

        async def execute(work: _Work[None]) -> None:
            raise RuntimeError("actual worker failure")

        work = runtime._spawn(runtime._reserve(), execute)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert runtime.active_slots == 0
        with pytest.raises(RuntimeError, match="actual worker failure"):
            await work.wait()


@pytest.mark.asyncio
async def test_pending_acceptance_slot_blocks_shutdown_until_released() -> None:
    runtime = Runtime(agents=[definition()], limits=Limits(shutdown_grace_seconds=0.02))
    await runtime.open()
    slot = runtime._reserve()
    with pytest.raises(ShutdownIncomplete):
        await runtime.aclose()
    slot.release()
    await runtime.aclose()


@pytest.mark.asyncio
async def test_execution_task_cannot_close_its_own_runtime_or_track_another_task() -> None:
    async with Runtime(agents=[definition()]) as runtime:
        call = asyncio.get_running_loop().create_future()

        async def execute(work: _Work[None]) -> None:
            with pytest.raises(ConfigurationError, match="own execution"):
                await runtime.aclose()

        work = runtime._spawn(runtime._reserve(), execute)
        with pytest.raises(ConfigurationError, match="owning work"):
            work.track(call)
        await work.wait()
        call.cancel()


@pytest.mark.asyncio
async def test_normal_close_cancels_live_task_and_no_task_leaks() -> None:
    entered = asyncio.Event()
    runtime = Runtime(agents=[definition()])
    await runtime.open()

    async def execute(work: _Work[None]) -> None:
        entered.set()
        await asyncio.Event().wait()

    work = runtime._spawn(runtime._reserve(), execute)
    await entered.wait()
    await runtime.aclose()
    assert work.task.cancelled()
    assert not runtime._works and not runtime._slots and not runtime._resources


@pytest.mark.asyncio
async def test_dispatched_call_is_retained_when_cancel_signal_already_arrived() -> None:
    call = asyncio.get_running_loop().create_future()
    async with Runtime(agents=[definition()]) as runtime:

        async def execute(work: _Work[None]) -> None:
            work.request_cancel()
            work.track(call)
            await asyncio.Event().wait()

        work = runtime._spawn(runtime._reserve(), execute)
        with pytest.raises(asyncio.CancelledError):
            await work.wait()
        assert runtime.active_slots == 1
        call.set_result(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert runtime.active_slots == 0


@pytest.mark.skipif(not hasattr(asyncio, "eager_task_factory"), reason="Python 3.12+ API")
@pytest.mark.asyncio
async def test_eager_task_factory_cannot_run_work_before_owner_assignment() -> None:
    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()
    loop.set_task_factory(asyncio.eager_task_factory)  # type: ignore[attr-defined]
    try:
        async with Runtime(agents=[definition()]) as runtime:
            call = loop.create_future()

            async def execute(work: _Work[int]) -> int:
                work.track(call)
                call.set_result(42)
                return 42

            work = runtime._spawn(runtime._reserve(), execute)
            assert await work.wait() == 42
    finally:
        loop.set_task_factory(previous)
