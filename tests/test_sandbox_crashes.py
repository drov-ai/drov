"""Real SDK/stream SIGKILL boundaries, SQLite/bytes reopening and original Docker cleanup."""

import asyncio
import json
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from drov.sandbox._engine import UnixEngine
from tests.test_runtime_crashes import database_facts, kill, read_signal
from tests.test_sandbox_docker import config as config

pytestmark = pytest.mark.docker
WORKER = Path(__file__).with_name("sandbox_process_fixture.py")
BOUNDARIES = [
    "input_staged",
    "input_admit",
    "container_reply",
    "container_ack",
    "inputs_installed",
    "exec_reply",
    "exec_create_intent",
    "exec_ack",
    "exec_start_intent",
    "code_reply",
    "code_running",
    "observation",
    "output_reserved",
    "output_planned",
    "output_byte_fsynced",
    "output_staged",
    "destroy_reply",
    "destroyed",
    "publish",
]
DURABLE = {
    "input_admit",
    "container_ack",
    "exec_create_intent",
    "exec_ack",
    "exec_start_intent",
    "observation",
    "output_reserved",
    "destroyed",
    "publish",
}


async def launch(root: Path, config_path: Path, **options: str) -> asyncio.subprocess.Process:
    args = [
        sys.executable,
        "-I",
        "-B",
        str(WORKER),
        "--directory",
        str(root),
        "--config",
        str(config_path),
    ]
    for key, value in options.items():
        args.extend(["--" + key, value])
    return await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )


def ledger(root: Path) -> list[dict[str, Any]]:
    path = root / "control-ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["sdk", "stream"])
@pytest.mark.parametrize(
    "boundary,phase",
    [(b, p) for b in BOUNDARIES for p in (["before", "after"] if b in DURABLE else ["after"])],
)
async def test_hard_kill_and_reopen_original_file_execution(
    tmp_path: Path, config: dict[str, str], entry: str, boundary: str, phase: str
) -> None:
    config_path = tmp_path / "fixture-config.json"
    config_path.write_text(json.dumps(config))
    process = await launch(tmp_path, config_path, boundary=boundary, phase=phase, entry=entry)
    try:
        signal = await read_signal(process)
        assert signal["barrier"] == boundary
        if boundary == "code_running":
            state = await UnixEngine(config["host"], control_seconds=5).inspect_exec(
                signal["exec_id"]
            )
            assert state["Running"] is True
    finally:
        await kill(process)
    before = ledger(tmp_path)
    facts = database_facts(tmp_path / "state.db")
    recovery = await launch(tmp_path, config_path, mode="recover")
    try:
        result = await read_signal(recovery)
        async with asyncio.timeout(25):
            _, errors = await recovery.communicate()
        assert recovery.returncode == 0, errors.decode()
    finally:
        await kill(recovery)
    after = ledger(tmp_path)
    # A separate durable control log survives the killed SDK. It records request
    # attempts/completed replies, not arbitrary claims made by untrusted code.
    assert [
        item for item in before if item["kind"] in ("create", "exec_create", "start_attempt")
    ] == [item for item in after if item["kind"] in ("create", "exec_create", "start_attempt")]
    assert sum(item["kind"] == "start_attempt" for item in after) <= 1
    recoverable = boundary in ("output_staged", "destroy_reply", "destroyed", "publish")
    if recoverable:
        assert result["status"] == "completed" and result["bytes"] == "value\n60\n"
        assert result["operation_status"] == ["succeeded"]
    elif boundary in ("input_staged", "input_admit"):
        assert not any(item["kind"] == "create" for item in after)
        assert (
            result["operation_status"] == []
            if facts.get("run")
            else result["status"] == "no_unfinished"
        )
    else:
        assert result["status"] == "completed" and result["operation_status"] == ["failed_known"]
        assert "bytes" not in result
    final = database_facts(tmp_path / "state.db")
    if final.get("run"):
        assert final["run"][0]["state"]["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["sdk", "stream"])
async def test_unacknowledged_absent_creation_retains_capacity_and_close_lock(
    tmp_path: Path, config: dict[str, str], entry: str
) -> None:
    import sqlite3

    from drov.sandbox import DockerSandbox
    from drov.sandbox._leases import SandboxLease

    path = tmp_path / "fixture-config.json"
    path.write_text(json.dumps(config))
    process = await launch(tmp_path, path, mode="retained", entry=entry)
    try:
        signal = await read_signal(process)
        assert signal["barrier"] == "retained"
        with closing(sqlite3.connect(tmp_path / "state.db")) as connection:
            payload, active = connection.execute(
                "SELECT payload,active FROM sandbox_leases"
            ).fetchone()
        lease = SandboxLease.from_json(payload)
        assert active == 1 and not lease.destroyed and lease.container_id is None
        async with DockerSandbox(
            docker=config["docker"], host=config["host"], image=config["image"]
        ) as backend:
            assert await backend.resource(lease) is None
        assert not ledger(tmp_path)
    finally:
        await kill(process)
    facts = database_facts(tmp_path / "state.db")
    assert facts["run"][0]["state"]["operations"][0]["status"] == "unknown"
    # Process death frees OS locks; it supplies no evidence that a sent create
    # could never arrive. The durable lease deliberately remains unresolved.
