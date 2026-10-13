import asyncio
import json
import threading
from collections.abc import Iterator
from concurrent.futures import Future as ThreadFuture
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from drov import Agent, Limits, PermissionPolicy, Runtime, SQLiteStorage, ToolContext, tool
from drov.auth import Resource, ToolResource
from drov.errors import (
    CapacityExceeded,
    Conflict,
    PersistenceUnavailable,
    RecordNotFound,
    ShutdownIncomplete,
    StorageInUse,
    TransientToolError,
)
from drov.runtime.core import _Work
from drov.storage import MemoryStorage
from drov.storage.records import (
    Checkpoint,
    EventDraft,
    RunRecord,
    RunState,
    SessionScope,
    StartRequest,
)
from drov.tools.execution import ToolExecution
from drov.tools.operations import OperationJournal
from drov.tools.python import PythonTool
from drov.types import RunContext
from tests.fixtures import ScriptedAdapter


def agent_for(python_tool: PythonTool, limits: Limits | None = None) -> Agent[str]:
    return Agent(
        name="assistant",
        revision="1",
        model=ScriptedAdapter([]),
        tools=[python_tool],
        limits=limits,
    )


async def create_run(runtime: Runtime, agent: Agent[Any], session: str = "s") -> RunRecord:
    assert agent.name is not None and agent.revision is not None
    scope = SessionScope(tenant_id="t", user_id="alice", session_id=session)
    ref = await runtime.storage.start(
        StartRequest(
            scope=scope,
            agent_name=agent.name,
            revision=agent.revision,
            config_hash=runtime.registration(agent.name, agent.revision).config_hash,
            input="test",
            limits=agent.limits,
        )
    )
    async with runtime.storage.transaction() as tx:
        await tx.save_step(
            scope,
            state=RunState(run_id=ref.run_id, state_version=2, status="running"),
            expected_version=1,
            checkpoint=Checkpoint(run_id=ref.run_id, state_version=2, cursor="model"),
            events=[EventDraft(type="run.running")],
        )
    return await runtime.storage.get_run(scope, ref.run_id)


def dispatch(
    runtime: Runtime,
    python_tool: PythonTool,
    record: RunRecord,
    arguments: str = "{}",
    operation_id: str = "op",
) -> _Work[ToolExecution]:
    async def execute(work: _Work[ToolExecution]) -> ToolExecution:
        assert runtime._tool_dispatcher is not None
        return await runtime._tool_dispatcher.execute(
            python_tool, arguments, work=work, record=record, operation_id=operation_id
        )

    return runtime._spawn(runtime._reserve(), execute)


@pytest.mark.asyncio
@pytest.mark.parametrize("async_tool", [False, True])
async def test_real_execution_default_normalization_and_trusted_context(async_tool: bool) -> None:
    contexts: list[ToolContext] = []

    def sync(value: int = 2, *, ctx: ToolContext) -> dict[str, int]:
        contexts.append(ctx)
        assert ctx.deps["service"] is service
        return {"value": value}

    async def asynchronous(value: int = 2, *, ctx: ToolContext) -> dict[str, int]:
        return sync(value, ctx=ctx)

    python_tool = tool(name="search", revision="2", effect="read")(
        asynchronous if async_tool else sync
    )
    agent = agent_for(python_tool)
    service = object()
    async with Runtime(agents=[agent], dependencies={"service": service}) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, python_tool, record).wait()
        assert result.operation.result == {"value": 2} and result.operation.has_result
        ctx = contexts[0]
        assert (ctx.tenant_id, ctx.user_id, ctx.run_id, ctx.operation_id) == (
            "t",
            "alice",
            record.state.run_id,
            "op",
        )
        assert result.operation.arguments == {"value": 2}
        again = await dispatch(runtime, python_tool, record, '{"value":2}').wait()
        assert again.operation == result.operation and len(contexts) == 1
        with pytest.raises(Conflict):
            await dispatch(runtime, python_tool, record, '{"value":3}').wait()
        checkpoint = await runtime.storage.get_checkpoint(record.scope, record.state.run_id)
        current = await runtime.storage.get_run(record.scope, record.state.run_id)
        assert checkpoint.state_version == current.state.state_version
        assert checkpoint.data["tool_dispatch_count"] == 1
        assert current.state.operations[0].status == "succeeded"
        events = await runtime.storage.events(record.scope, record.state.run_id)
        assert [event.type for event in events][-3:] == [
            "tool.prepared",
            "tool.dispatched",
            "tool.succeeded",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        '{"ctx":{}}',
        '{"tenant_id":"other"}',
        '{"value":"2"}',
        '{"value":true}',
        '{"value":1,"value":2}',
        '{"value":NaN}',
        "[]",
    ],
)
async def test_illegal_arguments_never_dispatch(arguments: str) -> None:
    count = 0

    @tool(name="search", effect="read")
    def search(value: int = 2, *, ctx: ToolContext) -> str:
        nonlocal count
        count += 1
        return "value"

    agent = agent_for(search)
    async with Runtime(agents=[agent]) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, search, record, arguments).wait()
        assert result.operation.status == "not_executed"
        assert result.operation.error_code == "invalid_arguments" and count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy,effect,approval,code",
    [
        (
            PermissionPolicy(deny={"search"}, allow={"search"}),
            "read",
            "policy",
            "permission_denied",
        ),
        (PermissionPolicy(ask={"search"}, allow={"search"}), "read", "policy", "approval_required"),
        (PermissionPolicy(allow={"search"}), "write", "required", "approval_required"),
        (PermissionPolicy(), "unknown", "policy", "approval_required"),
        (PermissionPolicy(allow={"search"}), "write", "policy", "effect_not_supported"),
        (PermissionPolicy(allow={"search"}), "unknown", "policy", "effect_not_supported"),
    ],
)
async def test_denied_ask_and_unsupported_effects_dispatch_zero(
    policy: PermissionPolicy, effect: str, approval: str, code: str
) -> None:
    called = 0

    def function() -> str:
        nonlocal called
        called += 1
        return "unexpected"

    python_tool = tool(name="search", effect=effect, approval=approval)(function)  # type: ignore[arg-type]
    agent = agent_for(python_tool)
    async with Runtime(agents=[agent], policy=policy) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, python_tool, record).wait()
        assert called == 0 and result.operation.error_code == code
        assert result.operation.attempts == 0
        again = await dispatch(runtime, python_tool, record).wait()
        assert again.operation == result.operation and called == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_at", [1, 2, 3])
