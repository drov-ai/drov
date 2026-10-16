"""Snapshot integrity and quarantine across absent post-backup facts."""

import asyncio
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from drov import Agent, Runtime
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.errors import (
    ConfigurationError,
    PersistenceUnavailable,
    RecoveryBlocked,
    StorageInUse,
)
from drov.models import ScriptedModel
from drov.storage import SQLiteStorage
from drov.storage.maintenance import SQLiteMaintenance
from tests.test_agent_runs import final
from tests.test_maintenance import ALICE, SCOPE, populated
from tests.test_storage import start_request


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_database_backup_original_unchanged_and_whole_restored_store_blocked(
    tmp_path: Path, empty: bool
) -> None:
    path, snapshot, restored = (tmp_path / name for name in ("state.db", "backup", "restored"))
    run_id = None
    if empty:
        async with SQLiteStorage(path):
            pass
    else:
        run_id = await populated(path)
    before = path.read_bytes()
    async with SQLiteMaintenance(path) as maintenance:
        manifest = await maintenance.backup(snapshot)
        assert await maintenance.get_recovery_barrier() is None
    assert path.read_bytes() == before
    assert manifest.versions["user_version"] == manifest.versions["record_schema"] == "1"
    assert manifest.files[-1].path == "state.sqlite"
    # Even opening the backup's DB directly cannot dispatch a rolled-back key.
    async with SQLiteStorage(snapshot / "data/state.sqlite") as storage:
        with pytest.raises(RecoveryBlocked):
            await storage.start(start_request())
    result = await SQLiteMaintenance.restore(snapshot, restored)
    assert result.barrier.origin == "restore" and result.barrier.previous_sha256
    assert (
        result.barrier.source_manifest_sha256
        == hashlib.sha256((snapshot / "data/manifest.json").read_bytes()).hexdigest()
    )
    model = ScriptedModel([])
    a: Agent[str] = Agent(name="assistant", revision="1", model=model)
    async with Runtime(agents=[a], storage=SQLiteStorage(result.database)) as runtime:
        for request in (start_request(), start_request(session="absent", key="post-backup")):
            with pytest.raises(RecoveryBlocked):
                await runtime.storage.start(request)
        with pytest.raises(RecoveryBlocked):
            await runtime.start(a, "new", session_id="new", context=ALICE)
        if run_id:
            assert (await runtime.inspect(run_id, context=ALICE)).snapshot.status == "cancelled"
        assert model.calls == []
    async with SQLiteMaintenance(result.database) as maintenance:
        assert await maintenance.get_recovery_barrier() == result.barrier
        if run_id:
            assert (await maintenance.preview_session(SCOPE)).blockers == []
            await maintenance.delete_session(await maintenance.preview_session(SCOPE))
        assert await maintenance.get_recovery_barrier() == result.barrier
    async with SQLiteStorage(result.database) as storage:
        with pytest.raises(RecoveryBlocked):
            await storage.start(start_request(session="still absent"))


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX paired artifact byte layer")
async def test_paired_snapshot_ready_shared_reference_and_reserved_stage(tmp_path: Path) -> None:
    path, files = tmp_path / "state.db", tmp_path / "files"
    a: Agent[str] = Agent(name="assistant", revision="1", model=ScriptedModel([final("ok")] * 2))
    async with Runtime(
        agents=[a], storage=SQLiteStorage(path), artifacts=LocalArtifacts(directory=files)
    ) as runtime:
        first = await runtime.run(
            a,
            "first",
            session_id="source",
            context=ALICE,
            artifacts=[ArtifactInput(name="numbers.csv", data=b"a\n1\n")],
        )
        ref = (await runtime.storage.get_run(ALICE, first.run_id)).artifact_inputs[0]
        await runtime.run(a, "shared", session_id="consumer", context=ALICE, artifacts=[ref])
        store = runtime._files.store()
        from drov.storage.records import MemoryScope, SessionScope

        owner = MemoryScope(**ALICE.model_dump(), agent_name="assistant")
        reserved = await store._reserve(
            owner=owner, run_id=first.run_id, operation_id=None, purpose="output", size_bytes=31
        )
    async with SQLiteMaintenance(path, artifacts=LocalArtifacts(directory=files)) as maintenance:
        await maintenance.delete_session(
            await maintenance.preview_session(
                SessionScope(**ALICE.model_dump(), session_id="source")
            )
        )
        manifest = await maintenance.backup(tmp_path / "backup")
    assert any(item.path.endswith(f"{reserved.stage_id}/stage.json") for item in manifest.files)
    result = await SQLiteMaintenance.restore(tmp_path / "backup", tmp_path / "restored")
    assert result.artifacts is not None
    async with Runtime(
        agents=[a],
        storage=SQLiteStorage(result.database),
        artifacts=LocalArtifacts(directory=result.artifacts, limits=result.artifact_limits),
    ) as runtime:
        assert await runtime.read_artifact(ref, context=ALICE) == b"a\n1\n"
        with pytest.raises(RecoveryBlocked):
            await runtime.start(a, "again", session_id="consumer", context=ALICE)
        assert any(
            stage.stage_id == reserved.stage_id for stage in await runtime._files.store()._pending()
        )


