"""Bounded retrieval owned by the active Run; late values are never consumed."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from drov.config import Memory
from drov.errors import PersistenceUnavailable
from drov.storage.base import Storage
from drov.storage.records import MemoryRecord, MemoryScope, RunRecord

if TYPE_CHECKING:
    from drov.runtime.core import _Work


@dataclass
class Retrieval:
    records: list[MemoryRecord]
    status: str


async def retrieve(
    storage: Storage, record: RunRecord, memory: Memory, work: "_Work[None]", end: float
) -> Retrieval:
    from drov.runtime.core import _observe, _owned_task

    scope = MemoryScope(
        tenant_id=record.scope.tenant_id, user_id=record.scope.user_id, agent_name=record.agent_name
    )

    async def read() -> list[MemoryRecord]:
        async with storage.transaction() as tx:
            return await tx.recall_memory(scope, limit=memory.limit)

    work.checkpoint()
    task = _owned_task(read, name="drov-memory-retrieval")
    work.track(task)
    task.add_done_callback(_observe)
    seconds = (
        record.limits.memory_required_seconds
        if memory.retrieval == "required"
        else record.limits.memory_best_effort_seconds
    )
    loop = asyncio.get_running_loop()
    deadline = min(end, loop.time() + seconds)
    try:
        done, _ = await asyncio.wait({task}, timeout=max(0, deadline - loop.time()))
        if done and loop.time() <= deadline:
            try:
                records = task.result()
            except (Exception, asyncio.CancelledError):
                return Retrieval([], "failed")
            return Retrieval(records, "included" if records else "empty")
        task.cancel()
        # Built-in reads are cooperative. Retain actual ownership if a broken backend is not.
        done, _ = await asyncio.wait({task}, timeout=0.1)
        if not done:
            raise PersistenceUnavailable("Memory retrieval did not stop after cancellation")
        return Retrieval([], "timeout")
    except asyncio.CancelledError:
        task.cancel()
        raise