async def test_revocation_at_every_dispatch_barrier(fail_at: int) -> None:
    called = 0

    class Revoke:
        checks = 0

        async def authorize(self, actor: RunContext, action: object, resource: Resource) -> bool:
            self.checks += 1
            assert isinstance(resource, ToolResource)
            assert resource.arguments == {"value": 2}
            resource.arguments["value"] = (
                999  # Must not mutate bound args or the next access check.
            )
            return self.checks < fail_at

    @tool(name="search", effect="read")
    def search(value: int = 2) -> int:
        nonlocal called
        called += 1
        return value

    agent = agent_for(search)
    async with Runtime(agents=[agent], authorizer=Revoke()) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, search, record).wait()
        assert called == 0 and result.operation.error_code == "permission_denied"
        assert result.operation.attempts == (1 if fail_at == 3 else 0)


@pytest.mark.asyncio
async def test_authorization_error_is_safe_and_default_owner_does_not_grant_other_scope() -> None:
    class Broken:
        async def authorize(self, actor: RunContext, action: object, resource: object) -> bool:
            raise RuntimeError("private credential text")

    @tool(name="search", effect="read")
    def search() -> str:
        raise AssertionError("Unauthorized tool called")

    agent = agent_for(search)
    async with Runtime(agents=[agent], authorizer=Broken()) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, search, record).wait()
        assert result.operation.error_code == "authorization_error"
        assert "private credential" not in result.operation.model_dump_json()
        forged = record.model_copy(
            update={"scope": record.scope.model_copy(update={"user_id": "bob"})}
        )
        with pytest.raises(RecordNotFound):
            await dispatch(runtime, search, forged, operation_id="other").wait()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retries,transient,expected", [(0, True, 1), (1, True, 2), (2, True, 3), (2, False, 1)]
)
async def test_read_retries_only_explicit_transient_errors(
    retries: int, transient: bool, expected: int
) -> None:
    count = 0

    @tool(name="search", effect="read")
    def search() -> str:
        nonlocal count
        count += 1
        if count < 3:
            if transient:
                raise TransientToolError("private backend message")
            raise RuntimeError("ordinary error")
        return "actual result"

    agent = agent_for(search, Limits(read_tool_retries=retries, retry_backoff_seconds=0.001))
    async with Runtime(agents=[agent]) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, search, record).wait()
        assert result.operation.attempts == expected and count == expected
        assert result.operation.has_result == (expected == 3)
        events = await runtime.storage.events(record.scope, record.state.run_id)
        attempts = [event.data["attempt_id"] for event in events if event.type == "tool.dispatched"]
        assert len(attempts) == len(set(attempts)) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_result", [1, float("nan"), "x" * 65])
