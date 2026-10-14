"""Owned SDK subprocess fixture. Barriers stop at real SQLite semantic commits."""

import argparse
import asyncio
import json
import sqlite3
import sys
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Direct isolated script invocation needs the checkout's test/example fixtures.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drov import Agent, ApprovalDecision, Limits, Memory, Runtime, SQLiteStorage, ToolContext, tool  # noqa: E402
from drov.errors import PersistenceUnavailable, RecordNotFound, ShutdownIncomplete, StorageInUse  # noqa: E402
from drov.models import ScriptedModel  # noqa: E402
from drov.models.protocol import ModelComplete, ModelEvent, ModelRequest, ToolCall  # noqa: E402
from drov.types import (  # noqa: E402
    ApprovalRequest,
    ReconciliationDecision,
    ReconciliationRequest,
    Suspended,
    Usage,
)
from examples.item_service_fixture import ItemService  # noqa: E402
from tests.fixtures import close_released_runtime  # noqa: E402
from tests.test_reconciliation import ALICE, build, final, plan  # noqa: E402

TARGETS = {
    "accepted": "run.accepted",
    "input": "context.prepared",
    "attempt": "model.attempt_started",
    "plan": "model.attempt_completed",
    "prepared": "tool.prepared",
    "approval": "interaction.requested",
    "receipt": "interaction.responded",
    "dispatch": "tool.dispatched",
    "result": "tool.succeeded",
    "final": "model.attempt_completed",
    "terminal": "run.completed",
    "cancel": "run.cancel_requested",
    "memory": "memory.changed",
}


def signal(value: dict[str, Any]) -> None:
    print(json.dumps(value), flush=True)


async def halt(value: dict[str, Any]) -> None:
    signal(value)
    await asyncio.Event().wait()  # Parent owns the deadline and hard termination.


