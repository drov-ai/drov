"""Offline maintenance uses real SQLite commits, locks and retained references."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from drov import Agent, ResourceBinding, RunContext, Runtime
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.artifacts.transaction import ArtifactTransaction
from drov.auth import Action, OwnerAuthorizer, Resource
from drov.errors import (
    ConfigurationError,
    Conflict,
    ContentGone,
    CursorExpired,
    IncompatibleStorage,
    PersistenceUnavailable,
    RecordNotFound,
    StorageInUse,
)
from drov.models import ScriptedModel
from drov.storage import SQLiteStorage
from drov.storage._retention import cost_key
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import MemoryScope, SessionScope
from drov.types import Completed
from tests.test_agent_runs import final
from tests.test_storage import start_request, transition

ALICE = RunContext(tenant_id="tenant", user_id="alice")
SCOPE = SessionScope(**ALICE.model_dump(), session_id="session")
POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX private artifact byte layer")


async def populated(path: Path, **changes: Any) -> str:
    async with SQLiteStorage(path) as storage:
        request = start_request(**changes)
        ref = await storage.start(request)
        await transition(storage, request.scope, ref, status="cancelled")
        return ref.run_id


def contents(path: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(path)) as connection:
        return list(connection.execute("SELECT * FROM records ORDER BY rowid"))


@pytest.mark.asyncio
async def test_preview_no_extension_no_body_and_unchanged_database(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    await populated(path, text="secret prompt", key="secret request key")
    before = path.read_bytes()
    async with SQLiteMaintenance(path) as maintenance:
        first = await maintenance.preview_session(SCOPE)
        assert first == await maintenance.preview_session(SCOPE)
        assert first.blockers == []
        assert first.counts == {
            "session": 1,
            "run": 1,
            "checkpoint": 1,
            "event": 2,
            "start_key": 1,
            "sandbox_leases": 0,
            "uploads": 0,
        }
        assert first.bytes_reclaimed == first.unreferenced_stages == 0
        assert "secret" not in first.model_dump_json()
    assert before == path.read_bytes()
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute("SELECT value FROM metadata WHERE key='retention_schema'").fetchall()
            == []
        )


@pytest.mark.asyncio
async def test_delete_content_dedup_and_session_ids_survive_reopen(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    run_id = await populated(path, text="secret prompt", key="secret request key")
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_session(SCOPE)
        assert await maintenance.delete_session(plan) == plan
        with pytest.raises(ContentGone):
            await maintenance.preview_session(SCOPE)
    assert contents(path) == []
    with closing(sqlite3.connect(path)) as connection:
        data = json.dumps(connection.execute("SELECT * FROM retention_tombstones").fetchall())
        assert "secret" not in data and "alice" not in data and '"tenant"' not in data
        assert run_id in data
    async with SQLiteStorage(path) as storage:
        for request in [start_request(), start_request(key=None), start_request(text="changed")]:
            with pytest.raises(ContentGone):
                await storage.start(request)
        with pytest.raises(ContentGone):
            await storage.get_run(ALICE, run_id)
        with pytest.raises(CursorExpired):
            await storage.events(ALICE, run_id, after=1)
        async with storage.transaction() as tx:
            with pytest.raises(ContentGone):
                await tx._locate_run(ALICE, run_id)
        fresh = await storage.start(start_request(session="fresh"))
        assert fresh.run_id != run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("other", [{"user": "bob"}, {"tenant": "another"}])
async def test_same_ids_other_scope_not_blocked_or_exposed(
    tmp_path: Path, other: dict[str, Any]
) -> None:
    path = tmp_path / "state.db"
    run_id = await populated(path)
    other_id = await populated(path, **other)
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.delete_session(await maintenance.preview_session(SCOPE))
    context = start_request(**other).scope
    async with SQLiteStorage(path) as storage:
        assert (await storage.get_run(context, other_id)).state.status == "cancelled"
        with pytest.raises(RecordNotFound):
            await storage.get_run(context, run_id)
        # Same request/session string is valid in a different owner scope.
        assert (await storage.start(start_request(**other))).run_id == other_id


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "running", "suspended"])
async def test_unfinished_run_is_a_blocker(tmp_path: Path, status: str) -> None:
    from drov.storage.records import Checkpoint, EventDraft, RunState

    path = tmp_path / "state.db"
    async with SQLiteStorage(path) as storage:
        ref = await storage.start(start_request())
        if status == "running":
            await transition(storage, SCOPE, ref)
        elif status == "suspended":
            async with storage.transaction() as tx:
                await tx.save_step(
                    SCOPE,
                    expected_version=1,
                    state=RunState(
                        run_id=ref.run_id, state_version=2, status="suspended", reason="recovery"
                    ),
                    checkpoint=Checkpoint(run_id=ref.run_id, state_version=2, cursor="recovery"),
                    events=[EventDraft(type="recovery.required")],
                )
    before = contents(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_session(SCOPE)
        assert "unfinished_run" in plan.blockers
        with pytest.raises(Conflict):
            await maintenance.delete_session(plan)
    assert before == contents(path)


@pytest.mark.asyncio
async def test_exclusive_lifecycle_missing_database_and_no_implicit_pair(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    async with SQLiteStorage(path):
        with pytest.raises(StorageInUse):
            async with SQLiteMaintenance(path):
                pass
    maintenance = SQLiteMaintenance(path)
    with pytest.raises(ConfigurationError):
        await maintenance.preview_session(SCOPE)
    async with maintenance:
        with pytest.raises(StorageInUse):
            await maintenance.open()
        with pytest.raises(StorageInUse):
            async with SQLiteStorage(path):
                pass
    missing = tmp_path / "missing.db"
    with pytest.raises(ConfigurationError):
        async with SQLiteMaintenance(missing):
            pass
    assert not missing.exists()


@pytest.mark.asyncio
async def test_plan_tampering_and_staleness_do_not_delete(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    await populated(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_session(SCOPE)
        changes: list[dict[str, Any]] = [
            {"fingerprint": "0" * 64},
            {"counts": {}},
            {"include_memory": True},
            {"blockers": ["fake"]},
            {"bytes_reclaimed": 100},
        ]
        for changed in changes:
            with pytest.raises(Conflict):
                await maintenance.delete_session(plan.model_copy(update=changed))
    await populated(path, session="second")
    async with SQLiteMaintenance(path) as maintenance:
        with pytest.raises(Conflict):
            await maintenance.delete_session(plan)
        await maintenance.delete_session(await maintenance.preview_session(SCOPE))


@pytest.mark.asyncio
@pytest.mark.parametrize("include_memory", [False, True])
async def test_memory_scope_is_explicit_and_other_agents_survive(
    tmp_path: Path, include_memory: bool
) -> None:
    from datetime import UTC, datetime

    from drov.storage.records import MemoryRecord

    path = tmp_path / "state.db"
    await populated(path)
    async with SQLiteStorage(path) as storage:
        async with storage.transaction() as tx:
            for agent in ("assistant", "another"):
                await tx.put_memory(
                    MemoryRecord(
                        scope=MemoryScope(**ALICE.model_dump(), agent_name=agent),
                        key="preference",
                        version=1,
                        content="secret memory",
                        source="explicit",
                        updated_at=datetime.now(UTC),
                    ),
                    expected_version=0,
                )
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_session(SCOPE, include_memory=include_memory)
        assert plan.counts.get("memory", 0) == int(include_memory)
        await maintenance.delete_session(plan)
    async with SQLiteStorage(path) as storage:
        async with storage.transaction() as tx:
            assert (
                await tx.get_memory(
                    MemoryScope(**ALICE.model_dump(), agent_name="assistant"), "preference"
                )
                is None
            ) == include_memory
            assert (
                await tx.get_memory(
                    MemoryScope(**ALICE.model_dump(), agent_name="another"), "preference"
                )
                is not None
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary", ["INSERT INTO retention_tombstones", "DELETE FROM records", "COMMIT"]
)
async def test_sql_failure_rolls_back_records_and_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    path = tmp_path / "state.db"
    await populated(path)
    before = contents(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_session(SCOPE)
        original = maintenance._storage._raw
        fired = False

        async def fail(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            nonlocal fired
            if not fired and sql.startswith(boundary):
                fired = True
                raise sqlite3.OperationalError("synthetic maintenance failure")
            return await original(sql, parameters)

        monkeypatch.setattr(maintenance._storage, "_raw", fail)
        with pytest.raises(PersistenceUnavailable):
            await maintenance.delete_session(plan)
        assert fired
    assert contents(path) == before
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='retention_tombstones'"
            ).fetchall()
            == []
        )


@pytest.mark.asyncio
async def test_commit_ack_lost_preserves_tombstone_not_a_second_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    await populated(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_session(SCOPE)
        original = maintenance._storage._raw
        fired = False

        async def lost(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            nonlocal fired
            result = await original(sql, parameters)
            if sql == "COMMIT" and not fired:
                fired = True
                raise sqlite3.OperationalError("synthetic lost commit receipt")
            return result

        monkeypatch.setattr(maintenance._storage, "_raw", lost)
        with pytest.raises(PersistenceUnavailable):
            await maintenance.delete_session(plan)
    async with SQLiteStorage(path) as storage:
        with pytest.raises(ContentGone):
            await storage.start(start_request())


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["2", "true"])
async def test_future_retention_version_refused_without_mutation(
    tmp_path: Path, invalid: str
) -> None:
    path = tmp_path / "state.db"
    await populated(path)
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.delete_session(await maintenance.preview_session(SCOPE))
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE metadata SET value=? WHERE key='retention_schema'", (invalid,))
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(IncompatibleStorage):
        async with SQLiteMaintenance(path):
            pass
    assert path.read_bytes() == before


def file_runtime(path: Path, a: Agent[Any], **kwargs: Any) -> Runtime:
    return Runtime(
        agents=[a],
        storage=SQLiteStorage(path / "state.db"),
        artifacts=ResourceBinding.owned(LocalArtifacts(directory=path / "files")),
        **kwargs,
    )


@pytest.mark.asyncio
@POSIX
async def test_shared_file_survives_origin_cleanup_and_last_reference_becomes_gone(
    tmp_path: Path,
) -> None:
    a: Agent[str] = Agent(name="assistant", revision="1", model=ScriptedModel([final("done")] * 4))
    data = b"value\n12\n18\n30\n"
    async with file_runtime(tmp_path, a) as rt:
        result = await rt.run(
            a,
            "first",
            session_id="session",
            context=ALICE,
            artifacts=[ArtifactInput(name="secret.csv", data=data)],
        )
        assert isinstance(result, Completed)
        ref = (await rt.storage.get_run(ALICE, result.run_id)).artifact_inputs[0]
        reused = await rt.run(a, "second", session_id="reused", context=ALICE, artifacts=[ref])
        assert isinstance(reused, Completed)
    bytes_before = {
        p.relative_to(tmp_path / "files"): p.read_bytes()
        for p in (tmp_path / "files").rglob("*")
        if p.is_file()
    }
    async with SQLiteMaintenance(
        tmp_path / "state.db", artifacts=LocalArtifacts(directory=tmp_path / "files")
    ) as maintenance:
        plan = await maintenance.preview_session(SCOPE)
        assert plan.retained_artifacts == 1 and plan.cleared_artifacts == 0
        await maintenance.delete_session(plan)
    async with file_runtime(tmp_path, a) as rt:
        assert await rt.read_artifact(ref, context=ALICE) == data
        view = await rt.inspect(reused.run_id, context=ALICE, include_content=True)
        assert view.snapshot.status == "completed"
        async with rt.storage.transaction() as tx:
            source = await ArtifactTransaction(tx).locate(ALICE, ref.artifact_id)
            # Minimal proof binds the original immutable name and stage too.
            with pytest.raises(PersistenceUnavailable):
                changed = source.model_copy(update={"name": "changed.csv"})
                await cast_sql(tx).execute(
                    "UPDATE artifact_records SET payload=? WHERE artifact_id=?",
                    (changed.model_dump_json(), ref.artifact_id),
                )
                await ArtifactTransaction(tx).locate(ALICE, ref.artifact_id)
            # Throwing from an unguarded read does not poison the transaction;
            # restore the intentionally tampered test metadata before commit.
            await cast_sql(tx).execute(
                "UPDATE artifact_records SET payload=? WHERE artifact_id=?",
                (source.model_dump_json(), ref.artifact_id),
            )
    async with SQLiteMaintenance(
        tmp_path / "state.db", artifacts=LocalArtifacts(directory=tmp_path / "files")
    ) as maintenance:
        plan = await maintenance.preview_session(SCOPE.model_copy(update={"session_id": "reused"}))
        assert plan.cleared_artifacts == 1 and plan.unreferenced_stages == 1
        assert plan.bytes_reclaimed == 0
        await maintenance.delete_session(plan)
    assert bytes_before == {
        p.relative_to(tmp_path / "files"): p.read_bytes()
        for p in (tmp_path / "files").rglob("*")
        if p.is_file()
    }
    async with file_runtime(tmp_path, a) as rt:
        with pytest.raises(ContentGone):
            await rt.read_artifact(ref, context=ALICE)
        with pytest.raises(RecordNotFound):
            await rt.read_artifact(ref, context=RunContext(tenant_id="tenant", user_id="bob"))


def cast_sql(tx: Any) -> Any:
    return tx._driver


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["dispatched", "unknown"])
async def test_terminal_state_does_not_discard_unknown_operation(
    tmp_path: Path, status: str
) -> None:
    from drov.storage.records import EventDraft
    from drov.tools.operations import OperationJournal, ToolOperation

    path = tmp_path / "state.db"
    async with SQLiteStorage(path) as storage:
        ref = await storage.start(start_request())
        ref = await transition(storage, SCOPE, ref)
        operation = ToolOperation.model_validate(
            dict(
                run_id=ref.run_id,
                operation_id="op",
                tool_name="external.write",
                tool_revision="1",
                effect="write",
                request_hash="a" * 64,
                arguments={"private": "secret arguments"},
                attempts=1,
                attempt_id="attempt",
                status=status,
            )
        )
        await OperationJournal(storage, ALICE).commit(
            operation, EventDraft(type="tool.dispatched", data={"operation_id": "op"})
        )
        current = await storage.get_run(ALICE, ref.run_id)
        await transition(storage, SCOPE, current.state, status="cancelled")
    before = contents(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_session(SCOPE)
        assert plan.blockers == ["unresolved_operation"]
        assert "secret" not in plan.model_dump_json()
        with pytest.raises(Conflict):
            await maintenance.delete_session(plan)
    assert before == contents(path)


@pytest.mark.asyncio
async def test_cancelled_commit_receipt_reloads_retention_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    await populated(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_session(SCOPE)
        original = maintenance._storage._raw
        committed, release = asyncio.Event(), asyncio.Event()

        async def stop(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            result = await original(sql, parameters)
            if sql == "COMMIT":
                committed.set()
                await release.wait()
            return result

        monkeypatch.setattr(maintenance._storage, "_raw", stop)
        task = asyncio.create_task(maintenance.delete_session(plan))
        await asyncio.wait_for(committed.wait(), 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ContentGone):
            await maintenance.preview_session(SCOPE)


@pytest.mark.asyncio
async def test_explicit_memory_delete_protects_other_unfinished_sessions(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    await populated(path)
    async with SQLiteStorage(path) as storage:
        await storage.start(start_request(session="pending"))
    async with SQLiteMaintenance(path) as maintenance:
        regular = await maintenance.preview_session(SCOPE)
        assert regular.blockers == []
        memory = await maintenance.preview_session(SCOPE, include_memory=True)
        assert memory.blockers == ["memory_scope_in_use"]
        with pytest.raises(Conflict):
            await maintenance.delete_session(memory)
        await maintenance.delete_session(regular)


@pytest.mark.asyncio
async def test_readonly_open_of_old_database_keeps_original_format(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    run_id = await populated(path)
    before = path.read_bytes()
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.preview_session(SCOPE)
    async with SQLiteStorage(path) as storage:
        assert (await storage.get_run(ALICE, run_id)).state.status == "cancelled"
        rows, _ = await storage._sql("SELECT key FROM metadata")
        assert "retention_schema" not in {row[0] for row in rows}
    assert before == path.read_bytes()


class DenyReads(OwnerAuthorizer):
    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
        return False if action == "read" else await super().authorize(actor, action, resource)


@pytest.mark.asyncio
async def test_gone_still_requires_current_read_permission(tmp_path: Path) -> None:
    a: Agent[str] = Agent(name="assistant", revision="1", model=ScriptedModel([final("done")]))
    async with Runtime(agents=[a], storage=SQLiteStorage(tmp_path / "state.db")) as rt:
        result = await rt.run(a, "first", session_id="session", context=ALICE)
    async with SQLiteMaintenance(tmp_path / "state.db") as maintenance:
        await maintenance.delete_session(await maintenance.preview_session(SCOPE))
    async with Runtime(
        agents=[a], storage=SQLiteStorage(tmp_path / "state.db"), authorizer=DenyReads()
    ) as rt:
        with pytest.raises(RecordNotFound):
            await rt.inspect(result.run_id, context=ALICE)


@pytest.mark.asyncio
@POSIX
async def test_pair_required_and_wrong_pair_does_not_change_database(tmp_path: Path) -> None:
    a: Agent[str] = Agent(name="assistant", revision="1", model=ScriptedModel([final("done")]))
    async with file_runtime(tmp_path, a) as rt:
        await rt.run(a, "first", session_id="session", context=ALICE)
    path = tmp_path / "state.db"
    before = path.read_bytes()
    with pytest.raises(ConfigurationError):
        async with SQLiteMaintenance(path):
            pass
    with pytest.raises(ConfigurationError):
        async with SQLiteMaintenance(
            path, artifacts=LocalArtifacts(directory=tmp_path / "missing")
        ):
            pass
    assert not (tmp_path / "missing").exists()
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_actual_cli_preview_and_fingerprint_delete(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    await populated(path)
    args = [
        sys.executable,
        "-m",
        "drov.storage.maintenance",
        "preview-session",
        "--database",
        str(path),
        "--tenant",
        "tenant",
        "--user",
        "alice",
        "--session",
        "session",
    ]
    preview = await asyncio.to_thread(
        subprocess.run, args, capture_output=True, text=True, timeout=10
    )
    assert preview.returncode == 0, preview.stderr
    plan = json.loads(preview.stdout)
    args[3] = "delete-session"
    missing = await asyncio.to_thread(
        subprocess.run, args, capture_output=True, text=True, timeout=10
    )
    assert missing.returncode == 2 and contents(path)
    applied = await asyncio.to_thread(
        subprocess.run,
        [*args, "--fingerprint", plan["fingerprint"]],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert applied.returncode == 0, applied.stderr
    assert contents(path) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before", "after"])
async def test_actual_process_kill_at_deletion_commit(tmp_path: Path, boundary: str) -> None:
    path, marker = tmp_path / "state.db", tmp_path / "marker"
    await populated(path)
    worker = Path(__file__).with_name("maintenance_process_fixture.py")
    process = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, str(worker), str(path), str(marker), boundary],
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
            async with SQLiteStorage(path):
                pass
    finally:
        if process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.wait, timeout=5)
        assert process.stderr is not None
        process.stderr.close()
    async with SQLiteStorage(path) as storage:
        if boundary == "before":
            assert (await storage.start(start_request())).state_version == 1
        else:
            with pytest.raises(ContentGone):
                await storage.start(start_request())


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [False, True])
async def test_unconfirmed_lease_blocks_even_a_terminal_run(tmp_path: Path, active: bool) -> None:
    from drov.sandbox._leases import LeaseTransaction, SandboxLease

    path = tmp_path / "state.db"
    run_id = await populated(path)
    async with SQLiteStorage(path) as storage:
        await storage._bind_sandbox()
        async with storage.transaction() as tx:
            await LeaseTransaction(tx).put(
                SandboxLease(
                    execution_key="a" * 32,
                    owner=MemoryScope(**ALICE.model_dump(), agent_name="assistant"),
                    run_id=run_id,
                    operation_id="op",
                    backend_name="python",
                    backend_hash="b" * 64,
                    code_hash="c" * 64,
                    active=active,
                    create_intent=active,
                )
            )
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_session(SCOPE)
        assert ("unconfirmed_sandbox" in plan.blockers) == active
        if active:
            with pytest.raises(Conflict):
                await maintenance.delete_session(plan)
        else:
            await maintenance.delete_session(plan)


@pytest.mark.asyncio
async def test_destroyed_cloud_reservation_is_transferred_not_refunded(tmp_path: Path) -> None:
    from drov.errors import CapacityExceeded
    from drov.sandbox._leases import LeaseTransaction, SandboxLease
    from drov.storage.sqlite import SQLiteDriver
    from tests.test_e2b_contracts import backend

    path = tmp_path / "state.db"
    run_id = await populated(path)
    cloud = backend()
    # Construction and reserve_in use no SDK or provider calls.
    cloud._budget = "offline-budget"
    cloud._ceiling = cloud.reservation_micro_usd
    async with SQLiteStorage(path) as storage:
        await storage._bind_sandbox(version=2)
        async with storage.transaction() as tx:
            await LeaseTransaction(tx).put(
                SandboxLease(
                    execution_key="a" * 32,
                    owner=MemoryScope(**ALICE.model_dump(), agent_name="assistant"),
                    run_id=run_id,
                    operation_id="op",
                    backend_name="python",
                    backend_hash="b" * 64,
                    code_hash="c" * 64,
                    create_intent=True,
                    destroyed=True,
                    backend_config={
                        "backend": "e2b",
                        "budget_id": "offline-budget",
                        "reservation_micro_usd": cloud.reservation_micro_usd,
                    },
                )
            )
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.delete_session(await maintenance.preview_session(SCOPE))
    async with SQLiteStorage(path) as storage:
        async with storage.transaction() as tx:
            assert isinstance(tx._driver, SQLiteDriver)
            rows, _ = await tx._driver.execute(
                "SELECT value FROM metadata WHERE key=?", (cost_key(tx._secret, "offline-budget"),)
            )
            assert rows == [(str(cloud.reservation_micro_usd),)]
            with pytest.raises(CapacityExceeded):
                await cloud.reserve_in(
                    tx,
                    SandboxLease(
                        execution_key="d" * 32,
                        owner=MemoryScope(**ALICE.model_dump(), agent_name="assistant"),
                        run_id="new-run",
                        operation_id="op",
                        backend_name="python",
                        backend_hash="b" * 64,
                        code_hash="c" * 64,
                    ),
                )
