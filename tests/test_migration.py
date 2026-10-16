"""Real historical writer payloads, not only schema version rejection."""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from drov.errors import Conflict, IncompatibleStorage, PersistenceUnavailable, RecoveryBlocked
from drov.storage import SQLiteStorage
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import SessionScope, StartRequest
from drov.tools.operations import OperationJournal
from drov.types import RunContext

ACTOR = RunContext(tenant_id="fixture", user_id="synthetic")


def historical(path: Path) -> dict[str, Any]:
    fixture: dict[str, Any] = json.loads(
        (Path(__file__).parent / "data/storage-t05-v1.json").read_text()
    )
    assert fixture["source_commit"] == "d84c66fb7df36395d679c1dd4b45a99e5bbc23f1"
    assert fixture["source_payload_files_verified"] == 29
    with closing(sqlite3.connect(path)) as connection:
        for statement in fixture["ddl"]:
            connection.execute(statement)
        connection.executemany("INSERT INTO metadata VALUES (?,?)", fixture["metadata"])
        connection.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)", fixture["rows"])
        connection.execute(f"PRAGMA user_version={fixture['database_version']}")
        connection.commit()
    path.chmod(0o600)
    return fixture


def rows(path: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(path)) as connection:
        return list(connection.execute("SELECT * FROM records ORDER BY rowid"))


def request(session: str) -> StartRequest:
    return StartRequest(
        scope=SessionScope(**ACTOR.model_dump(), session_id=session),
        agent_name="assistant",
        revision="1",
        config_hash="a" * 64,
        input="synthetic historical input",
        request_key="historical-once",
    )


@pytest.mark.asyncio
async def test_actual_old_run_event_dispatch_rewrite_preserves_semantics_and_dedup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    fixture = historical(path)
    original = rows(path)
    async with SQLiteStorage(path) as storage:
        runs = [await storage.get_run(ACTOR, value) for value in fixture["run_ids"].values()]
        operation = await OperationJournal(storage, ACTOR).get(
            fixture["run_ids"]["unfinished"], "legacy-write"
        )
        assert operation is not None and operation.status == "unknown"
        assert (await storage.start(request("terminal"))).run_id == fixture["run_ids"]["terminal"]
    before = path.read_bytes()
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_migration()
        assert plan.changes == {"run": 2, "operation": 1}
        assert plan.unfinished_runs == plan.unknown_operations == 1
        assert "synthetic" not in plan.model_dump_json() and "input" not in plan.model_dump_json()
    assert path.read_bytes() == before
    async with SQLiteMaintenance(path) as maintenance:
        receipt = await maintenance.migrate(plan, backup_directory=tmp_path / "backup")
        assert receipt.changes == plan.changes
        after = rows(path)
        assert await maintenance.migrate(plan, backup_directory=tmp_path / "not-created") == receipt
        assert rows(path) == after and not (tmp_path / "not-created").exists()
        assert (await maintenance.preview_migration()).changes == {}
        with pytest.raises(Conflict):
            await maintenance.migrate(
                await maintenance.preview_migration(), backup_directory=tmp_path / "empty"
            )
        assert not (tmp_path / "empty").exists()
    backup = await SQLiteMaintenance.inspect_backup(tmp_path / "backup")
    assert backup.snapshot_id == receipt.backup_snapshot_id
    assert rows(tmp_path / "backup/data/state.sqlite") == original
    restored = await SQLiteMaintenance.restore(tmp_path / "backup", tmp_path / "restored")
    async with SQLiteStorage(restored.database) as storage:
        with pytest.raises(RecoveryBlocked):
            await storage.start(request("terminal"))
    async with SQLiteStorage(path) as storage:
        for expected in runs:
            assert await storage.get_run(ACTOR, expected.state.run_id) == expected
        assert (
            await OperationJournal(storage, ACTOR).get(
                fixture["run_ids"]["unfinished"], "legacy-write"
            )
            == operation
        )
        assert (await storage.start(request("terminal"))).run_id == fixture["run_ids"]["terminal"]
    for old, new in zip(original, after, strict=True):
        assert old[:5] == new[:5] and old[6:] == new[6:]
        if old[0] not in ("run", "operation"):
            assert old == new
    for row in after:
        data = json.loads(row[5])
        if row[0] == "run":
            assert data["artifact_inputs"] == [] and data["artifact_binding"] == {}
        if row[0] == "operation":
            assert (
                data["data"]["memory_sources"] == [] and data["data"]["effect_resolution"] is None
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["plan", "record", "future-record", "future-event", "future-database"]
)
async def test_stale_or_future_migration_refuses_without_backup_or_writes(
    tmp_path: Path, change: str
) -> None:
    path = tmp_path / "state.db"
    historical(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_migration()
    if change == "plan":
        plan = plan.model_copy(update={"changes": {}})
    else:
        with closing(sqlite3.connect(path)) as connection:
            if change == "future-database":
                connection.execute("PRAGMA user_version=2")
            else:
                kind = "event" if change == "future-event" else "run"
                row = connection.execute(
                    "SELECT rowid,payload FROM records WHERE kind=? LIMIT 1", (kind,)
                ).fetchone()
                data = json.loads(row[1])
                data["schema_version"] = 2 if change in ("future-record", "future-event") else 1
                if change == "record":
                    data["input"] = "changed input"
                connection.execute(
                    "UPDATE records SET payload=? WHERE rowid=?", (json.dumps(data), row[0])
                )
            connection.commit()
    before = path.read_bytes()
    with pytest.raises((Conflict, PersistenceUnavailable, IncompatibleStorage)):
        async with SQLiteMaintenance(path) as maintenance:
            await maintenance.migrate(plan, backup_directory=tmp_path / "refused")
    assert path.read_bytes() == before and not (tmp_path / "refused").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["update", "receipt", "lost-commit"])
