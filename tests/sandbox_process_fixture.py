"""T-10B SDK subprocess: real Docker, original records, explicit parent-owned kill barriers."""

import argparse
import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drov.artifacts import ArtifactInput, LocalArtifacts  # noqa: E402
from drov.artifacts.local import _Stage  # noqa: E402
from drov.errors import ShutdownIncomplete, StorageInUse  # noqa: E402
from drov.models import ScriptedModel  # noqa: E402
from drov.models.protocol import ModelResponse  # noqa: E402
from drov.sandbox._docker import DockerControl  # noqa: E402
from drov.sandbox._engine import ExecOutput, UnixEngine  # noqa: E402
from drov.sandbox._leases import LeaseTransaction  # noqa: E402
from drov.sandbox._runner import INSTALL, WORKLOAD  # noqa: E402
from drov.storage import SQLiteStorage  # noqa: E402
from drov.types import WireModel  # noqa: E402
from tests.test_sandbox_docker import CONTEXT, create_runtime, observed  # noqa: E402
from tests.test_sandbox_faults import CODE  # noqa: E402
from tests.test_sandbox_isolation import OWNER, check_clean, granted_model  # noqa: E402


def signal(value: dict[str, Any]) -> None:
    print(json.dumps(value), flush=True)


async def halt(boundary: str) -> None:
    signal({"barrier": boundary})
    await asyncio.Event().wait()