class BarrierStorage(SQLiteStorage):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args.path)
        self.args = args
        self.target: dict[str, Any] | None = None
        self.fired = False

    async def _raw(self, sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
        if sql == "BEGIN IMMEDIATE":
            self.target = None
        if sql.startswith("INSERT INTO records") and parameters[0] == "event":
            event = json.loads(parameters[5])
            if event["type"] == TARGETS.get(self.args.boundary):
                is_plan = bool(event["data"].get("tool_calls"))
                if self.args.boundary not in ("plan", "final") or is_plan == (
                    self.args.boundary == "plan"
                ):
                    self.target = event
        if sql == "COMMIT" and self.target is not None and not self.fired:
            self.fired = True
            if self.args.phase == "after":
                result = await super()._raw(sql, parameters)
            if self.args.mode == "fault":
                raise sqlite3.OperationalError("Synthetic commit response failure")
            if self.args.mode == "real_commit":
                # Connection-local deferred FK: SQLite itself rejects COMMIT.
                await super()._raw("CREATE TEMP TABLE fault_parent (id INTEGER PRIMARY KEY)")
                await super()._raw(
                    "CREATE TEMP TABLE fault_child (parent INTEGER REFERENCES fault_parent(id) "
                    "DEFERRABLE INITIALLY DEFERRED)"
                )
                await super()._raw("INSERT INTO fault_child VALUES (1)")
                return await super()._raw(sql, parameters)
            await halt(
                {"boundary": self.args.boundary, "phase": self.args.phase, "event": self.target}
            )
            if self.args.phase == "after":
                return result
        return await super()._raw(sql, parameters)


class FixtureModel(ScriptedModel):
    def __init__(self, boundary: str, count: int, scenario: str) -> None:
        super().__init__([])
        self.boundary = boundary
        self.count, self.scenario = count, scenario

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls.append(ModelRequest.from_json(request.model_dump_json()))
        if self.boundary == "model_reply":
            await halt({"boundary": "model_reply", "request": request.model_dump(mode="json")})
        response = final() if any(m.role == "tool" for m in request.messages) else plan()
        if response.tool_calls:
            if self.scenario == "memory":
                response = response.model_copy(
                    update={
                        "tool_calls": [
                            ToolCall(
                                call_id="remember-1",
                                name="memory.remember",
                                arguments={
                                    "key": "style",
                                    "content": "synthetic concise",
                                    "expected_version": 0,
                                },
                            )
                        ]
                    }
                )
            elif self.count == 2:
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
        response = response.model_copy(
            update={
                "usage": Usage(
                    input_tokens=3,
                    output_tokens=2,
                    total_tokens=5,
                    cost_amount="0.01",
                    currency="USD",
                    complete=True,
                )
            }
        )
        yield ModelComplete(response=response)


async def main(args: argparse.Namespace) -> None:
    if args.mode == "probe":
        try:
            async with SQLiteStorage(args.path):
                signal({"locked": False})
        except StorageInUse:
            signal({"locked": True})
        return
    service = ItemService(args.path.parent)
    service.port = args.port
    service.mode = "barrier_reply" if args.boundary == "write_before_reply" else "normal"
    model = FixtureModel(args.boundary if args.mode != "recover" else "", args.count, args.scenario)
    agent = build(model, service, limits=Limits(model_timeout_seconds=2, tool_timeout_seconds=2))
    if args.budget != "normal":
        agent = build(
            model,
            service,
            limits=Limits(
                model_timeout_seconds=2,
                tool_timeout_seconds=2,
                model_retries=0 if args.budget == "attempt" else 1,
                run_active_seconds=2 if args.budget == "time" else 300,
            ),
        )
    if args.scenario == "memory":
        agent = Agent(
            name="operations", revision="1", model=model, memory=Memory(), limits=agent.limits
        )
    if args.kind:
        async_release, thread_release = asyncio.Event(), threading.Event()

        async def verify(ctx: ToolContext) -> ReconciliationDecision:
            return ReconciliationDecision.model_validate(
                await service.query(ctx.operation_id, ctx.tenant_id, ctx.user_id)
            )

        if args.kind == "thread":
            loop = asyncio.get_running_loop()

            @tool(name="update_item", effect="write", approval="required", verifier=verify)
            def update_thread(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
                value = asyncio.run_coroutine_threadsafe(
                    service.apply(ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status),
                    loop,
                ).result(timeout=10)
                signal({"boundary": "live_call", "kind": "thread"})
                thread_release.wait()
                return value

            selected = update_thread
        else:

            @tool(name="update_item", effect="write", approval="required", verifier=verify)
            async def update_task(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
                value = await service.apply(
                    ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status
                )
                signal({"boundary": "live_call", "kind": "task"})
                while not async_release.is_set():
                    try:
                        await async_release.wait()
                    except asyncio.CancelledError:
                        continue
                return value

            selected = update_task
        agent = Agent(
            name="operations",
            revision="1",
            model=model,
            tools=[selected],
            limits=Limits(model_timeout_seconds=2, tool_timeout_seconds=30),
        )
    storage = SQLiteStorage(args.path) if args.mode == "recover" else BarrierStorage(args)
    runtime = Runtime(
        agents=[agent],
        storage=storage,
        limits=Limits(shutdown_grace_seconds=0.2) if args.kind else None,
    )
    await runtime.open()
    if args.boundary == "model_response" and args.mode != "recover":
        original_call = runtime._runs.model_call

        async def completed_call(*args: Any, **kwargs: Any) -> Any:
            result = await original_call(*args, **kwargs)
            await halt({"boundary": "model_response", "request": args[1].model_dump(mode="json")})
            return result

        patch.object(runtime._runs, "model_call", completed_call).start()
    # Terminal runs are absent from startup scan; read them through their session.
    from drov.storage.records import SessionScope

    async with storage.transaction() as tx:
        try:
            records = await tx.runs(SessionScope(**ALICE.model_dump(), session_id="s"))
        except RecordNotFound:
            records = []
    initial = None
    if records:
        record = records[0]
        initial = {
            "state": record.state.model_dump(mode="json"),
            "checkpoint": (await storage.get_checkpoint(ALICE, record.state.run_id)).data,
        }
        run_id = record.state.run_id
        if record.state.cancel_requested:
            # Complete a durable cancellation whose control process died before finalization.
            await runtime.cancel(run_id, context=ALICE)
            record = await storage.get_run(ALICE, run_id)
        if record.state.status == "suspended" and record.state.reason == "recovery":
            outcome = await runtime.recover(run_id, context=ALICE)
        else:
            outcome = await runtime.wait(run_id, context=ALICE)
    else:
        if args.entry == "stream":
            async with runtime.stream(
                agent, "update synthetic item", session_id="s", context=ALICE
            ) as stream:
                async for _ in stream:
                    pass
                outcome = await stream.result()
        else:
            outcome = await runtime.run(
                agent, "update synthetic item", session_id="s", context=ALICE
            )
        run_id = outcome.run_id
    try:
        old_receipts = []
        async with storage.transaction() as tx:
            old_receipts = await tx.items(ALICE, run_id, "receipt")
        if args.boundary == "cancel" and args.mode != "recover":
            await runtime.cancel(run_id, context=ALICE)
            outcome = await runtime.wait(run_id, context=ALICE)
        for _ in range(8):
            if not isinstance(outcome, Suspended):
                break
            assert outcome.pending is not None
            if isinstance(outcome.pending, ApprovalRequest):
                response: Any = ApprovalDecision(approved=True)
            elif isinstance(outcome.pending, ReconciliationRequest):
                response = await runtime.verify(
                    run_id, request_id=outcome.pending.request_id, context=ALICE
                )
            else:
                raise AssertionError("Unexpected suspension")
            if args.kind and args.mode != "recover":
                await runtime.respond(
                    run_id, request_id=outcome.pending.request_id, response=response, context=ALICE
                )
                assert await asyncio.to_thread(sys.stdin.readline) == "cancel\n"
                await runtime.cancel(run_id, context=ALICE)
                outcome = await runtime.wait(run_id, context=ALICE)
                try:
                    await runtime.aclose()
                except ShutdownIncomplete:
                    pass
                else:
                    raise AssertionError("Live call falsely released ownership")
                try:
                    await SQLiteStorage(args.path).open()
                except StorageInUse:
                    pass
                else:
                    raise AssertionError("Live call lost its database lock")
                assert runtime.active_slots == 1
                signal(
                    {
                        "boundary": "shutdown_incomplete",
                        "outcome": outcome.model_dump(mode="json"),
                        "slots": runtime.active_slots,
                    }
                )
                assert await asyncio.to_thread(sys.stdin.readline) == "release\n"
                thread_release.set()
                async_release.set()
                await close_released_runtime(runtime)
                async with SQLiteStorage(args.path):
                    pass
                signal({"closed": True, "slots": runtime.active_slots})
                return
            assert isinstance(outcome, Suspended) and outcome.pending is not None
            outcome = await runtime.resume(
                run_id, request_id=outcome.pending.request_id, response=response, context=ALICE
            )
        async with storage.transaction() as tx:
            receipts = await tx.items(ALICE, run_id, "receipt")
        # Replaying committed responses must not run model/tool work or alter receipts.
        calls = len(model.calls)
        saved_state = (await storage.get_run(ALICE, run_id)).state
        for item in old_receipts:
            if item.data.get("record_type") == "verification":
                continue
            saved = item.data
            response_data = saved.get("response")
            if not isinstance(response_data, dict) or response_data.get("kind") != "approval":
                continue
            replayed = await runtime.respond(
                run_id,
                request_id=item.item_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
            assert replayed.model_dump(mode="json") == saved["receipt"]
        assert len(model.calls) == calls
        assert (await storage.get_run(ALICE, run_id)).state == saved_state
        from drov.storage.records import MemoryScope

        async with storage.transaction() as tx:
            memories = await tx.recall_memory(
                MemoryScope(**ALICE.model_dump(), agent_name="operations")
            )
        signal(
            {
                "outcome": outcome.model_dump(mode="json"),
                "initial": initial,
                "calls": [r.model_dump(mode="json") for r in model.calls],
                "checkpoint": (await storage.get_checkpoint(ALICE, run_id)).data,
                "events": [
                    e.model_dump(mode="json")
                    for e in await storage.events(ALICE, run_id, limit=500)
                ],
                "receipts": [r.model_dump(mode="json") for r in receipts],
                "stats": await service.stats(),
                "memories": [m.model_dump(mode="json") for m in memories],
            }
        )
    finally:
        await runtime.aclose()


async def run(args: argparse.Namespace) -> None:
    try:
        await main(args)
    except PersistenceUnavailable as exc:
        # Keep this failed SDK process owned until the parent observes and terminates it.
        await halt(
            {
                "failure": "persistence_unavailable",
                "boundary": args.boundary,
                "phase": args.phase,
                "sqlite_error": getattr(exc.__cause__, "sqlite_errorname", None),
            }
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument(
        "--mode", choices=["halt", "fault", "real_commit", "recover", "probe"], required=True
    )
    parser.add_argument("--boundary", default="")
    parser.add_argument("--phase", choices=["before", "after"], default="after")
    parser.add_argument("--entry", choices=["sdk", "stream"], default="sdk")
    parser.add_argument("--kind", choices=["", "thread", "task"], default="")
    parser.add_argument("--count", type=int, choices=[1, 2], default=1)
    parser.add_argument("--scenario", choices=["write", "memory"], default="write")
    parser.add_argument("--budget", choices=["normal", "attempt", "time"], default="normal")
    asyncio.run(run(parser.parse_args()))
