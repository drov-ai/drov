"""T-10 ordered completion/cancellation races and real receiving transactions."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from drov import Agent, ApprovalDecision, MemoryStorage, Runtime, SQLiteStorage, ToolContext, tool
from drov.errors import PersistenceUnavailable
from drov.models import ScriptedModel
from drov.models.protocol import ToolCall
from drov.storage.base import Storage
from drov.tools.operations import OperationJournal
from drov.types import ReconciliationRequest, Suspended, Usage
from examples.item_service_fixture import ItemService
from tests.fixtures import close_released_runtime
from tests.test_reconciliation import ALICE, approval, build, final, plan
from tests.test_runtime_crashes import database_facts


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[ItemService]:
    value = ItemService(tmp_path)
    try:
        await value.open()
        yield value
    finally:
        await value.aclose()


@pytest.fixture(params=["memory", "sqlite"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Storage:
    return MemoryStorage() if request.param == "memory" else SQLiteStorage(tmp_path / "race.db")


def known_model() -> ScriptedModel:
    usage = Usage(total_tokens=5, cost_amount="0.01", currency="USD", complete=True)
    return ScriptedModel(
        [plan().model_copy(update={"usage": usage}), final().model_copy(update={"usage": usage})]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    ["dispatch", "result_pending", "result_committed", "final_pending", "terminal_committed"],
)
async def test_cancel_completion_order_preserves_real_facts(
    storage: Storage, service: ItemService, boundary: str
) -> None:
    model = known_model()
    agent = build(model, service)
    entered, release = asyncio.Event(), asyncio.Event()
    original_commit = OperationJournal.commit
    terminal_version = None
    async with Runtime(agents=[agent], storage=storage) as runtime:
        first = await approval(runtime, agent)
        assert first.pending is not None
        original_final = runtime._runs.finish_response
        original_finish = runtime._runs.finish

        async def commit(journal: OperationJournal, operation: Any, event: Any) -> None:
            if (boundary == "dispatch" and event.type == "tool.dispatched") or (
                boundary == "result_pending" and event.type == "tool.succeeded"
            ):
                entered.set()
                await release.wait()
            await original_commit(journal, operation, event)
            if boundary == "result_committed" and event.type == "tool.succeeded":
                entered.set()
                await release.wait()

        async def final_response(*args: Any, **kwargs: Any) -> None:
            if boundary == "final_pending":
                entered.set()
                await release.wait()
            await original_final(*args, **kwargs)

        async def finish(*args: Any, **kwargs: Any) -> None:
            nonlocal terminal_version
            await original_finish(*args, **kwargs)
            if boundary == "terminal_committed":
                terminal_version = (await storage.get_run(ALICE, first.run_id)).state.state_version
                entered.set()
                await release.wait()

        with (
            patch.object(OperationJournal, "commit", commit),
            patch.object(runtime._runs, "finish_response", final_response),
            patch.object(runtime._runs, "finish", finish),
        ):
            await runtime.respond(
                first.run_id,
                request_id=first.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
            async with asyncio.timeout(5):
                await entered.wait()
            # Public observation disconnection cannot cancel the owning execution.
            waiter = asyncio.create_task(runtime.wait(first.run_id, context=ALICE))
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            receipts = await asyncio.gather(
                *(runtime.cancel(first.run_id, context=ALICE) for _ in range(20))
            )
            release.set()
            outcome = await runtime.wait(first.run_id, context=ALICE)
        if boundary == "result_pending":
            assert isinstance(outcome, Suspended) and isinstance(
                outcome.pending, ReconciliationRequest
            )
            assert (await storage.get_run(ALICE, first.run_id)).state.operations[
                0
            ].status == "unknown"
            evidence = await runtime.verify(
                first.run_id, request_id=outcome.pending.request_id, context=ALICE
            )
            outcome = await runtime.resume(
                first.run_id,
                request_id=outcome.pending.request_id,
                response=evidence,
                context=ALICE,
            )
        assert outcome.status == ("completed" if boundary == "terminal_committed" else "cancelled")
        record = await storage.get_run(ALICE, first.run_id)
        assert (
            record.state.operations[0].status
            == {
                "dispatch": "not_executed",
                "result_pending": "succeeded",
                "result_committed": "succeeded",
                "final_pending": "succeeded",
                "terminal_committed": "succeeded",
            }[boundary]
        )
        assert record.state.usage.complete
        assert record.state.usage.total_tokens == (
            10 if boundary in ("final_pending", "terminal_committed") else 5
        )
        if boundary == "terminal_committed":
            assert record.state.state_version == terminal_version
            assert all(not r.cancel_requested for r in receipts)
        else:
            assert record.state.cancel_requested
        events = await storage.events(ALICE, first.run_id, limit=500)
        assert sum(e.type == "run.cancel_requested" for e in events) == int(
            boundary != "terminal_committed"
        )
        old = await runtime.respond(
            first.run_id,
            request_id=first.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        assert old.request_id == first.pending.request_id
        assert len(model.calls) == (2 if boundary in ("final_pending", "terminal_committed") else 1)
    stats = await service.stats()
    assert stats["writes"] == stats["attempts"] == int(boundary != "dispatch")


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["wait", "stream"])
async def test_disconnected_wait_or_stream_does_not_cancel_approved_write(
    storage: Storage, service: ItemService, entry: str
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    @tool(name="update_item", effect="write", approval="required")
    async def update(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
        entered.set()
        await release.wait()
        return await service.apply(ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status)

    model = known_model()
    agent: Agent[str] = Agent(name="operations", revision="1", model=model, tools=[update])
    async with Runtime(agents=[agent], storage=storage) as runtime:
        first = await approval(runtime, agent)
        assert first.pending is not None
        await runtime.respond(
            first.run_id,
            request_id=first.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        async with asyncio.timeout(5):
            await entered.wait()
        if entry == "wait":
            waiter = asyncio.create_task(runtime.wait(first.run_id, context=ALICE))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        else:
            async with runtime.events(first.run_id, context=ALICE) as stream:
                await anext(stream)
        assert not (await storage.get_run(ALICE, first.run_id)).state.cancel_requested
        assert runtime.active_slots == 1 and (await service.stats())["writes"] == 0
        release.set()
        assert (await runtime.wait(first.run_id, context=ALICE)).status == "completed"
        assert len(model.calls) == 2
    assert (await service.stats())["writes"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("action", ["disconnect", "cancel"])
async def test_approval_receiving_commit_and_cancel_are_atomic(
    tmp_path: Path, service: ItemService, phase: str, action: str
) -> None:
    storage = SQLiteStorage(tmp_path / "receiving.db")
    entered, release = asyncio.Event(), asyncio.Event()
    target, fired = False, False
    original = storage._raw

    async def raw(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
        nonlocal target, fired
        if sql == "BEGIN IMMEDIATE":
            target = False
        if sql.startswith("INSERT INTO records") and parameters[0] == "event":
            target |= json.loads(parameters[5])["type"] == "interaction.responded"
        if sql == "COMMIT" and target and not fired:
            fired = True
            if phase == "after":
                result = await original(sql, parameters)
            entered.set()
            await release.wait()
            return result if phase == "after" else await original(sql, parameters)
        return await original(sql, parameters)

    model = known_model()
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        first = await approval(runtime, agent)
        assert first.pending is not None
        with patch.object(storage, "_raw", raw):
            responding = asyncio.create_task(
                runtime.respond(
                    first.run_id,
                    request_id=first.pending.request_id,
                    response=ApprovalDecision(approved=True),
                    context=ALICE,
                )
            )
            async with asyncio.timeout(5):
                await entered.wait()
            if action == "disconnect":
                responding.cancel()
                await asyncio.sleep(0)
                responding.cancel()
                assert not responding.done()
                cancelling = None
            else:
                cancelling = asyncio.create_task(runtime.cancel(first.run_id, context=ALICE))
            release.set()
            if action == "disconnect":
                with pytest.raises(asyncio.CancelledError):
                    await responding
            else:
                await responding
                assert cancelling is not None
                await cancelling
            outcome = await runtime.wait(first.run_id, context=ALICE)
        assert outcome.status == ("completed" if action == "disconnect" else "cancelled")
        expected_writes = int(action == "disconnect")
        assert (await service.stats())["writes"] == expected_writes
        async with storage.transaction() as tx:
            receipts = await tx.items(ALICE, first.run_id, "receipt")
            interactions = await tx.items(ALICE, first.run_id, "interaction")
        assert len(receipts) == len(interactions) == 1 and interactions[0].state == "consumed"
        repeated = await asyncio.gather(
            *(
                runtime.respond(
                    first.run_id,
                    request_id=first.pending.request_id,
                    response=ApprovalDecision(approved=True),
                    context=ALICE,
                )
                for _ in range(20)
            )
        )
        assert all(r.model_dump(mode="json") == receipts[0].data["receipt"] for r in repeated)
        assert len(model.calls) == (2 if action == "disconnect" else 1)


@pytest.mark.asyncio
async def test_cancel_after_partial_plan_keeps_first_write_and_skips_second(
    storage: Storage, service: ItemService
) -> None:
    response = plan()
    response = response.model_copy(
        update={
            "tool_calls": [
                *response.tool_calls,
                ToolCall(
                    call_id="write-2",
                    name="update_item",
                    arguments={"item_id": "item-02", "status": "processing"},
                ),
            ]
        }
    )
    model = ScriptedModel([response])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        first = await approval(runtime, agent)
        assert first.pending is not None
        second = await runtime.resume(
            first.run_id,
            request_id=first.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        assert isinstance(second, Suspended) and second.pending is not None
        assert (await service.stats())["writes"] == 1
        await runtime.cancel(first.run_id, context=ALICE)
        assert (await runtime.wait(first.run_id, context=ALICE)).status == "cancelled"
        record = await storage.get_run(ALICE, first.run_id)
        assert [s.status for s in record.state.operations] == ["succeeded", "not_executed"]
        assert len(model.calls) == 1
        assert (await storage.get_checkpoint(ALICE, first.run_id)).data["tool_dispatch_count"] == 1
    assert (await service.stats())["writes"] == 1


class ObservedSQLite(SQLiteStorage):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.errors: list[str] = []

    async def _raw(self, sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
        try:
            return await super()._raw(sql, parameters)
        except sqlite3.Error as exc:
            self.errors.append(exc.sqlite_errorname)
            raise


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["readonly_before_dispatch", "readonly_after_write", "full_after_write"]
)
async def test_real_sqlite_readonly_and_full_errors_do_not_invent_success(
    tmp_path: Path, service: ItemService, fault: str
) -> None:
    storage = ObservedSQLite(tmp_path / "io.db")

    async def arm() -> None:
        if fault.startswith("readonly"):
            await storage._raw("PRAGMA query_only=ON")
        else:
            pages, _ = await storage._raw("PRAGMA page_count")
            await storage._raw(f"PRAGMA max_page_count={int(pages[0][0])}")

    async def verify(ctx: ToolContext) -> Any:
        from drov import ReconciliationDecision

        return ReconciliationDecision.model_validate(
            await service.query(ctx.operation_id, ctx.tenant_id, ctx.user_id)
        )

    @tool(name="update_item", effect="write", approval="required", verifier=verify)
    async def update(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
        value = await service.apply(ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status)
        if fault != "readonly_before_dispatch":
            await arm()
        return value | {"payload": "x" * 130000}

    model = known_model()
    agent: Agent[str] = Agent(name="operations", revision="1", model=model, tools=[update])
    runtime = Runtime(agents=[agent], storage=storage)
    await runtime.open()
    first = await approval(runtime, agent)
    assert first.pending is not None
    original = OperationJournal.commit

    async def commit(journal: OperationJournal, operation: Any, event: Any) -> None:
        if fault == "readonly_before_dispatch" and event.type == "tool.dispatched":
            await arm()
        await original(journal, operation, event)

    try:
        with patch.object(OperationJournal, "commit", commit):
            await runtime.respond(
                first.run_id,
                request_id=first.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
            with pytest.raises(PersistenceUnavailable):
                await runtime.wait(first.run_id, context=ALICE)
        assert ("SQLITE_FULL" if fault.startswith("full") else "SQLITE_READONLY") in storage.errors
        await storage._raw("PRAGMA query_only=OFF")
        facts = database_facts(storage.path)
        state = facts["run"][0]["state"]
        assert state["status"] == "running" and state["outcome"] is None
        assert state["operations"][0]["status"] == (
            "prepared" if fault == "readonly_before_dispatch" else "dispatched"
        )
        assert not any(e["type"] in ("tool.succeeded", "run.completed") for e in facts["event"])
    finally:
        await storage._raw("PRAGMA query_only=OFF")
        await storage._raw("PRAGMA max_page_count=2147483646")
        await close_released_runtime(runtime)
    # Same registration; a recovered durable result cannot be replaced by this large value.
    async with Runtime(agents=[agent], storage=SQLiteStorage(storage.path)) as reopened:
        outcome = await reopened.recover(first.run_id, context=ALICE)
        if fault == "readonly_before_dispatch":
            assert outcome.status == "completed"
        else:
            assert isinstance(outcome, Suspended) and isinstance(
                outcome.pending, ReconciliationRequest
            )
            evidence = await reopened.verify(
                first.run_id, request_id=outcome.pending.request_id, context=ALICE
            )
            outcome = await reopened.resume(
                first.run_id,
                request_id=outcome.pending.request_id,
                response=evidence,
                context=ALICE,
            )
            assert outcome.status == "completed"
    assert (await service.stats())["writes"] == 1
