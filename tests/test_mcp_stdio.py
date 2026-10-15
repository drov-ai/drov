"""T-12: real official-SDK stdio processes, frozen tools, permissions and cleanup."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

pytest.importorskip("mcp")

from drov import (  # noqa: E402
    Agent,
    ApprovalDecision,
    Limits,
    PermissionPolicy,
    RunContext,
    Runtime,
    SQLiteStorage,
)
from drov.errors import ConfigurationError, UnsupportedSchema  # noqa: E402
from drov.integrations.mcp import MCPStdio, MCPTool, MCPToolPolicy, _schema  # noqa: E402
from drov.models import ScriptedModel  # noqa: E402
from drov.models.protocol import ModelResponse, ToolCall  # noqa: E402
from drov.runtime import ResourceBinding  # noqa: E402
from drov.runtime.registration import fingerprint  # noqa: E402
from drov.storage.base import Storage
from drov.tools.operations import OperationJournal, ToolOperation  # noqa: E402
from drov.types import ApprovalRequest, Completed, Suspended  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "examples/mcp_stdio_fixture.py"
ACTOR = RunContext(tenant_id="synthetic", user_id="alice")


def server(tmp_path: Path, mode: str = "normal", **kwargs: Any) -> MCPStdio:
    return MCPStdio(
        namespace="fixture",
        revision="1",
        command=sys.executable,
        args=[
            "-I",
            "-B",
            str(FIXTURE),
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
            "--control",
            str(tmp_path / "control"),
            "--mode",
            mode,
        ],
        **kwargs,
    )


def ledger(tmp_path: Path, event: str) -> list[dict[str, Any]]:
    path = tmp_path / "ledger.jsonl"
    return (
        [
            row
            for line in path.read_text().splitlines()
            if (row := json.loads(line))["event"] == event
        ]
        if path.exists()
        else []
    )


def choose(tools: tuple[MCPTool, ...], remote: str = "echo/name") -> MCPTool:
    return next(tool for tool in tools if tool.remote_name == remote)


def agent_for(tool: MCPTool, *, args: dict[str, Any] | None = None, **kwargs: Any) -> Agent[str]:
    model = ScriptedModel(
        [
            ModelResponse(
                finish_reason="tool_calls",
                tool_calls=[
                    ToolCall(
                        call_id="original",
                        name=tool.name,
                        arguments={"value": 7} if args is None else args,
                    )
                ],
            ),
            ModelResponse(finish_reason="completed", message="done"),
        ]
    )
    return Agent(name="mcp-agent", revision="1", model=model, tools=[tool], **kwargs)


async def get_operations(storage: Storage, run_id: str) -> list[ToolOperation]:
    record = await storage.get_run(ACTOR, run_id)
    results = [
        await OperationJournal(storage, ACTOR).get(run_id, item.operation_id)
        for item in record.state.operations
    ]
    assert all(item is not None for item in results)
    return cast(list[ToolOperation], results)


async def outcome(runtime: Runtime, agent: Agent[str], stream: bool) -> Any:
    if not stream:
        return await runtime.run(agent, "synthetic", session_id="s", context=ACTOR)
    async with runtime.stream(agent, "synthetic", session_id="s", context=ACTOR) as events:
        async for _ in events:
            pass
        return await events.result()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_real_pagination_read_call_and_process_cleanup(tmp_path: Path, stream: bool) -> None:
    connection = server(tmp_path)
    tools = await connection.discover(policies={"echo/name": MCPToolPolicy(effect="read")})
    assert len(tools) == 2
    assert [row["cursor"] for row in ledger(tmp_path, "list")] == [None, "page2"]
    assert len(ledger(tmp_path, "exit")) == 1
    tool = choose(tools)
    agent = agent_for(tool)
    storage = SQLiteStorage(tmp_path / "run.db")
    async with Runtime(
        agents=[agent],
        storage=storage,
        dependencies={connection.dependency: ResourceBinding.owned(connection)},
    ) as runtime:
        result = await outcome(runtime, agent, stream)
        assert isinstance(result, Completed)
        assert connection.protocol_version == "2025-11-25"
        operations = await get_operations(storage, result.run_id)
        assert len(operations) == 1 and operations[0].status == "succeeded"
        assert operations[0].result == {
            "content": [{"type": "text", "text": "synthetic value"}],
            "structuredContent": {"value": 7},
        }
    assert ledger(tmp_path, "call")[0]["arguments"] == {"value": 7}  # absent defaults stay absent
    assert len(ledger(tmp_path, "call")) == 1
    assert len(ledger(tmp_path, "exit")) == 2
    assert len(ledger(tmp_path, "list")) == 6  # no hidden SDK output-schema re-list
    assert not connection.is_open


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("approved", [False, True])
async def test_annotations_cannot_bypass_original_approval(
    tmp_path: Path, stream: bool, approved: bool
) -> None:
    connection = server(tmp_path)
    tool = choose(await connection.discover(), "write")
    assert tool.effect == "unknown" and tool.approval == "policy"
    agent = agent_for(tool)
    async with Runtime(
        agents=[agent],
        storage=SQLiteStorage(tmp_path / "run.db"),
        dependencies={connection.dependency: ResourceBinding.owned(connection)},
    ) as runtime:
        result = await outcome(runtime, agent, stream)
        assert isinstance(result, Suspended) and isinstance(result.pending, ApprovalRequest)
        assert not ledger(tmp_path, "call")
        assert result.pending.arguments_summary == {"value": 7}
        resumed = await runtime.resume(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=approved),
            context=ACTOR,
        )
        assert isinstance(resumed, Completed)
        assert len(ledger(tmp_path, "call")) == int(approved)
        assert len(cast(ScriptedModel, agent.model).calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["schema-change", "description-change"])
@pytest.mark.parametrize("at_open", [False, True])
async def test_drift_refuses_dispatch(tmp_path: Path, mode: str, at_open: bool) -> None:
    connection = server(tmp_path)
    tool = choose(await connection.discover(policies={"echo/name": MCPToolPolicy(effect="read")}))
    agent = agent_for(tool)
    runtime = Runtime(
        agents=[agent], dependencies={connection.dependency: ResourceBinding.owned(connection)}
    )
    if at_open:
        (tmp_path / "control").write_text(mode)
        with pytest.raises(ConfigurationError):
            await runtime.open()
        await runtime.aclose()
    else:
        async with runtime:
            (tmp_path / "control").write_text(mode)
            result = await outcome(runtime, agent, False)
            operation = (await get_operations(runtime.storage, result.run_id))[0]
            assert operation.status == "failed_known"
    assert not ledger(tmp_path, "call")
    assert not connection.is_open


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args", [{}, {"value": 0}, {"value": True}, {"value": 7, "effect": "read"}]
)
async def test_invalid_arguments_never_reach_process(tmp_path: Path, args: dict[str, Any]) -> None:
    connection = server(tmp_path)
    tool = choose(await connection.discover(policies={"echo/name": MCPToolPolicy(effect="read")}))
    agent = agent_for(tool, args=args)
    async with Runtime(
        agents=[agent], dependencies={connection.dependency: ResourceBinding.owned(connection)}
    ) as runtime:
        result = await outcome(runtime, agent, False)
        operation = (await get_operations(runtime.storage, result.run_id))[0]
        assert operation.status == "not_executed" and operation.error_code == "invalid_arguments"
    assert not ledger(tmp_path, "call")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["error", "rpc-error", "bad-content", "bad-output", "sampling", "exit", "hang"]
)
@pytest.mark.parametrize("effect", ["read", "write"])
async def test_errors_disconnect_timeout_never_retry_write(
    tmp_path: Path, mode: str, effect: str
) -> None:
    connection = server(tmp_path, timeout_seconds=5 if mode == "hang" else 10)
    tool = choose(
        await connection.discover(policies={"echo/name": MCPToolPolicy(effect=cast(Any, effect))})
    )
    agent = agent_for(tool)
    async with Runtime(
        agents=[agent],
        policy=PermissionPolicy(allow={tool.name}),
        dependencies={connection.dependency: ResourceBinding.owned(connection)},
    ) as runtime:
        (tmp_path / "control").write_text(mode)
        result = await outcome(runtime, agent, False)
        operation = (await get_operations(runtime.storage, result.run_id))[0]
        assert operation.status == ("failed_known" if effect == "read" else "unknown")
        assert operation.attempts == 1
        assert len(ledger(tmp_path, "call")) == 1
        assert "synthetic-private-error" not in operation.model_dump_json()
        assert not connection.is_open


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "duplicate",
        "cursor-cycle",
        "no-tools",
        "unsupported-schema",
        "list-hang",
        "init-hang",
        "task-required",
    ],
)
async def test_discovery_failure_closes_child(tmp_path: Path, mode: str) -> None:
    connection = server(tmp_path, mode, timeout_seconds=5 if mode.endswith("hang") else 10)
    expected = TimeoutError if mode.endswith("hang") else ConfigurationError
    with pytest.raises(expected):
        await connection.discover()
    assert not ledger(tmp_path, "call")
    await connection.aclose()
    assert connection._preflight is not None and connection._preflight.task.done()
    assert ledger(tmp_path, "start")
    if mode == "list-hang":
        assert ledger(tmp_path, "list")


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [{"max_pages": 1}, {"max_tools": 1}, {"max_listing_bytes": 10}])
async def test_discovery_limits(tmp_path: Path, limit: dict[str, int]) -> None:
    connection = server(tmp_path, **cast(dict[str, Any], limit))
    with pytest.raises(ConfigurationError):
        await connection.discover()
    await connection.aclose()
    assert len(ledger(tmp_path, "start")) == len(ledger(tmp_path, "exit")) == 1


@pytest.mark.asyncio
async def test_environment_whitelist_and_namespace_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DROV_MCP_TEST_SECRET", "synthetic-only-do-not-inherit")
    connection = server(tmp_path)
    tools = await connection.discover()
    assert ledger(tmp_path, "start")[0]["inherited_test_secret"] is False
    assert len({tool.name for tool in tools}) == 2
    other = MCPStdio(
        namespace="other",
        revision="1",
        command=sys.executable,
        args=["-I", "-B", str(FIXTURE)],
        env={"DROV_MCP_TEST_SECRET": "explicit-synthetic"},
    )
    other_tools = await other.discover()
    assert set(tool.name for tool in tools).isdisjoint(tool.name for tool in other_tools)


@pytest.mark.asyncio
async def test_borrowed_and_wrong_dependency_startup_cleanup(tmp_path: Path) -> None:
    connection = server(tmp_path)
    tool = choose(await connection.discover(policies={"echo/name": MCPToolPolicy(effect="read")}))
    agent = agent_for(tool)
    runtime = Runtime(agents=[agent], dependencies={connection.dependency: connection})
    with pytest.raises(ConfigurationError):
        await runtime.open()
    await runtime.aclose()
    assert len(ledger(tmp_path, "start")) == 1
    await connection.open()
    async with Runtime(agents=[agent], dependencies={connection.dependency: connection}) as runtime:
        assert isinstance(await outcome(runtime, agent, False), Completed)
    assert connection.is_open
    await connection.aclose()
    assert len(ledger(tmp_path, "exit")) == 2


@pytest.mark.asyncio
async def test_missing_dependency_and_start_failure_releases_storage(tmp_path: Path) -> None:
    connection = server(tmp_path)
    tool = choose(await connection.discover())
    agent = agent_for(tool)
    runtime = Runtime(
        agents=[agent],
        storage=SQLiteStorage(tmp_path / "run.db"),
        dependencies={
            connection.dependency: ResourceBinding.owned(connection),
            "broken": ResourceBinding.factory(fail_factory),
        },
    )
    with pytest.raises(ConfigurationError):
        await runtime.open()
    await runtime.aclose()
    assert len(ledger(tmp_path, "exit")) == 2
    async with SQLiteStorage(tmp_path / "run.db"):
        pass
    async with Runtime(agents=[Agent(name="empty", revision="1", model=ScriptedModel([]))]):
        pass
    runtime = Runtime(agents=[agent])
    with pytest.raises(ConfigurationError):
        await runtime.open()
    await runtime.aclose()


async def fail_factory() -> object:
    raise ConfigurationError("synthetic startup failure")


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_cancel_hung_call_retains_cleanup_owner(tmp_path: Path, stream: bool) -> None:
    connection = server(tmp_path, timeout_seconds=20)
    tool = choose(await connection.discover(policies={"echo/name": MCPToolPolicy(effect="read")}))
    agent = agent_for(tool)
    async with Runtime(
        agents=[agent],
        limits=Limits(shutdown_grace_seconds=10),
        dependencies={connection.dependency: ResourceBinding.owned(connection)},
    ) as runtime:
        (tmp_path / "control").write_text("hang")
        if stream:
            events = runtime.stream(agent, "synthetic", session_id="s", context=ACTOR)
            await events.__aenter__()
            run_id = events.run_id
        else:
            ref = await runtime.start(agent, "synthetic", session_id="s", context=ACTOR)
        async with asyncio.timeout(5):
            while not ledger(tmp_path, "call"):  # noqa: ASYNC110 - real subprocess file barrier
                await asyncio.sleep(0.01)
        await runtime.cancel(run_id if stream else ref.run_id, context=ACTOR)
        if stream:
            await events.__aexit__(None, None, None)
        async with asyncio.timeout(10):
            while runtime.active_slots:  # noqa: ASYNC110 - wait for actual ownership release
                await asyncio.sleep(0.01)
    assert len(ledger(tmp_path, "call")) == 1
    assert not connection.is_open


@pytest.mark.asyncio
@pytest.mark.parametrize("part", ["parameters", "result"])
async def test_fingerprint_and_mutable_schema_guard(tmp_path: Path, part: str) -> None:
    connection = server(tmp_path)
    tool = choose(await connection.discover())
    agent = agent_for(tool)
    before = fingerprint(agent)
    assert before == fingerprint(agent)
    if part == "parameters":
        tool.parameters["properties"]["value"]["minimum"] = -100
    else:
        tool.result_codec.schema["additionalProperties"] = True
    with pytest.raises(ConfigurationError):
        fingerprint(agent)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "additionalProperties": True},
        {"type": "object", "$defs": {}},
        {
            "type": "object",
            "properties": {"v": {"anyOf": [{"type": "integer"}, {"type": "string"}]}},
        },
        {"type": "object", "properties": {"v": {"type": "integer", "default": "bad"}}},
    ],
)
def test_unsupported_schemas_fail_explicitly(schema: dict[str, Any]) -> None:
    with pytest.raises(UnsupportedSchema):
        _schema(schema, root=True)


@pytest.mark.parametrize(
    "config",
    [
        {"command": "python"},
        {"args": "arbitrary shell text"},
        {"timeout_seconds": float("nan")},
        {"close_timeout_seconds": 0},
        {"max_pages": False},
        {"env": {"INVALID=NAME": "x"}},
        {"inherit_env": "PATH"},
        {"cwd": "relative"},
    ],
)
def test_invalid_host_configuration_is_inert(config: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError):
        MCPStdio(**({"namespace": "fixture", "revision": "1", "command": sys.executable} | config))


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel.WaitForSingleObject(handle, 0) == 258)
        finally:
            kernel.CloseHandle(handle)
    import subprocess

    status = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return bool(status) and not status.startswith("Z")


@pytest.mark.asyncio
async def test_stubborn_process_tree_is_actually_stopped(tmp_path: Path) -> None:
    connection = server(tmp_path, "stubborn")
    await connection.discover()
    assert len(ledger(tmp_path, "child")) == 1
    for row in ledger(tmp_path, "start") + ledger(tmp_path, "child"):
        assert not process_alive(row.get("child_pid", row["pid"]))
    await connection.open()
    await connection.aclose()
    for row in ledger(tmp_path, "start") + ledger(tmp_path, "child"):
        assert not process_alive(row.get("child_pid", row["pid"]))


@pytest.mark.asyncio
async def test_close_timeout_retains_runtime_lock_until_cleanup_finishes(tmp_path: Path) -> None:
    from drov.errors import ShutdownIncomplete, StorageInUse

    connection = server(tmp_path)
    tool = choose(await connection.discover())
    agent = agent_for(tool)
    runtime = Runtime(
        agents=[agent],
        storage=SQLiteStorage(tmp_path / "run.db"),
        limits=Limits(shutdown_grace_seconds=0.05),
        dependencies={connection.dependency: ResourceBinding.owned(connection)},
    )
    await runtime.open()
    assert connection._session is not None
    original_close = connection._session.close
    gate = asyncio.Event()

    async def delayed() -> None:
        await gate.wait()
        await original_close()

    connection._session.close = delayed  # type: ignore[method-assign]
    with pytest.raises(ShutdownIncomplete):
        await runtime.aclose()
    blocked = SQLiteStorage(tmp_path / "run.db")
    with pytest.raises(StorageInUse):
        await blocked.open()
    await blocked.aclose()
    gate.set()
    await connection.aclose()
    await runtime.aclose()
    async with SQLiteStorage(tmp_path / "run.db"):
        pass
    assert len(ledger(tmp_path, "exit")) == 2


@pytest.mark.asyncio
async def test_persistent_original_plan_resumes_with_new_process(tmp_path: Path) -> None:
    connection = server(tmp_path)
    tool = choose(await connection.discover(), "write")
    agent = agent_for(tool)
    async with Runtime(
        agents=[agent],
        storage=SQLiteStorage(tmp_path / "run.db"),
        dependencies={connection.dependency: ResourceBinding.owned(connection)},
    ) as runtime:
        result = await outcome(runtime, agent, False)
        assert isinstance(result, Suspended) and isinstance(result.pending, ApprovalRequest)
    assert not ledger(tmp_path, "call")
    replacement = server(tmp_path)
    original_tool = choose(await replacement.discover(), "write")
    resumed_agent: Agent[str] = Agent(
        name=agent.name,
        revision=agent.revision,
        tools=[original_tool],
        model=ScriptedModel([ModelResponse(finish_reason="completed", message="resumed")]),
    )
    assert fingerprint(agent) == fingerprint(resumed_agent)
    async with Runtime(
        agents=[resumed_agent],
        storage=SQLiteStorage(tmp_path / "run.db"),
        dependencies={replacement.dependency: ResourceBinding.owned(replacement)},
    ) as runtime:
        resumed = await runtime.resume(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ACTOR,
        )
        assert isinstance(resumed, Completed)
    assert len(ledger(tmp_path, "call")) == 1
    assert len(ledger(tmp_path, "start")) == len(ledger(tmp_path, "exit")) == 4


@pytest.mark.asyncio
async def test_policy_deny_and_result_budget_use_common_dispatcher(tmp_path: Path) -> None:
    for denied in (True, False):
        connection = server(tmp_path)
        tool = choose(
            await connection.discover(policies={"echo/name": MCPToolPolicy(effect="read")})
        )
        agent = agent_for(tool, limits=Limits(max_tool_result_bytes=1 if not denied else 65536))
        async with Runtime(
            agents=[agent],
            policy=PermissionPolicy(deny={tool.name} if denied else set()),
            dependencies={connection.dependency: ResourceBinding.owned(connection)},
        ) as runtime:
            result = await outcome(runtime, agent, False)
            operation = (await get_operations(runtime.storage, result.run_id))[0]
            assert operation.status == ("not_executed" if denied else "failed_known")
            assert operation.error_code == ("permission_denied" if denied else "output_unavailable")
        assert len(ledger(tmp_path, "call")) == int(not denied)


@pytest.mark.asyncio
async def test_owned_open_resource_and_factory(tmp_path: Path) -> None:
    for factory in (False, True):
        connection = server(tmp_path)
        tool = choose(
            await connection.discover(policies={"echo/name": MCPToolPolicy(effect="read")})
        )
        agent = agent_for(tool)

        async def create(value: MCPStdio = connection) -> MCPStdio:
            await value.open()
            return value

        binding = ResourceBinding.factory(create) if factory else ResourceBinding.owned(connection)
        if not factory:
            await connection.open()
        async with Runtime(
            agents=[agent], dependencies={connection.dependency: binding}
        ) as runtime:
            assert isinstance(await outcome(runtime, agent, False), Completed)
        assert not connection.is_open
    assert len(ledger(tmp_path, "call")) == 2


@pytest.mark.asyncio
async def test_discovery_cancel_and_initialization_failure_leave_no_process(tmp_path: Path) -> None:
    connection = server(tmp_path, "list-hang", timeout_seconds=20)
    task = asyncio.create_task(connection.discover())
    async with asyncio.timeout(5):
        while not ledger(tmp_path, "list"):  # noqa: ASYNC110 - actual subprocess barrier
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await connection.aclose()
    for row in ledger(tmp_path, "start"):
        assert not process_alive(row["pid"])


@pytest.mark.asyncio
async def test_missing_executable_is_clean_configuration_failure(tmp_path: Path) -> None:
    connection = MCPStdio(
        namespace="missing", revision="1", command=str(tmp_path / "missing-executable")
    )
    with pytest.raises(ConfigurationError):
        await connection.discover()
    await connection.aclose()


@pytest.mark.asyncio
async def test_unlisted_policy_and_cross_connection_binding_refused(tmp_path: Path) -> None:
    connection = server(tmp_path)
    with pytest.raises(ConfigurationError):
        await connection.discover(policies={"absent": MCPToolPolicy(effect="read")})
    await connection.aclose()
    first = server(tmp_path)
    tool = choose(await first.discover())
    replacement = MCPStdio(
        namespace="different", revision="1", command=sys.executable, args=["-I", "-B", str(FIXTURE)]
    )
    await replacement.discover()
    agent = agent_for(tool)
    runtime = Runtime(
        agents=[agent], dependencies={first.dependency: ResourceBinding.owned(replacement)}
    )
    with pytest.raises(ConfigurationError):
        await runtime.open()
    await runtime.aclose()
    assert not replacement.is_open


@pytest.mark.asyncio
async def test_missing_extra_fails_before_process_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = server(tmp_path)
    monkeypatch.setitem(sys.modules, "mcp", None)
    with pytest.raises(ConfigurationError, match=r"drov\[mcp\]"):
        await connection.discover()
    assert not ledger(tmp_path, "start") and not connection.is_open