async def test_bad_or_oversized_results_are_not_success_and_not_retried(bad_result: object) -> None:
    count = 0

    @tool(name="search", effect="read")
    def search() -> str:
        nonlocal count
        count += 1
        return bad_result  # type: ignore[return-value]

    agent = agent_for(search, Limits(max_tool_result_bytes=64))
    async with Runtime(agents=[agent]) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, search, record).wait()
        assert (
            result.operation.error_code == "output_unavailable" and not result.operation.has_result
        )
        assert count == 1


@pytest.mark.asyncio
async def test_tool_dispatch_limit_is_durable_and_cannot_be_reset_by_new_operation() -> None:
    count = 0

    @tool(name="search", effect="read")
    def search() -> str:
        nonlocal count
        count += 1
        return "actual"

    agent = agent_for(search, Limits(max_tool_calls=1))
    async with Runtime(agents=[agent]) as runtime:
        record = await create_run(runtime, agent)
        await dispatch(runtime, search, record).wait()
        with pytest.raises(CapacityExceeded):
            await dispatch(runtime, search, record, operation_id="second").wait()
        assert count == 1
        checkpoint = await runtime.storage.get_checkpoint(record.scope, record.state.run_id)
        assert checkpoint.data["tool_dispatch_count"] == 1


@pytest.mark.asyncio
async def test_sqlite_result_reopens_without_tool_redispatch(tmp_path: Path) -> None:
    count = 0

    @tool(name="search", effect="read")
    async def search() -> str:
        nonlocal count
        count += 1
        return "actual"

    agent = agent_for(search)
    path = tmp_path / "tools.db"
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, search, record).wait()
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        stored = await OperationJournal(runtime.storage, record.scope).get(
            record.state.run_id, "op"
        )
        assert stored == result.operation
        repeat = await dispatch(runtime, search, record).wait()
        assert repeat.operation == stored and count == 1


@pytest.mark.asyncio
async def test_commit_failure_prevents_actual_dispatch_and_releases_admission() -> None:
    class FaultStorage(MemoryStorage):
        async def _finish(self, commit: bool) -> None:
            if (
                commit
                and self._working is not None
                and any(
                    row.key.kind == "event" and json.loads(row.payload)["type"] == "tool.dispatched"
                    for row in self._working.values()
                )
            ):
                await super()._finish(False)
                raise PersistenceUnavailable("dispatch marker commit failed")
            await super()._finish(commit)

    @tool(name="search", effect="read")
    def search() -> str:
        raise AssertionError("Cannot dispatch without committed marker")

    agent = agent_for(search)
    async with Runtime(agents=[agent], storage=FaultStorage()) as runtime:
        record = await create_run(runtime, agent)
        with pytest.raises(PersistenceUnavailable):
            await dispatch(runtime, search, record).wait()
        stored = await OperationJournal(runtime.storage, record.scope).get(
            record.state.run_id, "op"
        )
        assert stored is not None and stored.status == "prepared" and stored.attempts == 0


