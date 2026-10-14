"""Real Docker work with bounded client/storage faults; no shared daemon outage."""

import errno
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from drov.artifacts import LocalArtifacts
from drov.artifacts.local import _Stage
from drov.errors import PersistenceUnavailable, RecordNotFound, StorageInUse
from drov.models import ScriptedModel
from drov.sandbox import SandboxLimits
from drov.sandbox._docker import ControlError, DockerControl
from drov.sandbox._engine import EngineError, ExecOutput, UnixEngine
from drov.sandbox._leases import LeaseTransaction
from drov.sandbox._runner import WORKLOAD
from drov.storage import SQLiteStorage
from drov.storage.sqlite import SQLiteDriver
from drov.types import WireModel
from tests.test_sandbox_docker import CONTEXT, create_runtime, model_for, observed
from tests.test_sandbox_docker import config as config
from tests.test_sandbox_isolation import OWNER, check_clean

pytestmark = pytest.mark.docker
CODE = (
    "from pathlib import Path;"
    "Path('/work/outputs/result.csv').write_text('value\\n60\\n');print(60)"
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary", ["create_reply", "code_reply", "export_reply", "destroy_before", "destroy_reply"]
)
async def test_lost_control_replies_never_create_or_execute_replacement_code(
    tmp_path: Path, config: dict[str, str], monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    creates = 0
    executions: set[str] = set()
    starts = 0
    fired = False
    command, create_exec, start_exec = (
        DockerControl.command,
        UnixEngine.create_exec,
        UnixEngine.start_exec,
    )

    async def faulty_command(
        self: DockerControl, arguments: list[str], *, data: bytes | None = None
    ) -> bytes:
        nonlocal creates, fired
        if arguments[:2] == ["container", "create"]:
            creates += 1
        match = (boundary == "create_reply" and arguments[:2] == ["container", "create"]) or (
            boundary.startswith("destroy_") and arguments[:2] == ["container", "rm"]
        )
        if match and not fired and boundary == "destroy_before":
            fired = True
            raise ControlError("Synthetic transport failure before removal")
        result = await command(self, arguments, data=data)
        if match and not fired:
            fired = True
            raise ControlError("Synthetic lost control acknowledgement")
        return result

    async def record_exec(
        self: UnixEngine,
        container_id: str,
        code: str,
        *,
        argument: str | None = None,
        control: bool = False,
    ) -> str:
        identifier = await create_exec(self, container_id, code, argument=argument, control=control)
        if code == WORKLOAD:
            executions.add(identifier)
        return identifier

    async def faulty_start(
        self: UnixEngine, execution_id: str, *, seconds: float, max_bytes: int
    ) -> ExecOutput:
        nonlocal starts, fired
        if execution_id in executions:
            starts += 1
        result = await start_exec(self, execution_id, seconds=seconds, max_bytes=max_bytes)
        match = (boundary == "code_reply" and execution_id in executions) or (
            boundary == "export_reply"
            and execution_id not in executions
            and result.stdout.startswith(b'{"files":')
        )
        if match and not fired:
            fired = True
            raise EngineError("Synthetic lost stream observation")
        return result

    monkeypatch.setattr(DockerControl, "command", faulty_command)
    monkeypatch.setattr(UnixEngine, "create_exec", record_exec)
    monkeypatch.setattr(UnixEngine, "start_exec", faulty_start)
    model = model_for(CODE, ["result.csv"])
    if boundary.startswith("destroy_"):
        model = ScriptedModel([model._steps[0], model._steps[0], model._steps[1], model._steps[1]])
    runtime, agent, backend = create_runtime(
        tmp_path, model, config, limits=SandboxLimits(concurrency=1)
    )
    async with runtime:
        done = await runtime.run(agent, "one attempt", session_id="s", context=CONTEXT)
        assert fired and creates == 1 and starts == (0 if boundary == "create_reply" else 1)
        if boundary.startswith("destroy_"):
            assert done.status == "suspended" and done.pending is not None
            async with runtime.storage.transaction() as tx:
                lease = await LeaseTransaction(tx).get(
                    OWNER,
                    done.run_id,
                    (await tx.get_run(CONTEXT, done.run_id)).state.operations[0].operation_id,
                )
                assert lease is not None and lease.active and not lease.destroyed and lease.stage_id
                assert await LeaseTransaction(tx).active_count() == 1
            stage = await runtime._files.store()._snapshot(lease.stage_id)
            with pytest.raises(RecordNotFound):
                await runtime.read_artifact(stage.files[0].ref, context=CONTEXT)
            with pytest.raises(StorageInUse):
                async with SQLiteStorage(tmp_path / "state.db"):
                    pass
            verified = await runtime.verify(
                done.run_id, request_id=done.pending.request_id, context=CONTEXT
            )
            assert verified.resolution == "unresolved" and starts == 1 and creates == 1
            if boundary == "destroy_before":
                assert await backend.resource(lease) is not None
            else:
                assert await backend.resource(lease) is None
            blocked = await runtime.run(
                agent, "capacity stays reserved", session_id="other", context=CONTEXT
            )
            blocked_record = await runtime.storage.get_run(CONTEXT, blocked.run_id)
            assert blocked_record.state.operations[0].status == "not_executed"
            async with runtime.storage.transaction() as tx:
                assert await LeaseTransaction(tx).active_count() == 1
            assert creates == starts == 1
            recovered = await runtime.recover(done.run_id, context=CONTEXT)
            assert recovered.status == "completed" and starts == creates == 1
            result = await observed(runtime, done.run_id)
            assert result.exit_code == 0 and result.artifact_status == "complete"
            assert (
                await runtime.read_artifact(result.artifacts[0], context=CONTEXT) == b"value\n60\n"
            )
        else:
            assert (
                done.status == "completed"
                and (await runtime.storage.get_run(CONTEXT, done.run_id)).state.operations[0].status
                == "failed_known"
            )
            async with runtime.storage.transaction() as tx:
                assert isinstance(tx._driver, SQLiteDriver)
                rows, _ = await tx._driver.execute("SELECT count(*) FROM artifact_records")
                assert rows[0][0] == 0
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
async def test_real_sqlite_ready_and_result_rollback_then_original_docker_stage_recovery(
    tmp_path: Path, config: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    starts = 0
    original = UnixEngine.create_exec

    async def count(
        self: UnixEngine,
        container_id: str,
        code: str,
        *,
        argument: str | None = None,
        control: bool = False,
    ) -> str:
        nonlocal starts
        if code == WORKLOAD:
            starts += 1
        return await original(self, container_id, code, argument=argument, control=control)

    monkeypatch.setattr(UnixEngine, "create_exec", count)
    runtime, agent, backend = create_runtime(tmp_path, model_for(CODE, ["result.csv"]), config)
    async with runtime:
        async with runtime.storage.transaction() as tx:
            assert isinstance(tx._driver, SQLiteDriver)
            await tx._driver.execute("""CREATE TRIGGER reject_docker_ready
                BEFORE INSERT ON artifact_records
                WHEN json_extract(NEW.payload,'$.operation_id') IS NOT NULL
                BEGIN SELECT RAISE(ABORT,'fixture ready failure'); END""")
        with pytest.raises(PersistenceUnavailable):
            await runtime.run(agent, "publication failure", session_id="s", context=CONTEXT)
        async with runtime.storage.transaction() as tx:
            assert isinstance(tx._driver, SQLiteDriver)
            run = (await tx._unfinished_runs())[0]
            rows, _ = await tx._driver.execute("SELECT count(*) FROM artifact_records")
            assert rows[0][0] == 0
            lease = await LeaseTransaction(tx).get(
                OWNER, run.state.run_id, run.state.operations[0].operation_id
            )
            assert lease is not None and lease.active and lease.destroyed and lease.stage_id
            await tx._driver.execute("DROP TRIGGER reject_docker_ready")
        stage = await runtime._files.store()._snapshot(lease.stage_id)
        with pytest.raises(RecordNotFound):
            await runtime.read_artifact(stage.files[0].ref, context=CONTEXT)
        done = await runtime.recover(run.state.run_id, context=CONTEXT)
        assert done.status == "completed" and starts == 1
        assert await runtime.read_artifact(stage.files[0].ref, context=CONTEXT) == b"value\n60\n"
        await check_clean(runtime, backend, done.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["byte_fsync", "stage_manifest"])
async def test_actual_export_disk_failure_retains_unpublished_stage_and_never_recalculates(
    tmp_path: Path, config: dict[str, str], monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    runtime, agent, backend = create_runtime(tmp_path, model_for(CODE, ["result.csv"]), config)
    original_json, original_fsync = LocalArtifacts._json, os.fsync
    fired = False
    output_stage = False

    def faulty_json(self: LocalArtifacts, fd: int, name: str, value: WireModel) -> None:
        nonlocal fired, output_stage
        if isinstance(value, _Stage) and value.purpose == "output" and value.files:
            output_stage = True
            if phase == "stage_manifest" and value.state == "staged" and not fired:
                fired = True
                raise OSError(errno.ENOSPC, "Synthetic output manifest disk full")
        original_json(self, fd, name, value)

    def faulty_fsync(fd: int) -> None:
        nonlocal fired
        # The stage's planned manifest was saved first. Fail the actual byte fsync,
        # not SQLite or the input/admission stage.
        if phase == "byte_fsync" and output_stage and not fired:
            import stat

            if stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_size == 9:
                fired = True
                raise OSError(errno.ENOSPC, "Synthetic export byte disk full")
        original_fsync(fd)

    async with runtime:
        monkeypatch.setattr(LocalArtifacts, "_json", faulty_json)
        monkeypatch.setattr(os, "fsync", faulty_fsync)
        done = await runtime.run(agent, "disk failure", session_id="s", context=CONTEXT)
        assert (
            fired
            and done.status == "completed"
            and (await runtime.storage.get_run(CONTEXT, done.run_id)).state.operations[0].status
            == "failed_known"
        )
        monkeypatch.setattr(LocalArtifacts, "_json", original_json)
        monkeypatch.setattr(os, "fsync", original_fsync)
        lease = await check_clean(runtime, backend, done.run_id)
        assert lease.stage_id and lease.observation
        stage = await runtime._files.store()._snapshot(lease.stage_id)
        assert stage.state == "reserved" and stage.files
        with pytest.raises(RecordNotFound):
            await runtime.read_artifact(stage.files[0].ref, context=CONTEXT)
        # This completed tool error is final; missing durable bytes cannot be recomputed.
        pending: Sequence[Any] = await runtime._files.store()._pending()
        assert any(item.stage_id == stage.stage_id for item in pending)


@pytest.mark.asyncio
async def test_recovery_cannot_bypass_original_result_byte_limit(
    tmp_path: Path, config: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from drov import Limits

    original = DockerControl.command
    blocked = False
    creates = 0

    async def command(
        self: DockerControl, arguments: list[str], *, data: bytes | None = None
    ) -> bytes:
        nonlocal blocked, creates
        if arguments[:2] == ["container", "create"]:
            creates += 1
        if arguments[:2] == ["container", "rm"] and not blocked:
            blocked = True
            raise ControlError("Synthetic cleanup interruption")
        return await original(self, arguments, data=data)

    monkeypatch.setattr(DockerControl, "command", command)
    runtime, agent, backend = create_runtime(
        tmp_path,
        model_for(CODE + ";print('x'*2048)", ["result.csv"]),
        config,
        run_limits=Limits(max_tool_result_bytes=512),
    )
    async with runtime:
        pending = await runtime.run(
            agent, "bounded recovery result", session_id="s", context=CONTEXT
        )
        assert pending.status == "suspended" and blocked
        done = await runtime.recover(pending.run_id, context=CONTEXT)
        record = await runtime.storage.get_run(CONTEXT, done.run_id)
        assert record.state.operations[0].status == "failed_known"
        assert creates == 1
        async with runtime.storage.transaction() as tx:
            assert isinstance(tx._driver, SQLiteDriver)
            rows, _ = await tx._driver.execute("SELECT count(*) FROM artifact_records")
            assert rows[0][0] == 0
        await check_clean(runtime, backend, done.run_id)