async def test_transaction_failure_or_lost_receipt_never_half_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = tmp_path / "state.db"
    historical(path)
    before = rows(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_migration()
        original = maintenance._storage._raw
        written = False

        async def fail(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            nonlocal written
            if failure == "update" and sql.startswith("UPDATE records SET payload="):
                raise sqlite3.OperationalError("synthetic update failure")
            if sql.startswith("INSERT INTO migration_receipts"):
                written = True
                if failure == "receipt":
                    raise sqlite3.OperationalError("synthetic receipt failure")
            result = await original(sql, parameters)
            if failure == "lost-commit" and sql == "COMMIT" and written:
                written = False
                raise sqlite3.OperationalError("synthetic lost receipt")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(maintenance._storage, "_raw", fail)
            with pytest.raises(PersistenceUnavailable):
                await maintenance.migrate(plan, backup_directory=tmp_path / "backup")
    await SQLiteMaintenance.inspect_backup(tmp_path / "backup")
    async with SQLiteMaintenance(path) as maintenance:
        if failure == "lost-commit":
            receipt = await maintenance.migrate(plan, backup_directory=tmp_path / "unused")
            assert receipt.changes == plan.changes and not (tmp_path / "unused").exists()
        else:
            assert rows(path) == before
            assert await maintenance.preview_migration() == plan
            await maintenance.migrate(plan, backup_directory=tmp_path / "retry-backup")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary", ["after-backup", "after-first-update", "before-commit", "after-commit"]
)
async def test_real_process_kill_migration_rolls_back_or_has_complete_receipt(
    tmp_path: Path, boundary: str
) -> None:
    import subprocess
    import sys

    path = tmp_path / "state.db"
    historical(path)
    before = rows(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_migration()
    backup, marker = tmp_path / "backup", tmp_path / "marker"
    process = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            "-B",
            str(Path(__file__).with_name("migration_process_fixture.py")),
            str(path),
            str(backup),
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
    await SQLiteMaintenance.inspect_backup(backup)
    async with SQLiteMaintenance(path) as maintenance:
        if boundary == "after-commit":
            receipt = await maintenance.migrate(plan, backup_directory=tmp_path / "unused")
            assert receipt.changes == plan.changes and not (tmp_path / "unused").exists()
        else:
            assert rows(path) == before and await maintenance.preview_migration() == plan
            await maintenance.migrate(plan, backup_directory=tmp_path / "retry")


@pytest.mark.asyncio
async def test_cancel_waits_for_actual_commit_then_original_plan_finds_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from drov.errors import StorageInUse

    path = tmp_path / "state.db"
    historical(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_migration()
        reached, release = asyncio.Event(), asyncio.Event()
        original = maintenance._storage._raw
        written = False

        async def pause(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            nonlocal written
            result = await original(sql, parameters)
            if sql.startswith("INSERT INTO migration_receipts"):
                written = True
            if sql == "COMMIT" and written:
                written = False
                reached.set()
                await release.wait()
            return result

        with monkeypatch.context() as patch:
            patch.setattr(maintenance._storage, "_raw", pause)
            task = asyncio.create_task(
                maintenance.migrate(plan, backup_directory=tmp_path / "backup")
            )
            await asyncio.wait_for(reached.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            with pytest.raises(StorageInUse):
                async with SQLiteMaintenance(path):
                    pass
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    async with SQLiteMaintenance(path) as maintenance:
        receipt = await maintenance.migrate(plan, backup_directory=tmp_path / "unused")
        assert receipt.changes == plan.changes and not (tmp_path / "unused").exists()


@pytest.mark.asyncio
async def test_backup_failure_and_existing_backup_target_leave_original_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import drov.storage._backup as implementation

    path = tmp_path / "state.db"
    historical(path)
    before = rows(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_migration()

        async def fail(*args: Any, **kwargs: Any) -> None:
            raise PersistenceUnavailable("synthetic backup failure")

        with monkeypatch.context() as patch:
            patch.setattr(implementation, "_database", fail)
            with pytest.raises(PersistenceUnavailable):
                await maintenance.migrate(plan, backup_directory=tmp_path / "failed")
        assert rows(path) == before and await maintenance.preview_migration() == plan
        existing = tmp_path / "existing"
        existing.mkdir()
        (existing / "important").write_text("preserve")
        with pytest.raises(FileExistsError):
            await maintenance.migrate(plan, backup_directory=existing)
        assert (existing / "important").read_text() == "preserve" and rows(path) == before


@pytest.mark.asyncio
async def test_migrating_restored_legacy_copy_never_clears_barrier(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    historical(path)
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.backup(tmp_path / "backup")
    result = await SQLiteMaintenance.restore(tmp_path / "backup", tmp_path / "restored")
    async with SQLiteMaintenance(result.database) as maintenance:
        plan = await maintenance.preview_migration()
        await maintenance.migrate(plan, backup_directory=tmp_path / "migration-backup")
        assert await maintenance.get_recovery_barrier() == result.barrier
    async with SQLiteStorage(result.database) as storage:
        with pytest.raises(RecoveryBlocked):
            await storage.start(request("terminal"))


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["migration_schema", "record_schema"])
async def test_future_versions_after_migration_rejected_readonly(
    tmp_path: Path, field: str
) -> None:
    path = tmp_path / "state.db"
    historical(path)
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.migrate(
            await maintenance.preview_migration(), backup_directory=tmp_path / "backup"
        )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE metadata SET value='2' WHERE key=?", (field,))
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(IncompatibleStorage):
        async with SQLiteStorage(path):
            pass
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_real_cli_preview_and_migration_backup_receipt(tmp_path: Path) -> None:
    import subprocess
    import sys

    path = tmp_path / "state.db"
    historical(path)

    async def call(*args: str) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "drov.storage.maintenance", *args, "--database", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )

    preview = await call("preview-migration")
    assert preview.returncode == 0, preview.stderr
    plan = tmp_path / "plan.json"
    plan.write_text(preview.stdout)
    missing = await call("migrate")
    assert missing.returncode == 2
    result = await call(
        "migrate", "--plan-file", str(plan), "--destination", str(tmp_path / "backup")
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["changes"] == {"run": 2, "operation": 1}
    repeated = await call(
        "migrate", "--plan-file", str(plan), "--destination", str(tmp_path / "unused")
    )
    assert repeated.returncode == 0 and json.loads(repeated.stdout) == receipt
    assert not (tmp_path / "unused").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("future", ["envelope", "manifest"])
async def test_future_saved_context_not_certified_as_migratable_or_backup(
    tmp_path: Path, future: str
) -> None:
    from drov import Agent, Runtime
    from drov.models import ScriptedModel
    from tests.test_agent_runs import final

    path = tmp_path / "state.db"
    a: Agent[str] = Agent(name="assistant", revision="1", model=ScriptedModel([final("synthetic")]))
    async with Runtime(agents=[a], storage=SQLiteStorage(path)) as runtime:
        await runtime.run(a, "synthetic", session_id="s", context=ACTOR)
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT rowid,payload FROM records WHERE kind='context' LIMIT 1"
        ).fetchone()
        payload = json.loads(row[1])
        if future == "envelope":
            payload["data"]["schema_version"] = 2
        else:
            payload["data"]["manifest"]["schema_version"] = 3
        connection.execute(
            "UPDATE records SET payload=? WHERE rowid=?", (json.dumps(payload), row[0])
        )
        connection.commit()
    before = path.read_bytes()
    async with SQLiteMaintenance(path) as maintenance:
        with pytest.raises(IncompatibleStorage):
            await maintenance.preview_migration()
        with pytest.raises(IncompatibleStorage):
            await maintenance.backup(tmp_path / "refused")
    assert path.read_bytes() == before and not (tmp_path / "refused").exists()
