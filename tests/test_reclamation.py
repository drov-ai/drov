"""Real byte removal, durable intent, retained references and failure boundaries."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

from drov import Agent
from drov.artifacts import ArtifactInput, ArtifactLimits, LocalArtifacts
from drov.artifacts.local import LEDGER_BYTES
from drov.artifacts.uploads import Uploads, UploadTarget
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    ContentGone,
    IncompatibleStorage,
    PersistenceUnavailable,
    RecordNotFound,
    StorageInUse,
)
from drov.models import ScriptedModel
from drov.storage import SQLiteStorage
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import MemoryScope
from tests.test_agent_runs import final
from tests.test_maintenance import ALICE, SCOPE, file_runtime
from tests.test_storage import start_request, transition

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private byte reclamation")
OWNER = MemoryScope(**ALICE.model_dump(), agent_name="assistant")


def admin(path: Path, *, limits: ArtifactLimits | None = None) -> SQLiteMaintenance:
    return SQLiteMaintenance(
        path / "state.db", artifacts=LocalArtifacts(directory=path / "files", limits=limits)
    )


async def orphan(
    path: Path,
    *,
    owner: MemoryScope = OWNER,
    state: str = "staged",
    run_id: str = "unaccepted",
    purpose: Literal["input", "output"] = "input",
) -> str:
    async with SQLiteStorage(path / "state.db") as storage:
        files = LocalArtifacts(directory=path / "files")
        await files.open(storage=storage)
        try:
            stage = await files._reserve(
                owner=owner,
                run_id=run_id,
                operation_id=None,
                purpose=purpose,
                size_bytes=40,
            )
            if state != "reserved":
                stage = await files._stage(
                    stage.stage_id, [ArtifactInput(name="secret.txt", data=b"private")]
                )
            if state == "partial":
                (path / "files" / stage.stage_id / stage.files[0].ref.artifact_id).unlink()
            return stage.stage_id
        finally:
            await files.aclose()


def byte_snapshot(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["reserved", "staged", "partial"])
async def test_preview_is_readonly_then_release_exact_capacity(tmp_path: Path, state: str) -> None:
    stage = await orphan(tmp_path, state=state)
    before = byte_snapshot(tmp_path)
    async with admin(tmp_path) as maintenance:
        plan = await maintenance.preview_reclamation(OWNER)
        assert plan.stages == 1 and not plan.protected
        assert plan.bytes_eligible == (7 if state == "staged" else 0)
        assert plan.capacity_eligible == LEDGER_BYTES + (40 if state == "reserved" else 7)
        assert not any(
            word in plan.model_dump_json() for word in ["secret", "private", stage, str(tmp_path)]
        )
    assert byte_snapshot(tmp_path) == before
    async with admin(tmp_path) as maintenance:
        result = await maintenance.reclaim(plan)
        assert result.completed_stages == 1
        assert result.bytes_reclaimed == plan.bytes_eligible
        assert result.capacity_released == plan.capacity_eligible
        assert (await maintenance.resume_reclamation()).completed_stages == 0
        assert (await maintenance.preview_reclamation(OWNER)).stages == 0
    assert not (tmp_path / "files" / stage).exists()
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        files = LocalArtifacts(directory=tmp_path / "files")
        await files.open(storage=storage)
        assert await files._pending() == []
        await files.aclose()
    with closing(sqlite3.connect(tmp_path / "state.db")) as connection:
        data = json.dumps(connection.execute("SELECT * FROM artifact_reclamation").fetchall())
    assert "secret" not in data and "private" not in data and "alice" not in data
    assert '"done"' in data


@pytest.mark.asyncio
async def test_reclamation_is_explicit_and_exact_owner_scope(tmp_path: Path) -> None:
    mine = await orphan(tmp_path)
    others = [
        await orphan(tmp_path, owner=OWNER.model_copy(update=change))
        for change in [
            {"user_id": "bob"},
            {"tenant_id": "another"},
            {"agent_name": "another"},
        ]
    ]
    async with admin(tmp_path) as maintenance:
        plan = await maintenance.preview_reclamation(OWNER)
        assert plan.stages == 1
        await maintenance.reclaim(plan)
    assert not (tmp_path / "files" / mine).exists()
    assert all((tmp_path / "files" / stage / "stage.json").is_file() for stage in others)


@pytest.mark.asyncio
async def test_shared_ready_file_protected_until_last_logical_reference(tmp_path: Path) -> None:
    a: Agent[str] = Agent(name="assistant", revision="1", model=ScriptedModel([final("done")] * 2))
    async with file_runtime(tmp_path, a) as runtime:
        first = await runtime.run(
            a,
            "source",
            session_id="session",
            context=ALICE,
            artifacts=[ArtifactInput(name="secret.csv", data=b"x\n1\n")],
        )
        ref = (await runtime.storage.get_run(ALICE, first.run_id)).artifact_inputs[0]
        await runtime.run(a, "share", session_id="share", context=ALICE, artifacts=[ref])
    async with admin(tmp_path) as maintenance:
        assert (await maintenance.preview_reclamation(OWNER)).stages == 0
        await maintenance.delete_session(await maintenance.preview_session(SCOPE))
        assert (await maintenance.preview_reclamation(OWNER)).stages == 0
    async with file_runtime(tmp_path, a) as runtime:
        assert await runtime.read_artifact(ref, context=ALICE) == b"x\n1\n"
    async with admin(tmp_path) as maintenance:
        await maintenance.delete_session(
            await maintenance.preview_session(SCOPE.model_copy(update={"session_id": "share"}))
        )
        plan = await maintenance.preview_reclamation(OWNER)
        assert plan.stages == 1
        assert (await maintenance.reclaim(plan)).bytes_reclaimed == 4
    async with file_runtime(tmp_path, a) as runtime:
        with pytest.raises(ContentGone):
            await runtime.read_artifact(ref, context=ALICE)
        with pytest.raises(ContentGone):
            await runtime.start(a, "source", session_id="session", context=ALICE)
    assert len(a.model.calls) == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["database", "stage", "bytes", "plan"])
async def test_stale_or_tampered_preview_cannot_delete(tmp_path: Path, change: str) -> None:
    stage = await orphan(tmp_path)
    async with admin(tmp_path) as maintenance:
        plan = await maintenance.preview_reclamation(OWNER)
    if change == "database":
        async with SQLiteStorage(tmp_path / "state.db") as storage:
            await storage.start(start_request())
    elif change == "stage":
        await orphan(tmp_path)
    elif change == "bytes":
        file = next((tmp_path / "files" / stage).glob("art_*"))
        file.write_bytes(b"changed")
    else:
        plan = plan.model_copy(update={"bytes_eligible": plan.bytes_eligible + 1})
    before = byte_snapshot(tmp_path)
    async with admin(tmp_path) as maintenance:
        with pytest.raises(Conflict):
            await maintenance.reclaim(plan)
    assert byte_snapshot(tmp_path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "cancelled_unknown", "lease", "output_no_facts"])
async def test_unfinished_unknown_and_lease_stages_are_held(tmp_path: Path, status: str) -> None:
    from drov.sandbox._leases import LeaseTransaction, SandboxLease
    from drov.storage.records import EventDraft
    from drov.tools.operations import OperationJournal, ToolOperation

    run_id = "no-facts"
    if status != "output_no_facts":
        async with SQLiteStorage(tmp_path / "state.db") as storage:
            ref = await storage.start(start_request())
            run_id = ref.run_id
            if status == "cancelled_unknown":
                await transition(storage, SCOPE, ref)
                await OperationJournal(storage, ALICE).commit(
                    ToolOperation(
                        run_id=run_id,
                        operation_id="op",
                        tool_name="write",
                        tool_revision="1",
                        effect="write",
                        request_hash="a" * 64,
                        arguments={},
                        attempts=1,
                        attempt_id="attempt",
                        status="unknown",
                    ),
                    EventDraft(type="tool.unknown"),
                )
                current = await storage.get_run(ALICE, run_id)
                await transition(storage, SCOPE, current.state, status="cancelled")
            if status == "lease":
                await transition(storage, SCOPE, ref, status="cancelled")
                await storage._bind_sandbox()
                async with storage.transaction() as tx:
                    await LeaseTransaction(tx).put(
                        SandboxLease(
                            execution_key="a" * 32,
                            owner=OWNER,
                            run_id=run_id,
                            operation_id="op",
                            backend_name="python",
                            backend_hash="b" * 64,
                            code_hash="c" * 64,
                            active=True,
                            create_intent=True,
                        )
                    )
    stage = await orphan(
        tmp_path, run_id=run_id, purpose="output" if status == "output_no_facts" else "input"
    )
    async with admin(tmp_path) as maintenance:
        plan = await maintenance.preview_reclamation(OWNER)
        assert plan.stages == 0 and sum(plan.protected.values()) == 1
        assert (await maintenance.reclaim(plan)).completed_stages == 0
    assert (tmp_path / "files" / stage).is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["receiving", "staged", "consumed", "fresh"])
async def test_only_explicit_expired_unconsumed_uploads_are_reclaimed(
    tmp_path: Path, state: str
) -> None:
    a: Agent[str] = Agent(name="assistant", revision="1", model=ScriptedModel([final("done")]))
    async with file_runtime(tmp_path, a) as runtime:
        assert isinstance(runtime.storage, SQLiteStorage)
        await runtime.storage._bind_uploads()
        uploads = Uploads(runtime)
        reserved = await uploads.reserve(
            ALICE,
            UploadTarget(
                agent_name="assistant", revision="1", session_id="session", name="secret.txt"
            ),
            7,
        )
        if state != "receiving":
            await uploads.stage(ALICE, reserved, b"private")
        if state == "consumed":
            started = await runtime._runs.start(
                a,
                "use",
                context=ALICE,
                session_id="session",
                request_key=None,
                _upload_ids=(reserved.upload_id,),
            )
            await runtime.wait(started.run_id, context=ALICE)
        if state != "fresh":
            async with runtime.storage.transaction() as tx:
                record = await uploads.get(tx, ALICE, reserved.upload_id)
                await uploads.replace(
                    tx,
                    record,
                    record.model_copy(
                        update={
                            "expires_at": tx._now() - timedelta(seconds=1),
                            "version": record.version + 1,
                        }
                    ),
                )
    async with admin(tmp_path) as maintenance:
        assert (await maintenance.preview_reclamation(OWNER)).stages == 0
        plan = await maintenance.preview_reclamation(OWNER, expired_uploads=True)
        eligible = state in ("receiving", "staged")
        assert plan.stages == plan.uploads == int(eligible)
        result = await maintenance.reclaim(plan)
        assert result.completed_stages == int(eligible)
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        async with storage.transaction() as tx:
            if eligible:
                with pytest.raises(RecordNotFound):
                    await Uploads(None).get(tx, ALICE, reserved.upload_id)  # type: ignore[arg-type]
            else:
                kept = await Uploads(None).get(tx, ALICE, reserved.upload_id)  # type: ignore[arg-type]
                assert kept.state == ("consumed" if state == "consumed" else "staged")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["intent", "intent_receipt", "unlink", "rmdir", "fsync", "completion", "completion_receipt"],
)
async def test_io_and_commit_failures_keep_resumable_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import drov.storage._reclamation as module

    stage = await orphan(tmp_path)
    original_unlink, original_rmdir, original_fsync = os.unlink, os.rmdir, os.fsync
    async with admin(tmp_path) as maintenance:
        plan = await maintenance.preview_reclamation(OWNER)
        original_sql = maintenance._storage._raw
        fired, commits = False, 0

        async def fail_sql(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            nonlocal fired, commits
            if sql == "COMMIT":
                commits += 1
            should_fail = (
                failure.startswith("intent") and sql.startswith("INSERT INTO artifact_reclamation")
            ) or (
                failure.startswith("completion") and sql.startswith("UPDATE artifact_reclamation")
            )
            if should_fail and not failure.endswith("receipt") and not fired:
                fired = True
                raise sqlite3.OperationalError("synthetic failed commit")
            result = await original_sql(sql, parameters)
            # reclaim transaction is commit 1; resume's read check is commit 2;
            # its actual completion update is commit 3.
            if (
                sql == "COMMIT"
                and not fired
                and (
                    (failure == "intent_receipt" and commits == 1)
                    or (failure == "completion_receipt" and commits == 3)
                )
            ):
                fired = True
                raise sqlite3.OperationalError("synthetic lost receipt")
            return result

        def fail_io(original: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal fired
            if not fired:
                fired = True
                raise OSError("synthetic removal failure")
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(maintenance._storage, "_raw", fail_sql)
            if failure in ("unlink", "rmdir", "fsync"):
                original = {
                    "unlink": original_unlink,
                    "rmdir": original_rmdir,
                    "fsync": original_fsync,
                }[failure]
                # Patch only inside erase: stage preparation/preview stays intact.
                real_erase = module.erase

                async def broken(*args: Any) -> None:
                    with monkeypatch.context() as io_patch:
                        io_patch.setattr(os, failure, lambda *a, **kw: fail_io(original, *a, **kw))
                        await real_erase(*args)

                patch.setattr("drov.storage.maintenance.erase", broken)
            with pytest.raises(PersistenceUnavailable):
                await maintenance.reclaim(plan)
            assert fired
    if failure == "intent":
        assert (tmp_path / "files" / stage).is_dir()
        async with admin(tmp_path) as maintenance:
            await maintenance.reclaim(await maintenance.preview_reclamation(OWNER))
    elif failure != "completion_receipt":
        with pytest.raises(PersistenceUnavailable):
            async with SQLiteStorage(tmp_path / "state.db"):
                pass
        async with admin(tmp_path) as maintenance:
            with pytest.raises(Conflict):
                await maintenance.preview_reclamation(OWNER)
            resumed = await maintenance.resume_reclamation()
            assert resumed.completed_stages == 1 and resumed.capacity_released == LEDGER_BYTES + 7
    assert not (tmp_path / "files" / stage).exists()
    async with SQLiteStorage(tmp_path / "state.db"):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    [
        "before_intent",
        "after_intent",
        "after_byte",
        "after_manifest",
        "after_rmdir",
        "before_complete",
        "after_complete",
    ],
)
async def test_actual_process_kill_recovers_each_removal_boundary(
    tmp_path: Path, boundary: str
) -> None:
    stage = await orphan(tmp_path)
    marker = tmp_path / "marker"
    process = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            str(Path(__file__).with_name("reclamation_process_fixture.py")),
            str(tmp_path),
            str(marker),
            boundary,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while not marker.exists():
            if process.poll() is not None:
                assert process.stderr is not None
                pytest.fail(process.stderr.read().decode())
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        with pytest.raises(StorageInUse):
            async with SQLiteStorage(tmp_path / "state.db"):
                pass
    finally:
        if process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.wait, timeout=5)
        assert process.stderr is not None
        process.stderr.close()
    if boundary == "before_intent":
        assert (tmp_path / "files" / stage / "stage.json").is_file()
        async with admin(tmp_path) as maintenance:
            await maintenance.reclaim(await maintenance.preview_reclamation(OWNER))
    else:
        if boundary != "after_complete":
            with pytest.raises(PersistenceUnavailable):
                async with SQLiteStorage(tmp_path / "state.db"):
                    pass
        async with admin(tmp_path) as maintenance:
            result = await maintenance.resume_reclamation()
            assert result.completed_stages == int(boundary != "after_complete")
    assert not (tmp_path / "files" / stage).exists()
    async with SQLiteStorage(tmp_path / "state.db"):
        pass


@pytest.mark.asyncio
async def test_capacity_admission_only_reopens_after_durable_reclamation(tmp_path: Path) -> None:
    await orphan(tmp_path)
    limits = ArtifactLimits(max_owner_bytes=LEDGER_BYTES + 10, max_store_bytes=3 * LEDGER_BYTES)
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        files = LocalArtifacts(directory=tmp_path / "files", limits=limits)
        await files.open(storage=storage)
        try:
            with pytest.raises(CapacityExceeded):
                await files._reserve(
                    owner=OWNER, run_id="new", operation_id=None, purpose="input", size_bytes=1
                )
        finally:
            await files.aclose()
    async with admin(tmp_path, limits=limits) as maintenance:
        await maintenance.reclaim(await maintenance.preview_reclamation(OWNER))
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        files = LocalArtifacts(directory=tmp_path / "files", limits=limits)
        await files.open(storage=storage)
        try:
            await files._reserve(
                owner=OWNER, run_id="new", operation_id=None, purpose="input", size_bytes=10
            )
        finally:
            await files.aclose()


@pytest.mark.asyncio
async def test_cancellation_waits_for_actual_byte_worker_and_retains_pending_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await orphan(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = os.unlink

    def delayed(name: str, **kwargs: Any) -> None:
        if name.startswith("art_"):
            entered.set()
            assert release.wait(5)
        original(name, **kwargs)

    maintenance = admin(tmp_path)
    await maintenance.open()
    task: asyncio.Task[Any] | None = None
    close: asyncio.Task[Any] | None = None
    try:
        plan = await maintenance.preview_reclamation(OWNER)
        with monkeypatch.context() as patch:
            patch.setattr(os, "unlink", delayed)
            task = asyncio.create_task(maintenance.reclaim(plan))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            close = asyncio.create_task(maintenance.aclose())
            await asyncio.sleep(0.02)
            assert not task.done() and not close.done()
            with pytest.raises(StorageInUse):
                async with SQLiteStorage(tmp_path / "state.db"):
                    pass
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await close
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if close is not None:
            await close
        await maintenance.aclose()
    with pytest.raises(PersistenceUnavailable):
        async with SQLiteStorage(tmp_path / "state.db"):
            pass
    async with admin(tmp_path) as maintenance:
        assert (await maintenance.resume_reclamation()).completed_stages == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["symlink", "hardlink", "extra", "directory", "public"])
async def test_resume_rejects_replaced_or_unaccounted_files_without_touching_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    stage = await orphan(tmp_path)
    async with admin(tmp_path) as maintenance:
        plan = await maintenance.preview_reclamation(OWNER)

        async def fail(*args: Any) -> None:
            raise PersistenceUnavailable("synthetic stop after intent")

        with monkeypatch.context() as patch:
            patch.setattr("drov.storage.maintenance.erase", fail)
            with pytest.raises(PersistenceUnavailable):
                await maintenance.reclaim(plan)
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"preserve")
    sentinel.chmod(0o600)
    directory = tmp_path / "files" / stage
    file = next(directory.glob("art_*"))
    if tamper in ("symlink", "hardlink"):
        file.unlink()
        if tamper == "symlink":
            file.symlink_to(sentinel)
        else:
            os.link(sentinel, file)
    elif tamper == "extra":
        (directory / "unexpected").write_bytes(b"preserve")
    elif tamper == "directory":
        directory.rename(tmp_path / "original")
        directory.mkdir(mode=0o700)
    else:
        file.chmod(0o644)
    async with admin(tmp_path) as maintenance:
        with pytest.raises((PersistenceUnavailable, ConfigurationError)):
            await maintenance.resume_reclamation()
    assert sentinel.read_bytes() == b"preserve" and directory.exists()
    with pytest.raises(PersistenceUnavailable):
        async with SQLiteStorage(tmp_path / "state.db"):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2", "true"])
async def test_future_reclamation_schema_refused_without_mutation(
    tmp_path: Path, version: str
) -> None:
    await orphan(tmp_path)
    async with admin(tmp_path) as maintenance:
        await maintenance.reclaim(await maintenance.preview_reclamation(OWNER))
    with closing(sqlite3.connect(tmp_path / "state.db")) as connection:
        connection.execute("UPDATE metadata SET value=? WHERE key='reclamation_schema'", (version,))
        connection.commit()
    before = byte_snapshot(tmp_path)
    with pytest.raises(IncompatibleStorage):
        async with admin(tmp_path):
            pass
    assert byte_snapshot(tmp_path) == before


@pytest.mark.asyncio
async def test_cli_uses_saved_full_preview_and_resumes_idempotently(tmp_path: Path) -> None:
    await orphan(tmp_path)
    prefix = [sys.executable, "-m", "drov.storage.maintenance"]
    storage = ["--database", str(tmp_path / "state.db"), "--artifacts", str(tmp_path / "files")]

    async def call(args: list[str]) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(
            subprocess.run, [*prefix, *args, *storage], capture_output=True, text=True, timeout=10
        )

    preview = await call(
        ["preview-reclamation", "--tenant", "tenant", "--user", "alice", "--agent", "assistant"]
    )
    assert preview.returncode == 0, preview.stderr
    saved = tmp_path / "preview.json"
    saved.write_text(preview.stdout)
    missing = await call(["reclaim"])
    assert missing.returncode == 2
    result = await call(["reclaim", "--plan-file", str(saved)])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["bytes_reclaimed"] == 7
    resumed = await call(["resume-reclamation"])
    assert resumed.returncode == 0 and json.loads(resumed.stdout)["completed_stages"] == 0
    saved.write_text("x" * 65537)
    bad = await call(["reclaim", "--plan-file", str(saved)])
    assert bad.returncode == 2 and "Traceback" not in bad.stderr


@pytest.mark.asyncio
async def test_resume_rechecks_new_reference_even_if_admin_bypassed_open_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = await orphan(tmp_path)
    file = next((tmp_path / "files" / stage).glob("art_*"))
    async with admin(tmp_path) as maintenance:
        plan = await maintenance.preview_reclamation(OWNER)

        async def stop(*args: Any) -> None:
            raise PersistenceUnavailable("synthetic stop after intent")

        with monkeypatch.context() as patch:
            patch.setattr("drov.storage.maintenance.erase", stop)
            with pytest.raises(PersistenceUnavailable):
                await maintenance.reclaim(plan)
        # Simulate a host administrator changing the locked database directly.
        # Ordinary Storage.open is already barred; this is not a public bypass.
        ref = await maintenance._storage.start(start_request(text="retain " + file.name))
        with pytest.raises(PersistenceUnavailable):
            await maintenance.resume_reclamation()
        assert file.read_bytes() == b"private"
        assert (await maintenance._storage.get_run(ALICE, ref.run_id)).input.endswith(file.name)