@pytest.mark.asyncio
async def test_thread_timeout_retains_slot_lock_and_late_result_is_unused(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    count = 0

    @tool(name="search", effect="read")
    def search() -> str:
        nonlocal count
        count += 1
        entered.set()
        assert release.wait(10)
        raise RuntimeError("late private error")

    agent = agent_for(search, Limits(tool_timeout_seconds=0.05))
    storage = SQLiteStorage(tmp_path / "held.db")
    runtime = Runtime(agents=[agent], storage=storage, limits=Limits(shutdown_grace_seconds=0.02))
    await runtime.open()
    record = await create_run(runtime, agent)
    work = dispatch(runtime, search, record)
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        result = await work.wait()
        assert (
            result.operation.error_code == "tool_timeout"
            and count == 1
            and runtime.active_slots == 1
        )
        with pytest.raises(ShutdownIncomplete):
            await runtime.aclose()
        with pytest.raises(StorageInUse):
            await SQLiteStorage(storage.path).open()
    finally:
        release.set()
        # Wait for the actual function to settle, without depending on OS scheduling in grace.
        for call in tuple(work._calls):
            pending = asyncio.wrap_future(call) if isinstance(call, ThreadFuture) else call
            await asyncio.gather(pending, return_exceptions=True)
        # SQLite cleanup may need another grace window on a slower scheduler. Retry the
        # documented retained-resource close; the initial ShutdownIncomplete/lock checks stand.
        async with asyncio.timeout(5):
            while True:
                try:
                    await runtime.aclose()
                    break
                except ShutdownIncomplete:
                    await asyncio.sleep(0)
    async with SQLiteStorage(storage.path) as reopened:
        saved = await OperationJournal(reopened, record.scope).get(record.state.run_id, "op")
        assert saved is not None and not saved.has_result and saved.error_code == "tool_timeout"


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_tool_but_explicit_cancel_does() -> None:
    entered = asyncio.Event()

    @tool(name="search", effect="read")
    async def search() -> str:
        entered.set()
        await asyncio.Event().wait()
        return "unreachable"

    agent = agent_for(search)
    async with Runtime(agents=[agent]) as runtime:
        record = await create_run(runtime, agent)
        work = dispatch(runtime, search, record)
        await entered.wait()
        waiter = asyncio.create_task(work.wait())
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not work.task.done()
        work.request_cancel()
        with pytest.raises(asyncio.CancelledError):
            await work.wait()
        stored = await OperationJournal(runtime.storage, record.scope).get(
            record.state.run_id, "op"
        )
        assert stored is not None and stored.error_code == "tool_cancelled"


@pytest.mark.asyncio
async def test_bounded_queue_and_revocation_while_waiting() -> None:
    entered, release = threading.Event(), threading.Event()
    queued = asyncio.Event()
    called: list[str] = []

    class Access:
        granted = True

        async def authorize(self, actor: RunContext, action: object, resource: Resource) -> bool:
            assert isinstance(resource, ToolResource)
            if resource.operation_id == "queued":
                queued.set()
            return self.granted

    access = Access()

    @tool(name="search", effect="read")
    def search(value: str) -> str:
        called.append(value)
        entered.set()
        assert release.wait(10)
        return value

    agent = agent_for(search)
    async with Runtime(
        agents=[agent], authorizer=access, limits=Limits(tool_threads=1, tool_queue_size=1)
    ) as runtime:
        records = [await create_run(runtime, agent, f"s{i}") for i in range(3)]
        first = dispatch(runtime, search, records[0], '{"value":"first"}', "first")
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            second = dispatch(runtime, search, records[1], '{"value":"second"}', "queued")
            await queued.wait()
            third = await dispatch(runtime, search, records[2], '{"value":"third"}', "third").wait()
            assert third.operation.error_code == "capacity_exceeded"
            assert third.operation.attempts == 0
            access.granted = False
        finally:
            release.set()
        assert (await first.wait()).operation.has_result
        second_result = await second.wait()
        assert (
            second_result.operation.error_code == "permission_denied"
            and second_result.operation.attempts == 0
        )
        assert called == ["first"]


@pytest.mark.asyncio
async def test_same_operation_concurrency_is_rejected_and_null_result_is_valid() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    @tool(name="search", effect="read")
    async def search() -> None:
        nonlocal count
        count += 1
        entered.set()
        await release.wait()

    agent = agent_for(search)
    async with Runtime(agents=[agent]) as runtime:
        record = await create_run(runtime, agent)
        first = dispatch(runtime, search, record)
        await entered.wait()
        try:
            with pytest.raises(Conflict, match="active dispatcher"):
                await dispatch(runtime, search, record).wait()
        finally:
            release.set()
        result = await first.wait()
        assert result.operation.has_result and result.operation.result is None and count == 1


def test_tool_default_metadata_and_generator_rejection() -> None:
    from drov.errors import ConfigurationError

    @tool(name="unknown")
    def function() -> str:
        raise AssertionError("Decorator must not execute")

    assert (function.revision, function.effect, function.approval) == ("1", "unknown", "policy")
    with pytest.raises(ConfigurationError, match="reserved"):
        tool(name="memory.remember")

    def generator() -> Iterator[str]:
        yield "x"

    with pytest.raises(ConfigurationError, match="generator"):
        tool(name="generator")(generator)


@pytest.mark.asyncio
async def test_replacing_registered_callable_with_same_schema_is_rejected() -> None:
    @tool(name="search", effect="read")
    def first() -> str:
        return "first"

    @tool(name="search", effect="read")
    def second() -> str:
        raise AssertionError("Replacement cannot execute")

    agent = agent_for(first)
    async with Runtime(agents=[agent]) as runtime:
        record = await create_run(runtime, agent)
        agent.tools = (second,)
        with pytest.raises(Conflict, match="changed"):
            await dispatch(runtime, second, record).wait()


@pytest.mark.asyncio
async def test_queue_cancellation_releases_admission_without_dispatch() -> None:
    entered, release = threading.Event(), threading.Event()
    queue_seen = asyncio.Event()
    called: list[str] = []

    class Access:
        async def authorize(self, actor: RunContext, action: object, resource: Resource) -> bool:
            assert isinstance(resource, ToolResource)
            if resource.operation_id == "queued":
                queue_seen.set()
            return True

    @tool(name="search", effect="read")
    def search(value: str) -> str:
        called.append(value)
        entered.set()
        assert release.wait(10)
        return value

    agent = agent_for(search)
    async with Runtime(
        agents=[agent], authorizer=Access(), limits=Limits(tool_threads=1, tool_queue_size=1)
    ) as runtime:
        records = [await create_run(runtime, agent, f"s{i}") for i in range(3)]
        first = dispatch(runtime, search, records[0], '{"value":"first"}', "first")
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            queued = dispatch(runtime, search, records[1], '{"value":"second"}', "queued")
            await queue_seen.wait()
            queued.request_cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued.wait()
            saved = await OperationJournal(runtime.storage, records[1].scope).get(
                records[1].state.run_id, "queued"
            )
            assert saved is not None and saved.status == "not_executed" and saved.attempts == 0
            third = dispatch(runtime, search, records[2], '{"value":"third"}', "third")
        finally:
            release.set()
        await first.wait()
        assert (await third.wait()).operation.has_result
        assert called == ["first", "third"]


@pytest.mark.asyncio
async def test_authorization_timeout_never_dispatches() -> None:
    class Hanging:
        async def authorize(self, actor: RunContext, action: object, resource: Resource) -> bool:
            await asyncio.Event().wait()
            return True

    @tool(name="search", effect="read")
    def search() -> str:
        raise AssertionError("Timed-out authorization cannot grant execution")

    agent = agent_for(search, Limits(advance_seconds=0.02))
    async with Runtime(agents=[agent], authorizer=Hanging()) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, search, record).wait()
        assert (
            result.operation.error_code == "authorization_error" and result.operation.attempts == 0
        )


