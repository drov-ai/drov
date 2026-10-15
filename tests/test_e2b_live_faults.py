"""Actual remote resources with controlled SDK reply loss and real Runtime recovery."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from drov.errors import ConfigurationError, ShutdownIncomplete
from drov.sandbox._e2b_runner import RUN
from drov.sandbox._leases import LeaseTransaction
from drov.sandbox._runner import EXPORT
from drov.storage.records import MemoryScope
from tests.e2b_support import configuration, configured, reserve
from tests.test_e2b import runtime_for
from tests.test_sandbox_runtime import CONTEXT, definition

pytestmark = pytest.mark.live
CODE = (
    "from pathlib import Path;"
    "Path('/work/outputs/result.csv').write_text('value\\n60\\n');print(60)"
)
OWNER = MemoryScope(tenant_id="fixture", user_id="alice", agent_name="sandbox-fixture")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    [
        "create_after",
        "upload_before",
        "upload_after",
        "run_after",
        "export_before",
        "export_after",
        "query_before",
        "kill_before",
        "kill_after",
    ],
)
async def test_actual_remote_faults_never_recreate_or_reexecute(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    config = configuration()
    import e2b
    from e2b.sandbox_async.filesystem.filesystem import Filesystem

    directory = Path(config["record_dir"]) / (boundary + "-" + uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    backend = configured(config)
    reserve(config, directory.name, backend.reservation_micro_usd)
    agent, _ = definition({"code": CODE, "outputs": ["result.csv"]})
    calls: list[dict[str, Any]] = []

    def record(kind: str, **data: Any) -> None:
        calls.append({"kind": kind, **data})
        with (directory / "control.jsonl").open("a") as stream:
            stream.write(json.dumps(calls[-1]) + "\n")

    active = True
    create, write, program, query, kill = (
        e2b.AsyncSandbox.create,
        Filesystem.write,
        backend._program,
        backend.resource,
        e2b.AsyncSandbox.kill,
    )

    async def create_call(*args: Any, **opts: Any) -> Any:
        record("create_attempt")
        result = await create(*args, **opts)
        record("create_reply", resource_id=result.sandbox_id)
        if active and boundary == "create_after":
            raise RuntimeError("Synthetic lost actual create reply")
        return result

    async def write_call(self: Any, *args: Any, **opts: Any) -> Any:
        if active and boundary == "upload_before":
            raise RuntimeError("Synthetic lost upload request")
        result = await write(self, *args, **opts)
        if active and boundary == "upload_after":
            raise RuntimeError("Synthetic lost actual upload reply")
        return result

    async def program_call(sandbox: Any, source: str, *, argument: str | None = None) -> str:
        if source == RUN:
            record("code_start_attempt")
        if active and source == EXPORT and boundary == "export_before":
            raise ConfigurationError("Synthetic lost export request")
        result = await program(sandbox, source, argument=argument)
        if active and (
            (source == RUN and boundary == "run_after")
            or (source == EXPORT and boundary == "export_after")
        ):
            raise ConfigurationError("Synthetic lost actual program reply")
        return result

    async def query_call(lease: Any) -> Any:
        if active and boundary == "query_before":
            raise ConfigurationError("Synthetic provider query unavailable")
        return await query(lease)

    async def kill_call(*args: Any, **opts: Any) -> Any:
        if active and boundary == "kill_before":
            raise ConfigurationError("Synthetic kill request lost")
        result = await kill(*args, **opts)
        if active and boundary == "kill_after":
            raise ConfigurationError("Synthetic actual kill reply lost")
        return result

    monkeypatch.setattr(e2b.AsyncSandbox, "create", create_call)
    monkeypatch.setattr(Filesystem, "write", write_call)
    monkeypatch.setattr(backend, "_program", program_call)
    monkeypatch.setattr(backend, "resource", query_call)
    monkeypatch.setattr(e2b.AsyncSandbox, "kill", kill_call)
    runtime = runtime_for(directory, agent, backend, allow=True)
    await runtime.open()
    try:
        outcome: Any = await runtime.run(
            agent, "synthetic remote fault", session_id="s", context=CONTEXT
        )
        count = len([i for i in calls if i["kind"] == "code_start_attempt"])
        assert len([i for i in calls if i["kind"] == "create_attempt"]) == 1
        assert count == (0 if boundary in ("create_after", "upload_before", "upload_after") else 1)
        if boundary in ("query_before", "kill_before", "kill_after"):
            assert outcome.status == "suspended" and outcome.reason == "reconciliation"
            assert runtime._sandboxes is not None
            with pytest.raises(ShutdownIncomplete):
                await runtime._sandboxes.aclose()
            active = False
            outcome = await runtime.recover(outcome.run_id, context=CONTEXT)
        assert outcome.status == "completed"
        run = await runtime.storage.get_run(CONTEXT, outcome.run_id)
        operation = run.state.operations[0]
        assert operation.status == (
            "succeeded"
            if boundary in ("query_before", "kill_before", "kill_after")
            else "failed_known"
        )
        async with runtime.storage.transaction() as tx:
            lease = await LeaseTransaction(tx).get(OWNER, outcome.run_id, operation.operation_id)
        assert lease is not None and lease.destroyed and not lease.active
        active = False
        assert await backend.resource(lease) is None
        assert count == len([i for i in calls if i["kind"] == "code_start_attempt"])
        assert len([i for i in calls if i["kind"] == "create_attempt"]) == 1
    finally:
        active = False
        await runtime.aclose()