@pytest.mark.asyncio
async def test_existing_destination_and_owned_source_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    await populated(path)
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "valuable"
    marker.write_text("preserve")
    async with SQLiteStorage(path):
        with pytest.raises(StorageInUse):
            async with SQLiteMaintenance(path):
                pass
    async with SQLiteMaintenance(path) as maintenance:
        with pytest.raises(FileExistsError):
            await maintenance.backup(existing)
        await maintenance.backup(tmp_path / "backup")
    with pytest.raises(FileExistsError):
        await SQLiteMaintenance.restore(tmp_path / "backup", existing)
    assert marker.read_text() == "preserve"


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["hash", "missing", "extra", "future", "path"])
async def test_corrupt_bundle_refused_before_destination_creation(
    tmp_path: Path, corruption: str
) -> None:
    path = tmp_path / "state.db"
    await populated(path)
    source, destination = tmp_path / "backup", tmp_path / "restored"
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.backup(source)
    database, manifest = source / "data/state.sqlite", source / "data/manifest.json"
    if corruption == "hash":
        with database.open("r+b") as target:
            target.seek(100)
            target.write(b"corrupt")
    elif corruption == "missing":
        database.unlink()
    elif corruption == "extra":
        (source / "data/extra").write_bytes(b"x")
        (source / "data/extra").chmod(0o600)
    else:
        data = json.loads(manifest.read_text())
        if corruption == "future":
            data["schema_version"] = 2
        else:
            data["files"][0]["path"] = "../../outside"
        manifest.write_text(json.dumps(data))
    with pytest.raises((PersistenceUnavailable, ConfigurationError)):
        await SQLiteMaintenance.restore(source, destination)
    assert not destination.exists()


@pytest.mark.asyncio
async def test_saved_approval_and_absent_post_backup_run_cannot_resume(tmp_path: Path) -> None:
    from drov import ApprovalDecision
    from tests.test_interactions import ALICE as ACTOR
    from tests.test_interactions import Service, build, plan

    path = tmp_path / "state.db"
    service = Service()
    model = ScriptedModel([plan(("read_item", {"value": 7})), final("done")])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        paused = await runtime.run(
            agent, "synthetic", session_id="s", context=ACTOR, request_key="once"
        )
        assert paused.status == "suspended" and paused.pending
        request_id = paused.pending.request_id
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.backup(tmp_path / "backup")
    # The real original continues after the snapshot, consuming the old receipt.
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        await runtime.respond(
            paused.run_id,
            request_id=request_id,
            response=ApprovalDecision(approved=True),
            context=ACTOR,
        )
        assert (await runtime.wait(paused.run_id, context=ACTOR)).status == "completed"
    assert len(service.calls) == 1 and len(model.calls) == 2
    result = await SQLiteMaintenance.restore(tmp_path / "backup", tmp_path / "restored")
    async with Runtime(agents=[agent], storage=SQLiteStorage(result.database)) as runtime:
        assert (await runtime.inspect(paused.run_id, context=ACTOR)).snapshot.status == "suspended"
        for action in (runtime.respond, runtime.resume):
            with pytest.raises(RecoveryBlocked):
                await action(
                    paused.run_id,
                    request_id=request_id,
                    response=ApprovalDecision(approved=True),
                    context=ACTOR,
                )
        with pytest.raises(RecoveryBlocked):
            await runtime.recover(paused.run_id, context=ACTOR)
        for key in ("once", "missing-key", None):
            with pytest.raises(RecoveryBlocked):
                await runtime.start(
                    agent, "synthetic", session_id="s", context=ACTOR, request_key=key
                )
        with pytest.raises(RecoveryBlocked):
            await runtime.start(
                agent,
                "absent",
                session_id="post-backup-session",
                context=ACTOR,
                request_key="post-backup-key",
            )
    assert len(service.calls) == 1 and len(model.calls) == 2