class BarrierStorage(SQLiteStorage):
    def __init__(self, path: Path, boundary: str, phase: str) -> None:
        super().__init__(path)
        self.boundary, self.phase = boundary, phase
        self.target = False
        self.fired = False

    async def _raw(self, sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
        if sql == "BEGIN IMMEDIATE":
            self.target = False
        if sql.startswith("UPDATE sandbox_leases"):
            value = json.loads(parameters[2])
            match = {
                "container_ack": value["container_id"] is not None
                and not value["exec_create_intent"],
                "exec_create_intent": value["exec_create_intent"] and value["exec_id"] is None,
                "exec_ack": value["exec_id"] is not None and not value["exec_start_intent"],
                "exec_start_intent": value["exec_start_intent"] and value["observation"] is None,
                "observation": value["observation"] is not None and value["stage_id"] is None,
                "output_reserved": value["stage_id"] is not None and not value["destroyed"],
                "destroyed": value["destroyed"] and value["active"],
                "publish": value["destroyed"] and not value["active"],
            }
            self.target = match.get(self.boundary, False)
        if sql.startswith("INSERT INTO records") and parameters[0] == "event":
            event = json.loads(parameters[5])
            if self.boundary == "input_admit" and event["type"] == "run.accepted":
                self.target = True
        if sql == "COMMIT" and self.target and not self.fired:
            self.fired = True
            if self.phase == "after":
                result = await super()._raw(sql, parameters)
                await halt(self.boundary)
                return result
            await halt(self.boundary)
        return await super()._raw(sql, parameters)


async def main(args: argparse.Namespace) -> None:
    config = json.loads(args.config.read_text())
    code = CODE
    if args.boundary == "code_running":
        code += (
            ";import subprocess,sys,time;"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
            "start_new_session=True);time.sleep(30)"
        )
    model = granted_model(code, ["result.csv"])
    if args.mode == "recover":
        # Saved tool plans are authoritative. Recovery needs only the final model step.
        model = ScriptedModel([ModelResponse(message="original facts", finish_reason="completed")])
    runtime, agent, backend = create_runtime(args.directory, model, config)
    if args.mode == "retained":
        original = DockerControl.command

        async def missing(
            self: DockerControl, arguments: list[str], *, data: bytes | None = None
        ) -> bytes:
            from drov.sandbox._docker import ControlError

            if arguments[:2] == ["container", "create"]:
                raise ControlError("Synthetic unacknowledged create request")
            return await original(self, arguments, data=data)

        with patch.object(DockerControl, "command", missing):
            await runtime.open()
            if args.entry == "stream":
                async with runtime.stream(
                    agent, "unknown creation", session_id="s", context=CONTEXT
                ) as sub:
                    async for _ in sub:
                        pass
                async with runtime.storage.transaction() as tx:
                    record = (await tx._unfinished_runs())[0]
                run_id = record.state.run_id
            else:
                pending = await runtime.run(
                    agent, "unknown creation", session_id="s", context=CONTEXT
                )
                run_id = pending.run_id
            record = await runtime.storage.get_run(CONTEXT, run_id)
            assert record.state.status == "suspended" and record.state.reason == "reconciliation"
            async with runtime.storage.transaction() as tx:
                lease = await LeaseTransaction(tx).get(
                    OWNER, run_id, record.state.operations[0].operation_id
                )
                assert lease is not None and lease.active and not lease.destroyed
                assert lease.container_id is None and await LeaseTransaction(tx).active_count() == 1
            assert await backend.resource(lease) is None
            try:
                await runtime.aclose()
            except ShutdownIncomplete:
                pass
            else:
                raise AssertionError("Unknown creation released resource ownership")
            try:
                async with SQLiteStorage(args.directory / "state.db"):
                    pass
            except StorageInUse:
                pass
            else:
                raise AssertionError("Unknown creation released SQLite lock")
            await halt("retained")
        return
    if args.mode == "halt":
        from drov import ResourceBinding

        runtime._storage_binding = ResourceBinding.owned(
            BarrierStorage(args.directory / "state.db", args.boundary, args.phase)
        )

    command, create_exec, start_exec = (
        DockerControl.command,
        UnixEngine.create_exec,
        UnixEngine.start_exec,
    )
    stage, json_write = LocalArtifacts._stage, LocalArtifacts._json
    connect = UnixEngine._connect
    workload_ids: set[str] = set()
    fired = False
    ledger = args.directory / "control-ledger.jsonl"

    def note(kind: str, **values: str) -> None:
        import os

        with ledger.open("a") as stream:
            stream.write(json.dumps({"kind": kind, **values}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    async def control(
        self: DockerControl, arguments: list[str], *, data: bytes | None = None
    ) -> bytes:
        nonlocal fired
        result = await command(self, arguments, data=data)
        boundary = ""
        if arguments[:2] == ["container", "create"]:
            note("create", container_id=result.decode().strip())
            boundary = "container_reply"
        elif arguments[:2] == ["container", "exec"] and INSTALL in arguments:
            boundary = "inputs_installed"
        elif arguments[:2] == ["container", "rm"]:
            note("destroy")
            boundary = "destroy_reply"
        if args.mode == "halt" and boundary == args.boundary and not fired:
            fired = True
            await halt(boundary)
        return result

    async def creating(
        self: UnixEngine,
        container_id: str,
        code: str,
        *,
        argument: str | None = None,
        control: bool = False,
    ) -> str:
        identifier = await create_exec(self, container_id, code, argument=argument, control=control)
        if code == WORKLOAD:
            workload_ids.add(identifier)
            note("exec_create", exec_id=identifier)
            if args.mode == "halt" and args.boundary == "exec_reply":
                await halt(args.boundary)
        return identifier

    async def starting(
        self: UnixEngine, execution_id: str, *, seconds: float, max_bytes: int
    ) -> ExecOutput:
        if execution_id in workload_ids:
            # This is a durable request-attempt upper bound, not a claim of code completion.
            note("start_attempt", exec_id=execution_id)
        result = await start_exec(self, execution_id, seconds=seconds, max_bytes=max_bytes)
        if execution_id in workload_ids:
            note("code_reply", exec_id=execution_id)
            if args.mode == "halt" and args.boundary == "code_reply":
                await halt(args.boundary)
        return result

    async def connecting(
        self: UnixEngine, method: str, path: str, payload: Any, *, attach: bool = False
    ) -> Any:
        result = await connect(self, method, path, payload, attach=attach)
        identifier = path.split("/")[-2]
        if (
            args.mode == "halt"
            and args.boundary == "code_running"
            and attach
            and identifier in workload_ids
        ):
            signal({"barrier": args.boundary, "exec_id": identifier})
            await asyncio.Event().wait()
        return result

    async def staging(self: LocalArtifacts, stage_id: str, inputs: Any) -> Any:
        result = await stage(self, stage_id, inputs)
        boundary = "input_staged" if result.purpose == "input" else "output_staged"
        if args.mode == "halt" and args.boundary == boundary:
            await halt(boundary)
        return result

    def writing(self: LocalArtifacts, fd: int, name: str, value: WireModel) -> None:
        if (
            args.mode == "halt"
            and args.boundary == "output_byte_fsynced"
            and isinstance(value, _Stage)
            and value.purpose == "output"
            and value.state == "staged"
        ):
            signal({"barrier": args.boundary})
            threading.Event().wait()
        json_write(self, fd, name, value)
        if (
            args.mode == "halt"
            and args.boundary == "output_planned"
            and isinstance(value, _Stage)
            and value.purpose == "output"
            and value.files
            and value.state == "reserved"
        ):
            signal({"barrier": args.boundary})
            threading.Event().wait()  # Only this isolated fixture's parent will kill us.

    with (
        patch.object(DockerControl, "command", control),
        patch.object(UnixEngine, "create_exec", creating),
        patch.object(UnixEngine, "start_exec", starting),
        patch.object(UnixEngine, "_connect", connecting),
        patch.object(LocalArtifacts, "_stage", staging),
        patch.object(LocalArtifacts, "_json", writing),
    ):
        async with runtime:
            if args.mode == "halt":
                files = [ArtifactInput(name="sentinel.csv", data=b"synthetic-only")]
                if args.entry == "stream":
                    async with runtime.stream(
                        agent, "original code", session_id="s", context=CONTEXT, artifacts=files
                    ) as subscription:
                        async for _ in subscription:
                            pass
                else:
                    await runtime.run(
                        agent, "original code", session_id="s", context=CONTEXT, artifacts=files
                    )
                raise AssertionError("Requested barrier did not fire")
            async with runtime.storage.transaction() as tx:
                unfinished = await tx._unfinished_runs()
            if not unfinished:
                signal({"status": "no_unfinished", "model_calls": len(model.calls)})
                return
            run_id = unfinished[0].state.run_id
            done = await runtime.recover(run_id, context=CONTEXT)
            record = await runtime.storage.get_run(CONTEXT, run_id)
            if record.state.operations:
                await check_clean(runtime, backend, run_id)
            value: dict[str, Any] = {
                "status": done.status,
                "operation_status": [op.status for op in record.state.operations],
                "model_calls": len(model.calls),
            }
            if record.state.operations and record.state.operations[0].status == "succeeded":
                result = await observed(runtime, run_id)
                value["bytes"] = (
                    await runtime.read_artifact(result.artifacts[0], context=CONTEXT)
                ).decode()
            signal(value)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--boundary", default="")
    parser.add_argument("--phase", default="after")
    parser.add_argument("--entry", choices=["sdk", "stream"], default="sdk")
    parser.add_argument("--mode", choices=["halt", "recover", "retained"], default="halt")
    asyncio.run(main(parser.parse_args()))
