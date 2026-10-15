"""SIGKILL and new-process recovery of actual E2B resources, SDK and stream."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from tests.e2b_support import configuration, configured, reserve
from tests.test_runtime_crashes import kill, read_signal

pytestmark = pytest.mark.live
WORKER = Path(__file__).with_name("e2b_process_fixture.py")
BOUNDARIES = [
    (b, phase)
    for b in ("remote_ack", "start_intent", "observation", "destroyed", "publish")
    for phase in ("before", "after")
] + [
    (b, "after")
    for b in ("create_reply", "code_reply", "code_running", "output_staged", "kill_reply")
]


def ledger(directory: Path) -> list[dict[str, Any]]:
    path = directory / "control.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def launch(directory: Path, config: str, **opts: str) -> asyncio.subprocess.Process:
    args = [
        sys.executable,
        "-I",
        "-B",
        str(WORKER),
        "--directory",
        str(directory),
        "--config",
        config,
    ]
    for name, value in opts.items():
        args.extend(["--" + name, value])
    return await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["sdk", "stream"])
@pytest.mark.parametrize("boundary,phase", BOUNDARIES)
async def test_real_cloud_hard_kill_reopens_original_only(
    entry: str, boundary: str, phase: str
) -> None:
    config = configuration()
    directory = Path(config["record_dir"]) / (
        entry + "-" + boundary + "-" + phase + "-" + uuid4().hex
    )
    directory.mkdir(mode=0o700, parents=True)
    reserve(config, directory.name, configured(config).reservation_micro_usd)
    config_path = os.environ["DROV_E2B_TEST_CONFIG"]
    process = await launch(directory, config_path, entry=entry, boundary=boundary, phase=phase)
    try:
        message = await read_signal(process)
        assert message["barrier"] == boundary
    finally:
        await kill(process)
    before = ledger(directory)
    recovery = await launch(directory, config_path, mode="recover")
    try:
        result = await read_signal(recovery)
        async with asyncio.timeout(55):
            _, errors = await recovery.communicate()
        assert recovery.returncode == 0, errors.decode()
    finally:
        await kill(recovery)
    assert result["status"] == "completed" and result["destroyed"] is True
    expected = (
        "succeeded"
        if boundary in ("output_staged", "kill_reply", "destroyed", "publish")
        else "failed_known"
    )
    assert result["operation_status"] == expected
    after = ledger(directory)
    for kind in ("create_attempt", "code_start_attempt"):
        assert [x for x in before if x["kind"] == kind] == [x for x in after if x["kind"] == kind]
    assert sum(x["kind"] == "create_attempt" for x in after) == 1
    assert sum(x["kind"] == "code_start_attempt" for x in after) <= 1