@pytest.mark.asyncio
async def test_unknown_lease_budget_and_abandonment_facts_survive_restore(tmp_path: Path) -> None:
    from drov.sandbox._leases import LeaseTransaction, SandboxLease
    from drov.storage._retention import cost_key
    from drov.storage.records import MemoryScope
    from tests.test_abandonment import unfinished

    path = tmp_path / "state.db"
    run_id = await unfinished(path, operations=("unknown", "prepared"))
    async with SQLiteStorage(path) as storage:
        await storage._bind_sandbox(version=2)
        async with storage.transaction() as tx:
            lease = SandboxLease(
                execution_key="a" * 32,
                owner=MemoryScope(**ALICE.model_dump(), agent_name="assistant"),
                run_id=run_id,
                operation_id="op-0",
                backend_name="python",
                backend_hash="b" * 64,
                code_hash="c" * 64,
                active=True,
                create_intent=True,
                backend_config={
                    "backend": "e2b",
                    "budget_id": "synthetic-budget",
                    "reservation_micro_usd": 100,
                },
            )
            await LeaseTransaction(tx).put(lease)
            key = cost_key(tx._secret, "synthetic-budget")
            await storage._sql("INSERT INTO metadata VALUES (?,?)", (key, "123"))
        original_leases, _ = await storage._sql("SELECT * FROM sandbox_leases")
    async with SQLiteMaintenance(path) as maintenance:
        fact = await maintenance.abandon_run(
            await maintenance.preview_abandonment(
                ALICE, run_id, reason="synthetic external uncertainty"
            )
        )
        await maintenance.backup(tmp_path / "backup")
    result = await SQLiteMaintenance.restore(tmp_path / "backup", tmp_path / "restored")
    async with SQLiteMaintenance(result.database) as maintenance:
        assert await maintenance.get_abandonment(ALICE, run_id) == fact
        assert (await maintenance.preview_session(SCOPE)).blockers == ["unconfirmed_sandbox"]
        assert (await maintenance._storage._sql("SELECT * FROM sandbox_leases"))[
            0
        ] == original_leases
        assert (await maintenance._storage._sql("SELECT value FROM metadata WHERE key=?", (key,)))[
            0
        ] == [("123",)]
        assert await maintenance.get_recovery_barrier() == result.barrier


@pytest.mark.asyncio
async def test_native_backup_api_failure_preserves_original_and_incomplete_refuses_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aiosqlite

    path = tmp_path / "state.db"
    await populated(path)
    before = path.read_bytes()

    async def fail(self: aiosqlite.Connection, *args: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("synthetic backup read failure")

    async with SQLiteMaintenance(path) as maintenance:
        with monkeypatch.context() as patch:
            patch.setattr(aiosqlite.Connection, "backup", fail)
            with pytest.raises(PersistenceUnavailable):
                await maintenance.backup(tmp_path / "incomplete")
        assert await maintenance.get_recovery_barrier() is None
    assert path.read_bytes() == before and not (tmp_path / "incomplete/data").exists()
    with pytest.raises(PersistenceUnavailable):
        await SQLiteMaintenance.restore(tmp_path / "incomplete", tmp_path / "never-created")
    assert not (tmp_path / "never-created").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "future", "bad-payload", "missing-flag"])