@pytest.mark.asyncio
async def test_uncooperative_async_tool_keeps_slot_after_timeout() -> None:
    entered, refused, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    @tool(name="search", effect="read")
    async def search() -> str:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            refused.set()
            await release.wait()
        return "late result"

    agent = agent_for(search, Limits(tool_timeout_seconds=0.02))
    runtime = Runtime(agents=[agent], limits=Limits(shutdown_grace_seconds=0.02))
    await runtime.open()
    record = await create_run(runtime, agent)
    work = dispatch(runtime, search, record)
    try:
        await entered.wait()
        result = await work.wait()
        await refused.wait()
        assert result.operation.error_code == "tool_timeout" and runtime.active_slots == 1
        with pytest.raises(ShutdownIncomplete):
            await runtime.aclose()
    finally:
        release.set()
        await runtime.aclose()


@pytest.mark.asyncio
async def test_unexpected_coroutine_result_is_closed_and_not_executed() -> None:
    async def nested() -> str:
        raise AssertionError("Wrong return value must not be awaited as another tool")

    @tool(name="search", effect="read")
    def search() -> str:
        return nested()  # type: ignore[return-value]

    agent = agent_for(search)
    async with Runtime(agents=[agent]) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, search, record).wait()
        assert result.operation.error_code == "output_unavailable"


@pytest.mark.asyncio
async def test_submission_failure_records_no_execution_and_configuration_is_readonly() -> None:
    @tool(name="search", effect="read")
    def search() -> str:
        raise AssertionError("Failed submission cannot execute")

    agent = agent_for(search)
    async with Runtime(agents=[agent]) as runtime:
        with pytest.raises(AttributeError):
            runtime.policy = PermissionPolicy(allow={"search"})  # type: ignore[misc]
        record = await create_run(runtime, agent)
        assert runtime._tool_dispatcher is not None
        with patch.object(
            runtime._tool_dispatcher.pool._executor,
            "submit",
            side_effect=RuntimeError("executor failure"),
        ):
            result = await dispatch(runtime, search, record).wait()
        assert (
            result.operation.status == "not_executed"
            and result.operation.error_code == "tool_dispatch_error"
        )


@pytest.mark.asyncio
async def test_actual_async_tool_cannot_wait_for_its_own_runtime_shutdown() -> None:
    from drov.errors import ConfigurationError

    @tool(name="search", effect="read")
    async def search() -> str:
        with pytest.raises(ConfigurationError, match="own execution"):
            await runtime.aclose()
        return "still open"

    agent = agent_for(search)
    async with Runtime(agents=[agent]) as runtime:
        record = await create_run(runtime, agent)
        result = await dispatch(runtime, search, record).wait()
        assert result.operation.has_result and runtime.storage.is_open
