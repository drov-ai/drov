"""Isolated real-cloud SDK worker, killed only by its acceptance-test parent."""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drov import ResourceBinding  # noqa: E402
from drov.artifacts import LocalArtifacts  # noqa: E402
from drov.models import ScriptedModel  # noqa: E402
from drov.models.protocol import ModelResponse  # noqa: E402
from drov.sandbox._e2b_runner import RUN  # noqa: E402
from drov.sandbox._leases import LeaseTransaction  # noqa: E402
from drov.storage import SQLiteStorage  # noqa: E402
from tests.e2b_support import configured  # noqa: E402
from tests.test_e2b import runtime_for  # noqa: E402
from tests.test_e2b_live_faults import CODE, OWNER  # noqa: E402
from tests.test_sandbox_runtime import CONTEXT, definition  # noqa: E402


def signal(value: dict[str, Any]) -> None:
    print(json.dumps(value), flush=True)


async def halt(boundary: str) -> None:
    signal({"barrier": boundary})
    await asyncio.Event().wait()


class BarrierStorage(SQLiteStorage):
    def __init__(self, path: Path, boundary: str, phase: str) -> None:
        super().__init__(path)
        self.boundary, self.phase, self.target, self.fired = boundary, phase, False, False

    async def _raw(self, sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
        if sql == "BEGIN IMMEDIATE":
            self.target = False
        if sql.startswith("UPDATE sandbox_leases"):
            value = json.loads(parameters[2])
            match = {
                "remote_ack": value["remote_id"] is not None and not value["remote_start_intent"],
                "start_intent": value["remote_start_intent"] and value["observation"] is None,
                "observation": value["observation"] is not None and value["stage_id"] is None,
                "destroyed": value["destroyed"] and value["active"],
                "publish": value["destroyed"] and not value["active"],
            }
            self.target = match.get(self.boundary, False)
        if sql == "COMMIT" and self.target and not self.fired:
            self.fired = True
            if self.phase == "after":
                result = await super()._raw(sql, parameters)
                await halt(self.boundary)
                return result
            await halt(self.boundary)
        return await super()._raw(sql, parameters)


async def main(args: argparse.Namespace) -> None:
    import e2b
    from e2b.sandbox_async.commands.command import Commands

    config = json.loads(args.config.read_text())
    backend = configured(config)
    code = CODE + (";import time;time.sleep(40)" if args.boundary == "code_running" else "")
    agent, model = definition({"code": code, "outputs": ["result.csv"]})
    if args.mode == "recover":
        agent.model = ScriptedModel(
            [ModelResponse(message="original cloud facts", finish_reason="completed")]
        )
    runtime = runtime_for(args.directory, agent, backend, allow=True)
    if args.mode == "halt":
        runtime._storage_binding = ResourceBinding.owned(
            BarrierStorage(args.directory / "state.db", args.boundary, args.phase)
        )
    ledger = args.directory / "control.jsonl"

    def note(kind: str, **data: Any) -> None:
        with ledger.open("a") as stream:
            stream.write(json.dumps({"kind": kind, **data}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    create, program, kill, stage, start = (
        e2b.AsyncSandbox.create,
        backend._program,
        e2b.AsyncSandbox.kill,
        LocalArtifacts._stage,
        Commands._start,
    )

    async def creating(*values: Any, **opts: Any) -> Any:
        note("create_attempt")
        result = await create(*values, **opts)
        note("create_reply", resource_id=result.sandbox_id)
        if args.mode == "halt" and args.boundary == "create_reply":
            await halt(args.boundary)
        return result

    async def running(sandbox: Any, source: str, *, argument: str | None = None) -> str:
        if source == RUN:
            note("code_start_attempt")
        result = await program(sandbox, source, argument=argument)
        if args.mode == "halt" and args.boundary == "code_reply" and source == RUN:
            await halt(args.boundary)
        return result

    async def starting(self: Any, command: str, *values: Any, **opts: Any) -> Any:
        result = await start(self, command, *values, **opts)
        if args.mode == "halt" and args.boundary == "code_running" and "spec=json.loads" in command:
            note("root_process", pid=result.pid)
            await halt(args.boundary)
        return result

    async def killing(*values: Any, **opts: Any) -> Any:
        result = await kill(*values, **opts)
        note("kill_reply")
        if args.mode == "halt" and args.boundary == "kill_reply":
            await halt(args.boundary)
        return result

    async def staging(self: LocalArtifacts, stage_id: str, inputs: Any) -> Any:
        result = await stage(self, stage_id, inputs)
        if args.mode == "halt" and args.boundary == "output_staged" and result.purpose == "output":
            await halt(args.boundary)
        return result

    with (
        patch.object(e2b.AsyncSandbox, "create", creating),
        patch.object(backend, "_program", running),
        patch.object(e2b.AsyncSandbox, "kill", killing),
        patch.object(LocalArtifacts, "_stage", staging),
        patch.object(Commands, "_start", starting),
    ):
        async with runtime:
            if args.mode == "halt":
                if args.entry == "stream":
                    async with runtime.stream(
                        agent, "cloud crash fixture", session_id="s", context=CONTEXT
                    ) as stream:
                        async for _ in stream:
                            pass
                else:
                    await runtime.run(agent, "cloud crash fixture", session_id="s", context=CONTEXT)
                raise AssertionError("Cloud crash barrier did not fire")
            async with runtime.storage.transaction() as tx:
                runs = await tx._unfinished_runs()
            assert len(runs) == 1
            outcome = await runtime.recover(runs[0].state.run_id, context=CONTEXT)
            run = await runtime.storage.get_run(CONTEXT, runs[0].state.run_id)
            op = run.state.operations[0]
            async with runtime.storage.transaction() as tx:
                lease = await LeaseTransaction(tx).get(OWNER, run.state.run_id, op.operation_id)
            assert lease is not None and lease.destroyed and not lease.active
            assert await backend.resource(lease) is None
            signal(
                {
                    "status": outcome.status,
                    "operation_status": op.status,
                    "destroyed": lease.destroyed,
                }
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--boundary", default="")
    parser.add_argument("--phase", default="after")
    parser.add_argument("--entry", choices=["sdk", "stream"], default="sdk")
    parser.add_argument("--mode", choices=["halt", "recover"], default="halt")
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        raise SystemExit("Cloud worker failed: " + type(exc).__name__) from None