async def test_barrier_corruption_is_not_treated_as_no_barrier(tmp_path: Path, change: str) -> None:
    from drov.errors import IncompatibleStorage

    path = tmp_path / "state.db"
    await populated(path)
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.backup(tmp_path / "backup")
    database = tmp_path / "backup/data/state.sqlite"
    with closing(sqlite3.connect(database)) as c:
        if change == "missing":
            c.execute("DELETE FROM recovery_barrier")
        elif change == "future":
            c.execute("UPDATE metadata SET value='2' WHERE key='recovery_schema'")
        elif change == "missing-flag":
            c.execute("DELETE FROM metadata WHERE key='recovery_schema'")
        else:
            c.execute("UPDATE recovery_barrier SET payload='{}'")
        c.commit()
    before = database.read_bytes()
    with pytest.raises((IncompatibleStorage, PersistenceUnavailable)):
        async with SQLiteStorage(database):
            pass
    assert database.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["backup", "restore"])
async def test_cancellation_settles_copy_before_releasing_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    import threading

    import drov.storage._backup as implementation

    path = tmp_path / "state.db"
    await populated(path)
    backup = tmp_path / "backup"
    async with SQLiteMaintenance(path) as maintenance:
        if mode == "restore":
            await maintenance.backup(backup)
        reached, release = threading.Event(), threading.Event()
        original = implementation._Bundle.publish

        def pause(self: Any, partial: Path) -> None:
            reached.set()
            assert release.wait(10)
            original(self, partial)

        with monkeypatch.context() as patch:
            patch.setattr(implementation._Bundle, "publish", pause)
            task = asyncio.create_task(
                maintenance.backup(backup)
                if mode == "backup"
                else SQLiteMaintenance.restore(backup, tmp_path / "restored")
            )
            assert await asyncio.to_thread(reached.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            if mode == "backup":
                with pytest.raises(StorageInUse):
                    async with SQLiteMaintenance(path):
                        pass
            else:
                with pytest.raises(StorageInUse):
                    await SQLiteMaintenance.restore(backup, tmp_path / "other")
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    directory = backup if mode == "backup" else tmp_path / "restored"
    assert (directory / "data").is_dir()
    async with SQLiteStorage(directory / "data/state.sqlite") as storage:
        with pytest.raises(RecoveryBlocked):
            await storage.start(start_request(session="new"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,boundary",
    [
        ("backup", "before-copy"),
        ("backup", "after-copy"),
        ("backup", "before-publish"),
        ("backup", "after-publish"),
        ("restore", "before-publish"),
        ("restore", "after-publish"),
    ],
)
async def test_actual_process_kill_publishes_only_complete_quarantined_pair(
    tmp_path: Path, mode: str, boundary: str
) -> None:
    import subprocess
    import sys

    path = tmp_path / "state.db"
    await populated(path)
    before = path.read_bytes()
    source = path
    if mode == "restore":
        async with SQLiteMaintenance(path) as maintenance:
            await maintenance.backup(tmp_path / "snapshot")
        source = tmp_path / "snapshot"
    target, marker = tmp_path / "target", tmp_path / "marker"
    process = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            "-B",
            str(Path(__file__).with_name("backup_process_fixture.py")),
            mode,
            str(source),
            str(target),
            str(marker),
            boundary,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(20):
            while not marker.exists():
                if process.poll() is not None:
                    _, stderr = process.communicate()
                    pytest.fail(stderr.decode())
                await asyncio.sleep(0.02)
        process.kill()
        await asyncio.to_thread(process.communicate, timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.communicate, timeout=10)
    assert path.read_bytes() == before
    if boundary == "after-publish":
        async with SQLiteStorage(target / "data/state.sqlite") as storage:
            assert (await storage.get_session(SCOPE)).active_run_id is None
            with pytest.raises(RecoveryBlocked):
                await storage.start(start_request(session="missing"))
        if mode == "backup":
            restored = await SQLiteMaintenance.restore(target, tmp_path / "restored")
            async with SQLiteStorage(restored.database) as storage:
                with pytest.raises(RecoveryBlocked):
                    await storage.start(start_request())
    else:
        assert not (target / "data").exists()
        if mode == "backup":
            with pytest.raises(PersistenceUnavailable):
                await SQLiteMaintenance.restore(target, tmp_path / "refused")
    async with SQLiteStorage(path) as storage:
        assert (await storage.get_session(SCOPE)).active_run_id is None


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX paired bytes and links")
@pytest.mark.parametrize(
    "damage", ["missing-ready", "corrupt-ready", "hardlink", "symlink", "public", "wrong-pair"]
)
async def test_ready_byte_damage_or_pairing_refuses_backup_without_repair(
    tmp_path: Path, damage: str
) -> None:
    path, files = tmp_path / "state.db", tmp_path / "files"
    a: Agent[str] = Agent(name="assistant", revision="1", model=ScriptedModel([final("ok")]))
    async with Runtime(
        agents=[a], storage=SQLiteStorage(path), artifacts=LocalArtifacts(directory=files)
    ) as runtime:
        await runtime.run(
            a,
            "input",
            session_id="session",
            context=ALICE,
            artifacts=[ArtifactInput(name="private.csv", data=b"a\n1\n")],
        )
    original = next(files.glob("*/art_*"))
    if damage == "missing-ready":
        original.unlink()
    elif damage == "corrupt-ready":
        original.write_bytes(b"fake")
    elif damage == "public":
        original.chmod(0o644)
    elif damage == "hardlink":
        os.link(original, tmp_path / "outside")
    elif damage == "symlink":
        outside = tmp_path / "outside"
        original.rename(outside)
        original.symlink_to(outside)
    else:
        async with SQLiteStorage(tmp_path / "other.db") as storage:
            other = LocalArtifacts(directory=tmp_path / "other-files")
            await other.open(storage=storage)
            await other.aclose()
        files = tmp_path / "other-files"
    before = path.read_bytes()
    from drov.errors import Conflict

    with pytest.raises((PersistenceUnavailable, ConfigurationError, Conflict)):
        async with SQLiteMaintenance(
            path, artifacts=LocalArtifacts(directory=files)
        ) as maintenance:
            await maintenance.backup(tmp_path / "refused")
    assert path.read_bytes() == before and not (tmp_path / "refused").exists()


@pytest.mark.asyncio
async def test_restore_cannot_query_verifier_or_reissue_unknown_write(tmp_path: Path) -> None:
    from examples.item_service_fixture import ItemService
    from tests.test_reconciliation import ALICE as ACTOR
    from tests.test_reconciliation import build, plan, unknown

    service_dir = tmp_path / "service"
    service_dir.mkdir()
    service = ItemService(service_dir)
    await service.open()
    try:
        service.mode = "reply_lost"
        model = ScriptedModel([plan()])
        agent = build(model, service)
        path = tmp_path / "state.db"
        async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
            paused = await unknown(runtime, agent)
            assert paused.pending is not None
            request_id = paused.pending.request_id
        before = await service.stats()
        async with SQLiteMaintenance(path) as maintenance:
            await maintenance.backup(tmp_path / "backup")
        restored = await SQLiteMaintenance.restore(tmp_path / "backup", tmp_path / "restored")
        async with Runtime(agents=[agent], storage=SQLiteStorage(restored.database)) as runtime:
            with pytest.raises(RecoveryBlocked):
                await runtime.verify(paused.run_id, request_id=request_id, context=ACTOR)
            with pytest.raises(RecoveryBlocked):
                await runtime.recover(paused.run_id, context=ACTOR)
        assert await service.stats() == before and before["writes"] == 1 and len(model.calls) == 1
    finally:
        await service.aclose()


@pytest.mark.asyncio
async def test_cli_backup_restore_barrier_lookup_and_no_overwrite(tmp_path: Path) -> None:
    import subprocess
    import sys

    path = tmp_path / "state.db"
    await populated(path)

    async def call(*args: str) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "drov.storage.maintenance", *args],
            capture_output=True,
            text=True,
            check=False,
        )

    backup = await call(
        "backup", "--database", str(path), "--destination", str(tmp_path / "backup")
    )
    assert backup.returncode == 0 and json.loads(backup.stdout)["format"] == "drov.backup", (
        backup.stderr
    )
    restored = await call(
        "restore", "--source", str(tmp_path / "backup"), "--destination", str(tmp_path / "restored")
    )
    assert restored.returncode == 0, restored.stderr
    result = json.loads(restored.stdout)
    barrier = await call("get-recovery-barrier", "--database", result["database"])
    assert barrier.returncode == 0 and json.loads(barrier.stdout) == result["barrier"]
    again = await call(
        "restore", "--source", str(tmp_path / "backup"), "--destination", str(tmp_path / "restored")
    )
    assert again.returncode == 2 and "error" in json.loads(again.stdout)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "running"])
