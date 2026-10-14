"""T-10: hard-kill real SDK processes, with an independently surviving write ledger."""

import asyncio
import json
import sqlite3
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from examples.item_service_fixture import ItemService

WORKER = Path(__file__).with_name("runtime_process_fixture.py")
COMMITS = [
    "accepted",
    "input",
    "attempt",
    "plan",
    "prepared",
    "approval",
    "receipt",
    "dispatch",
    "result",
    "final",
    "terminal",
]


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[ItemService]:
    value = ItemService(tmp_path)
    try:
        await value.open()
        yield value
    finally:
        # A killed SDK may have left the test service waiting before its reply.
        if value.process is not None and value.process.stdin is not None:
            try:
                value.process.stdin.write(b"release\n")
                await value.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
        await value.aclose()


async def launch(path: Path, service: ItemService, **options: str) -> asyncio.subprocess.Process:
    arguments = [
        sys.executable,
        "-I",
        "-B",
        "-X",
        "utf8",
        str(WORKER),
        "--path",
        str(path),
        "--port",
        str(service.port),
    ]
    for name, value in options.items():
        arguments.extend(["--" + name, value])
    return await asyncio.create_subprocess_exec(
        *arguments,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def read_signal(process: asyncio.subprocess.Process) -> dict[str, Any]:
    assert process.stdout is not None
    try:
        # This includes interpreter startup, SQLite fsync and multiple approvals.
        # SDK model/tool and deliberate budget deadlines are measured separately.
        async with asyncio.timeout(45):
            line = await process.stdout.readline()
    except TimeoutError:
        if process.returncode is None:
            process.kill()
        async with asyncio.timeout(10):
            output, errors = await process.communicate()
        pytest.fail("SDK barrier timed out: " + (output + errors).decode(errors="replace"))
    if not line:
        assert process.stderr is not None
        pytest.fail("SDK exited before its barrier: " + (await process.stderr.read()).decode())
    value: dict[str, Any] = json.loads(line)
    return value


async def kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    async with asyncio.timeout(10):
        await process.communicate()


def database_facts(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        rows = connection.execute("SELECT kind,key,payload FROM records").fetchall()
        records: dict[str, list[Any]] = {}
        for kind, _, payload in rows:
            records.setdefault(kind, []).append(json.loads(payload))
        if records.get("run"):
            run = records["run"][0]
            checkpoint = records["checkpoint"][0]
            events = sorted(records["event"], key=lambda event: event["seq"])
            assert run["state"]["state_version"] == checkpoint["state_version"]
            assert [e["seq"] for e in events] == list(range(1, run["last_seq"] + 1))
            assert events[-1]["state_version"] == checkpoint["state_version"]
            assert records["session"][0]["active_run_id"] == (
                None
                if run["state"]["status"] in ("completed", "failed", "cancelled")
                else run["state"]["run_id"]
            )
        return records
    finally:
        connection.close()


async def recover(
    path: Path, service: ItemService, entry: str, kind: str = "", **options: str
) -> dict[str, Any]:
    process = await launch(path, service, mode="recover", entry=entry, kind=kind, **options)
    try:
        result = await read_signal(process)
        async with asyncio.timeout(20):
            _, errors = await process.communicate()
        assert process.returncode == 0, errors.decode()
        return result
    finally:
        await kill(process)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["sdk", "stream"])
@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("boundary", COMMITS)
async def test_hard_kill_each_semantic_commit(
    tmp_path: Path, service: ItemService, entry: str, phase: str, boundary: str
) -> None:
    await check_boundary(tmp_path, service, entry, phase, boundary, "halt")


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("boundary", COMMITS)
async def test_sql_commit_failure_or_lost_response(
    tmp_path: Path, service: ItemService, phase: str, boundary: str
) -> None:
    await check_boundary(tmp_path, service, "sdk", phase, boundary, "fault")


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", COMMITS)
async def test_sqlite_itself_rejects_semantic_commit(
    tmp_path: Path, service: ItemService, boundary: str
) -> None:
    await check_boundary(tmp_path, service, "sdk", "before", boundary, "real_commit")


async def check_boundary(
    tmp_path: Path, service: ItemService, entry: str, phase: str, boundary: str, mode: str
) -> None:
    path = tmp_path / "runtime.db"
    process = await launch(path, service, mode=mode, boundary=boundary, phase=phase, entry=entry)
    try:
        signal = await read_signal(process)
        assert signal["boundary"] == boundary and signal["phase"] == phase
        if mode in ("fault", "real_commit"):
            assert signal["failure"] == "persistence_unavailable"
        if mode == "real_commit":
            assert signal["sqlite_error"] == "SQLITE_CONSTRAINT_FOREIGNKEY"
    finally:
        await kill(process)
    before = database_facts(path)
    stats = await service.stats()
    wrote = boundary in ("result", "final", "terminal")
    assert stats["writes"] == stats["attempts"] == int(wrote)
    expected_event = {
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
    }[boundary]
    events = sorted(before.get("event", []), key=lambda e: e["seq"])
    if phase == "after":
        assert expected_event in [e["type"] for e in events]
    if boundary == "accepted" and phase == "before":
        assert not before
    if boundary == "receipt":
        assert len(before.get("receipt", [])) == int(phase == "after")
    if boundary == "dispatch":
        assert before["operation"][0]["state"] == ("dispatched" if phase == "after" else "prepared")
    if boundary == "result":
        assert before["operation"][0]["state"] == (
            "succeeded" if phase == "after" else "dispatched"
        )
    result = await recover(path, service, entry)
    assert result["outcome"]["status"] == "completed"
    assert result["stats"]["writes"] == result["stats"]["attempts"] == 1, result
    checkpoint = result["checkpoint"]
    dispatched_without_call = boundary == "dispatch" and phase == "after"
    assert checkpoint["tool_dispatch_count"] == (2 if dispatched_without_call else 1)
    uncommitted_model = (
        (boundary == "attempt" and phase == "after")
        or (boundary == "plan" and phase == "before")
        or (boundary == "final" and phase == "before")
    )
    assert checkpoint["model_step_count"] == 2
    assert checkpoint["model_attempt_count"] == (3 if uncommitted_model else 2)
    assert result["outcome"]["usage"]["complete"] is not uncommitted_model
    if not uncommitted_model:
        assert result["outcome"]["usage"]["total_tokens"] == 10
        assert result["outcome"]["usage"]["cost_amount"] == "0.02"
    else:
        assert result["outcome"]["usage"]["total_tokens"] is None
        assert result["outcome"]["usage"]["cost_amount"] is None
    if before.get("checkpoint"):
        previous = before["checkpoint"][0]["data"]
        for name in ("model_step_count", "model_attempt_count", "tool_dispatch_count"):
            assert checkpoint[name] >= previous.get(name, 0)
        if previous.get("pending_model_call"):
            saved = next(
                i for i in before["model_input"] if i["item_id"] == previous["pending_model_call"]
            )
            assert result["calls"][0] == saved["data"]["request"]
    if boundary in ("final", "terminal") and phase == "after":
        assert result["calls"] == []
    database_facts(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["write_before_reply", "model_reply", "model_response"])
async def test_hard_kill_in_real_call_or_complete_uncommitted_model(
    tmp_path: Path, service: ItemService, boundary: str
) -> None:
    path = tmp_path / "call.db"
    process = await launch(path, service, mode="halt", boundary=boundary)
    try:
        if boundary == "write_before_reply":
            assert service.process is not None
            signal = await read_signal(service.process)
        else:
            signal = await read_signal(process)
        assert signal["boundary"] == boundary
    finally:
        await kill(process)
        if boundary == "write_before_reply":
            assert service.process is not None and service.process.stdin is not None
            service.process.stdin.write(b"release\n")
            await service.process.stdin.drain()
    before = database_facts(path)
    result = await recover(path, service, "sdk")
    assert result["outcome"]["status"] == "completed"
    assert result["stats"]["writes"] == result["stats"]["attempts"] == 1
    if boundary == "write_before_reply":
        assert before["operation"][0]["state"] == "dispatched"
        assert result["initial"]["state"]["reason"] == "reconciliation"
        assert result["checkpoint"]["tool_dispatch_count"] == 1
    else:
        assert not before.get("model_response") and not before.get("operation")
        assert result["calls"][0] == signal["request"]
        assert result["checkpoint"]["model_step_count"] == 2
        assert result["checkpoint"]["model_attempt_count"] == 3
        assert result["outcome"]["usage"]["complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["thread", "task"])
@pytest.mark.parametrize("ending", ["release", "kill"])
async def test_real_noncooperative_call_retains_lock_until_stopped(
    tmp_path: Path, service: ItemService, kind: str, ending: str
) -> None:
    path = tmp_path / "owner.db"
    process = await launch(path, service, mode="halt", kind=kind)
    try:
        first_signal = await read_signal(process)
        assert first_signal.get("boundary") == "live_call", first_signal
        assert process.stdin is not None
        process.stdin.write(b"cancel\n")
        await process.stdin.drain()
        owned = await read_signal(process)
        assert owned["boundary"] == "shutdown_incomplete" and owned["slots"] == 1
        assert owned["outcome"]["status"] == "suspended"
        assert owned["outcome"]["reason"] == "reconciliation"
        probe = await launch(path, service, mode="probe")
        try:
            assert await read_signal(probe) == {"locked": True}
            async with asyncio.timeout(10):
                await probe.communicate()
            assert probe.returncode == 0
        finally:
            await kill(probe)
        if ending == "release":
            process.stdin.write(b"release\n")
            await process.stdin.drain()
            assert await read_signal(process) == {"closed": True, "slots": 0}
            async with asyncio.timeout(10):
                _, errors = await process.communicate()
            assert process.returncode == 0, errors.decode()
    finally:
        await kill(process)
    result = await recover(path, service, "sdk", kind)
    assert result["outcome"]["status"] == "cancelled"
    assert result["calls"] == []
    assert result["checkpoint"]["tool_dispatch_count"] == 1
    assert result["stats"]["writes"] == result["stats"]["attempts"] == 1
    database_facts(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before", "after"])
async def test_cancel_request_commit_survives_hard_kill(
    tmp_path: Path, service: ItemService, phase: str
) -> None:
    path = tmp_path / "cancel.db"
    process = await launch(path, service, mode="halt", boundary="cancel", phase=phase)
    try:
        assert (await read_signal(process))["boundary"] == "cancel"
    finally:
        await kill(process)
    before = database_facts(path)
    assert before["run"][0]["state"]["cancel_requested"] is (phase == "after")
    result = await recover(path, service, "sdk")
    assert result["outcome"]["status"] == ("cancelled" if phase == "after" else "completed")
    assert result["stats"]["writes"] == (0 if phase == "after" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("entry", ["sdk", "stream"])
async def test_partial_success_plan_recovers_each_original_call_once(
    tmp_path: Path, service: ItemService, phase: str, entry: str
) -> None:
    path = tmp_path / "partial.db"
    process = await launch(
        path, service, mode="halt", boundary="result", phase=phase, entry=entry, count="2"
    )
    try:
        assert (await read_signal(process))["boundary"] == "result"
    finally:
        await kill(process)
    before = database_facts(path)
    assert len(before["model_response"][0]["data"]["operation_ids"]) == 2
    assert (await service.stats())["writes"] == 1
    result = await recover(path, service, entry, count="2")
    assert result["outcome"]["status"] == "completed"
    assert result["stats"]["writes"] == result["stats"]["attempts"] == 2
    assert len(result["stats"]["operations"]) == 2
    assert result["checkpoint"]["tool_dispatch_count"] == 2
    assert result["checkpoint"]["model_step_count"] == 2
    assert result["checkpoint"]["model_attempt_count"] == 2
    tool_messages = [m for m in result["calls"][0]["messages"] if m["role"] == "tool"]
    assert [m["call_id"] for m in tool_messages] == ["write-1", "write-2"]
    assert [m["content"]["result"]["item_id"] for m in tool_messages] == ["item-01", "item-02"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,phase",
    [
        ("halt", "before"),
        ("halt", "after"),
        ("fault", "before"),
        ("fault", "after"),
        ("real_commit", "before"),
    ],
)
async def test_memory_mutation_result_and_budget_survive_or_rollback_together(
    tmp_path: Path, service: ItemService, mode: str, phase: str
) -> None:
    path = tmp_path / "memory.db"
    process = await launch(
        path, service, mode=mode, boundary="memory", phase=phase, scenario="memory"
    )
    try:
        assert (await read_signal(process))["boundary"] == "memory"
    finally:
        await kill(process)
    before = database_facts(path)
    assert len(before.get("memory", [])) == int(phase == "after")
    assert before["operation"][0]["state"] == ("succeeded" if phase == "after" else "prepared")
    assert before["checkpoint"][0]["data"].get("tool_dispatch_count", 0) == int(phase == "after")
    result = await recover(path, service, "sdk", scenario="memory")
    assert result["outcome"]["status"] == "completed"
    assert result["stats"]["writes"] == 0
    assert result["checkpoint"]["tool_dispatch_count"] == 1
    assert len(result["memories"]) == 1 and result["memories"][0]["version"] == 1
    assert result["memories"][0]["content"] == "synthetic concise"
    assert sum(e["type"] == "memory.changed" for e in result["events"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", ["attempt", "time"])
async def test_hard_restart_never_resets_exhausted_attempt_or_time_budget(
    tmp_path: Path, service: ItemService, budget: str
) -> None:
    path = tmp_path / "budget.db"
    process = await launch(path, service, mode="halt", boundary="model_reply", budget=budget)
    try:
        assert (await read_signal(process))["boundary"] == "model_reply"
    finally:
        await kill(process)
    result = await recover(path, service, "sdk", budget=budget)
    assert result["outcome"]["status"] == "failed"
    assert result["outcome"]["error"]["code"] == (
        "model_attempt_limit" if budget == "attempt" else "limit_exceeded"
    )
    assert result["calls"] == []
    assert result["stats"]["writes"] == 0
    assert result["checkpoint"]["model_step_count"] == 1
    assert result["checkpoint"]["model_attempt_count"] == 1
    assert not result["outcome"]["usage"]["complete"]
    assert result["outcome"]["usage"]["total_tokens"] is None