async def test_observing_backup_does_not_normalize_or_dispatch_original_cursor(
    tmp_path: Path, status: str
) -> None:
    from tests.test_abandonment import unfinished

    path = tmp_path / "state.db"
    run_id = await unfinished(path, status=status)
    async with SQLiteMaintenance(path) as maintenance:
        manifest = await maintenance.backup(tmp_path / "backup")
    database = tmp_path / "backup/data/state.sqlite"
    before = database.read_bytes()
    a: Agent[str] = Agent(name="assistant", revision="1", model=ScriptedModel([]))
    async with Runtime(agents=[a], storage=SQLiteStorage(database)) as runtime:
        assert (await runtime.inspect(run_id, context=ALICE)).snapshot.status == status
        with pytest.raises(RecoveryBlocked):
            await runtime.recover(run_id, context=ALICE)
    assert database.read_bytes() == before
    assert await SQLiteMaintenance.inspect_backup(tmp_path / "backup") == manifest


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX reclamation inventory")
async def test_pending_reclamation_must_finish_before_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from drov.errors import Conflict
    from tests.test_reclamation import OWNER, admin, orphan

    await orphan(tmp_path)

    async def refuse(*args: Any, **kwargs: Any) -> None:
        raise PersistenceUnavailable("synthetic I/O pause")

    async with admin(tmp_path) as maintenance:
        with monkeypatch.context() as patch:
            patch.setattr("drov.storage.maintenance.erase", refuse)
            with pytest.raises(PersistenceUnavailable):
                await maintenance.reclaim(await maintenance.preview_reclamation(OWNER))
        with pytest.raises(Conflict):
            await maintenance.backup(tmp_path / "refused")
        assert not (tmp_path / "refused").exists()
        await maintenance.resume_reclamation()
        manifest = await maintenance.backup(tmp_path / "complete")
        assert manifest.artifact_store_id is not None
    assert await SQLiteMaintenance.inspect_backup(tmp_path / "complete") == manifest


@pytest.mark.parametrize("changed", [False, True])
def test_stat_and_fstat_clock_domains_kept_separate_but_changes_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    from drov.storage._backup import _transfer

    source = tmp_path / "state.sqlite"
    source.write_bytes(b"synthetic bytes")
    source.chmod(0o600)
    original = os.fstat
    calls = 0

    def shifted(fd: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        actual = original(fd)
        fields = {
            name: getattr(actual, name) for name in ("st_atime_ns", "st_mtime_ns", "st_ctime_ns")
        }
        fields["st_ctime_ns"] += calls if changed else 1
        if hasattr(actual, "st_file_attributes"):
            fields["st_file_attributes"] = getattr(actual, "st_file_attributes", 0)
        return os.stat_result(tuple(actual), fields)

    monkeypatch.setattr(os, "fstat", shifted)
    if changed:
        with pytest.raises(PersistenceUnavailable):
            _transfer(source, None, "state.sqlite")
    else:
        result = _transfer(source, None, "state.sqlite")
        assert result.sha256 == hashlib.sha256(b"synthetic bytes").hexdigest()
