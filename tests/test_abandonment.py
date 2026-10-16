"""Abandonment preserves uncertainty across terminal state, cleanup and restarts."""

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

from drov import Agent, Runtime
from drov.errors import (
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
from drov.storage.records import Checkpoint, EventDraft
from drov.tools.operations import OperationJournal, ToolOperation
from drov.types import Failed
from tests.test_maintenance import ALICE, SCOPE, contents, populated
from tests.test_storage import start_request, transition


async def unfinished(
    path: Path, *, status: str = "running", operations: tuple[str, ...] = ()
) -> str:
    async with SQLiteStorage(path) as storage:
        ref = await storage.start(start_request(text="secret input", key="secret key"))
        if status != "accepted":
            await transition(storage, SCOPE, ref)
        for index, kind in enumerate(operations):
            op = ToolOperation.model_validate(
                dict(
                    run_id=ref.run_id,
                    operation_id=f"op-{index}",
                    tool_name="external.write",
                    tool_revision="1",
                    effect="write",
                    request_hash="b" * 64,
                    arguments={"private": "secret arguments"},
                    status=kind,
                    attempts=0 if kind == "prepared" else 1,
                    attempt_id=None if kind == "prepared" else f"attempt-{index}",
                )
            )
            await OperationJournal(storage, ALICE).commit(
                op, EventDraft.model_validate({"type": "tool." + kind})
            )
        if status == "suspended":
            current = await storage.get_run(ALICE, ref.run_id)
            version = current.state.state_version + 1
            async with storage.transaction() as tx:
                await tx.save_step(
                    ALICE,
                    expected_version=version - 1,
                    state=current.state.model_copy(
                        update={
                            "state_version": version,
                            "status": "suspended",
                            "reason": "recovery",
                            "recoverable": True,
                        }
                    ),
                    checkpoint=Checkpoint(
                        run_id=ref.run_id, state_version=version, cursor="recovery"
                    ),
                    events=[EventDraft(type="recovery.required")],
                )
        return ref.run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["accepted", "running", "suspended"])
async def test_preview_readonly_and_terminal_fact_survives_content_cleanup(
    tmp_path: Path, status: str
) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path, status=status)
    before = path.read_bytes()
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_abandonment(ALICE, run_id, reason="source process lost")
        assert plan.status == status and plan.unknown_operations == plan.retained_leases == 0
        assert "secret" not in plan.model_dump_json()
    assert path.read_bytes() == before
    async with SQLiteMaintenance(path) as maintenance:
        fact = await maintenance.abandon_run(plan)
        assert fact.original_status == status and fact.reason == "source process lost"
        assert await maintenance.abandon_run(plan) == fact
        current = await maintenance._storage.get_run(ALICE, run_id)
        assert current.state.status == "failed" and not current.state.recoverable
        assert isinstance(current.state.outcome, Failed)
        assert current.state.outcome.error.code == "data_abandoned"
        assert not current.state.outcome.error.retryable and not current.state.usage.complete
        assert "source process lost" not in current.state.model_dump_json()
        assert (await maintenance._storage.get_session(SCOPE)).active_run_id is None
        before_repeat = contents(path)
        assert await maintenance.abandon_run(plan) == fact
        assert contents(path) == before_repeat
        assert (await maintenance.preview_session(SCOPE)).blockers == []
        await maintenance.delete_session(await maintenance.preview_session(SCOPE))
        assert await maintenance.get_abandonment(ALICE, run_id) == fact
        assert await maintenance.abandon_run(plan) == fact
    async with SQLiteMaintenance(path) as maintenance:
        assert await maintenance.get_abandonment(ALICE, run_id) == fact
        with pytest.raises(RecordNotFound):
            await maintenance.get_abandonment(ALICE.model_copy(update={"user_id": "bob"}), run_id)
    async with SQLiteStorage(path) as storage:
        with pytest.raises(ContentGone):
            await storage.start(start_request(text="secret input", key="secret key"))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["dispatched", "unknown"])
async def test_unknown_operation_kept_exactly_then_minimal_fact_survives_deletion(
    tmp_path: Path, status: str
) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path, operations=(status, "prepared"))
    async with SQLiteStorage(path) as storage:
        original = await OperationJournal(storage, ALICE).get(run_id, "op-0")
    async with SQLiteMaintenance(path) as maintenance:
        assert "unresolved_operation" in (await maintenance.preview_session(SCOPE)).blockers
        plan = await maintenance.preview_abandonment(
            ALICE, run_id, reason="cannot reconstruct provider result"
        )
        assert plan.unknown_operations == 1
        fact = await maintenance.abandon_run(plan)
        assert fact.unknown_operations[0].status == status
        assert fact.unknown_operations[0].tool_name == "external.write"
        assert "secret" not in fact.model_dump_json() and "arguments" not in fact.model_dump_json()
        assert await OperationJournal(maintenance._storage, ALICE).get(run_id, "op-0") == original
        stopped = await OperationJournal(maintenance._storage, ALICE).get(run_id, "op-1")
        assert stopped is not None and stopped.status == "not_executed" and stopped.attempts == 0
        assert stopped.error_code == "data_abandoned_before_dispatch"
    # Normal Runtime cannot revive this terminal state, and performs no model work.
    model = ScriptedModel([])
    agent: Agent[str] = Agent(name="assistant", revision="1", model=model)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        result = await runtime.wait(run_id, context=ALICE)
        assert isinstance(result, Failed) and result.error.code == "data_abandoned"
        with pytest.raises(Conflict):
            await runtime.recover(run_id, context=ALICE)
        assert not (await runtime.inspect(run_id, context=ALICE)).snapshot.recoverable
    assert not model.calls
    async with SQLiteStorage(path) as storage:
        old = await storage.start(start_request(text="secret input", key="secret key"))
        assert old.run_id == run_id
    async with SQLiteMaintenance(path) as maintenance:
        deletion = await maintenance.preview_session(SCOPE)
        assert deletion.blockers == []
        await maintenance.delete_session(deletion)
        fact = await maintenance.get_abandonment(ALICE, run_id)
        assert (
            fact.unknown_operations[0].operation_id == "op-0"
            and fact.unknown_operations[0].status == status
        )
    assert not contents(path)
    with closing(sqlite3.connect(path)) as connection:
        retained = json.dumps(connection.execute("SELECT * FROM abandonment_facts").fetchall())
    assert "secret" not in retained and "alice" not in retained and "arguments" not in retained


@pytest.mark.asyncio
async def test_ordinary_terminal_unknown_is_still_protected(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path, operations=("unknown",))
    async with SQLiteStorage(path) as storage:
        current = await storage.get_run(ALICE, run_id)
        await transition(storage, SCOPE, current.state, status="cancelled")
    async with SQLiteMaintenance(path) as maintenance:
        assert (await maintenance.preview_session(SCOPE)).blockers == ["unresolved_operation"]
        with pytest.raises(Conflict):
            await maintenance.preview_abandonment(ALICE, run_id, reason="already terminal")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["reason", "count", "database"])
async def test_tampered_or_stale_plan_refused_atomically(tmp_path: Path, change: str) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_abandonment(ALICE, run_id, reason="lost process")
    if change == "reason":
        plan = plan.model_copy(update={"reason": "another reason"})
    elif change == "count":
        plan = plan.model_copy(update={"retained_leases": 1})
    else:
        await populated(path, session="another")
    before = contents(path)
    async with SQLiteMaintenance(path) as maintenance:
        with pytest.raises(Conflict):
            await maintenance.abandon_run(plan)
    assert contents(path) == before
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='abandonment_facts'"
            ).fetchall()
            == []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["", "   ", "x\nunsafe", "x" * 513])
async def test_reason_is_explicit_bounded_printable_and_no_writes(
    tmp_path: Path, reason: str
) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path)
    before = contents(path)
    async with SQLiteMaintenance(path) as maintenance:
        with pytest.raises(ValueError):
            await maintenance.preview_abandonment(ALICE, run_id, reason=reason)
    assert contents(path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["INSERT INTO abandonment_facts", "UPDATE records", "COMMIT"])
async def test_failures_roll_back_state_events_and_fact_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path, operations=("unknown", "prepared"))
    before = contents(path)
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_abandonment(ALICE, run_id, reason="lost process")
        original = maintenance._storage._raw
        fired = False

        async def fail(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            nonlocal fired
            if not fired and sql.startswith(boundary):
                fired = True
                raise sqlite3.OperationalError("synthetic failed abandonment")
            return await original(sql, parameters)

        monkeypatch.setattr(maintenance._storage, "_raw", fail)
        with pytest.raises(PersistenceUnavailable):
            await maintenance.abandon_run(plan)
        assert fired
    assert contents(path) == before


@pytest.mark.asyncio
async def test_lost_commit_receipt_is_safe_to_retry_with_same_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path, operations=("unknown",))
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_abandonment(ALICE, run_id, reason="lost process")
        original = maintenance._storage._raw
        fired = False

        async def lost(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            nonlocal fired
            result = await original(sql, parameters)
            if sql == "COMMIT" and not fired:
                fired = True
                raise sqlite3.OperationalError("synthetic lost receipt")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(maintenance._storage, "_raw", lost)
            with pytest.raises(PersistenceUnavailable):
                await maintenance.abandon_run(plan)
        with pytest.raises(PersistenceUnavailable):
            await maintenance.abandon_run(plan)
    before = contents(path)
    async with SQLiteMaintenance(path) as maintenance:
        fact = await maintenance.abandon_run(plan)
        assert fact.state_version == plan.state_version + 1
        assert contents(path) == before
        with pytest.raises(Conflict):
            await maintenance.abandon_run(plan.model_copy(update={"reason": "changed"}))


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2", "true"])
async def test_future_abandonment_schema_refused_without_writes(
    tmp_path: Path, version: str
) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path)
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.abandon_run(
            await maintenance.preview_abandonment(ALICE, run_id, reason="lost process")
        )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("UPDATE metadata SET value=? WHERE key='abandonment_schema'", (version,))
        connection.commit()
    before = path.read_bytes()
    with pytest.raises(IncompatibleStorage):
        async with SQLiteMaintenance(path):
            pass
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_live_runtime_owner_cannot_be_forced_to_abandon(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path)
    async with SQLiteStorage(path) as storage:
        with pytest.raises(StorageInUse):
            async with SQLiteMaintenance(path):
                pass
        assert (await storage.get_run(ALICE, run_id)).state.status == "running"


@pytest.mark.asyncio
async def test_active_sandbox_lease_and_cloud_reservation_are_not_nuked(tmp_path: Path) -> None:
    from drov.sandbox._leases import LeaseTransaction, SandboxLease
    from drov.storage._retention import cost_key
    from drov.storage.records import MemoryScope

    path = tmp_path / "state.db"
    run_id = await unfinished(path, operations=("unknown",))
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
        before, _ = await storage._sql("SELECT * FROM sandbox_leases")
    async with SQLiteMaintenance(path) as maintenance:
        preview = await maintenance.preview_abandonment(ALICE, run_id, reason="source process lost")
        assert preview.retained_leases == 1
        fact = await maintenance.abandon_run(preview)
        after, _ = await maintenance._storage._sql("SELECT * FROM sandbox_leases")
        assert before == after and fact.retained_leases == 1
        costs, _ = await maintenance._storage._sql("SELECT value FROM metadata WHERE key=?", (key,))
        assert costs == [("123",)]
        deletion = await maintenance.preview_session(SCOPE)
        assert deletion.blockers == ["unconfirmed_sandbox"]
        with pytest.raises(Conflict):
            await maintenance.delete_session(deletion)


@pytest.mark.asyncio
async def test_real_pending_approval_is_closed_and_cannot_dispatch_after_abandonment(
    tmp_path: Path,
) -> None:
    from drov import ApprovalDecision
    from drov.errors import InteractionConflict
    from tests.test_interactions import ALICE as ACTOR
    from tests.test_interactions import Service, build, plan

    service = Service()
    model = ScriptedModel([plan(("read_item", {"value": 7}))])
    agent = build(model, service)
    path = tmp_path / "state.db"
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        paused = await runtime.run(agent, "synthetic", session_id="s", context=ACTOR)
        assert paused.status == "suspended" and paused.pending is not None
        request_id = paused.pending.request_id
    async with SQLiteMaintenance(path) as maintenance:
        preview = await maintenance.preview_abandonment(
            ACTOR, paused.run_id, reason="operator cannot continue"
        )
        await maintenance.abandon_run(preview)
        async with maintenance._storage.transaction() as tx:
            item = await tx.get_item(ACTOR, paused.run_id, "interaction", request_id)
            assert item.state == "abandoned"
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        with pytest.raises(InteractionConflict):
            await runtime.respond(
                paused.run_id,
                request_id=request_id,
                response=ApprovalDecision(approved=True),
                context=ACTOR,
            )
        with pytest.raises(Conflict):
            await runtime.recover(paused.run_id, context=ACTOR)
        failed = await runtime.wait(paused.run_id, context=ACTOR)
        assert isinstance(failed, Failed) and failed.error.code == "data_abandoned"
    assert len(model.calls) == 1 and service.calls == []


@pytest.mark.asyncio
async def test_cancellation_after_actual_commit_recovers_same_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path, operations=("unknown",))
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_abandonment(ALICE, run_id, reason="lost process")
        original = maintenance._storage._raw
        committed, release = asyncio.Event(), asyncio.Event()

        async def stop(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            result = await original(sql, parameters)
            if sql == "COMMIT":
                committed.set()
                await release.wait()
            return result

        with monkeypatch.context() as patch:
            patch.setattr(maintenance._storage, "_raw", stop)
            task = asyncio.create_task(maintenance.abandon_run(plan))
            try:
                await asyncio.wait_for(committed.wait(), 5)
                task.cancel()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
    before = contents(path)
    async with SQLiteMaintenance(path) as maintenance:
        assert (await maintenance.abandon_run(plan)).state_version == plan.state_version + 1
    assert contents(path) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["before", "after"])
async def test_actual_process_kill_at_abandonment_commit(tmp_path: Path, boundary: str) -> None:
    path, marker = tmp_path / "state.db", tmp_path / "marker"
    run_id = await unfinished(path, operations=("unknown", "prepared"))
    process = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            str(Path(__file__).with_name("abandonment_process_fixture.py")),
            str(path),
            run_id,
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
            async with SQLiteStorage(path):
                pass
    finally:
        if process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.wait, timeout=5)
        assert process.stderr is not None
        process.stderr.close()
    deadline = asyncio.get_running_loop().time() + 5
    while True:
        try:
            async with SQLiteStorage(path) as storage:
                current = await storage.get_run(ALICE, run_id)
                assert current.state.status == ("running" if boundary == "before" else "failed")
            break
        except StorageInUse:
            # Only allow bounded Windows lock cleanup after the child was reaped;
            # the live-child ownership assertion above must still fail immediately.
            if os.name != "nt" or asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.01)
    async with SQLiteMaintenance(path) as maintenance:
        if boundary == "before":
            with pytest.raises(RecordNotFound):
                await maintenance.get_abandonment(ALICE, run_id)
        else:
            fact = await maintenance.get_abandonment(ALICE, run_id)
            assert fact.unknown_operations[0].status == "unknown"
            assert (await maintenance.preview_session(SCOPE)).blockers == []


@pytest.mark.asyncio
async def test_real_cli_preview_apply_and_fact_lookup(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    run_id = await unfinished(path, operations=("unknown",))
    prefix = [sys.executable, "-m", "drov.storage.maintenance"]
    common = ["--database", str(path)]
    owner = ["--tenant", "tenant", "--user", "alice", "--run", run_id]

    async def call(args: list[str]) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(
            subprocess.run, [*prefix, *args, *common], capture_output=True, text=True, timeout=10
        )

    preview = await call(["preview-abandonment", *owner, "--reason", "lost source process"])
    assert preview.returncode == 0, preview.stderr
    saved = tmp_path / "plan.json"
    saved.write_text(preview.stdout)
    result = await call(["abandon-run", "--plan-file", str(saved)])
    assert result.returncode == 0, result.stderr
    fact = await call(["get-abandonment", *owner])
    assert fact.returncode == 0 and json.loads(fact.stdout) == json.loads(result.stdout)
    repeat = await call(["abandon-run", "--plan-file", str(saved)])
    assert repeat.returncode == 0 and json.loads(repeat.stdout) == json.loads(result.stdout)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX private artifact layer")
async def test_unknown_stage_waits_for_explicit_logical_cleanup_before_reclamation(
    tmp_path: Path,
) -> None:
    from tests.test_reclamation import OWNER, admin, orphan

    path = tmp_path / "state.db"
    run_id = await unfinished(path, operations=("unknown",))
    stage = await orphan(tmp_path, run_id=run_id)
    async with admin(tmp_path) as maintenance:
        assert (await maintenance.preview_reclamation(OWNER)).stages == 0
        await maintenance.abandon_run(
            await maintenance.preview_abandonment(ALICE, run_id, reason="cannot continue")
        )
        assert (await maintenance.preview_reclamation(OWNER)).stages == 0
        await maintenance.delete_session(await maintenance.preview_session(SCOPE))
        assert (
            await maintenance.reclaim(await maintenance.preview_reclamation(OWNER))
        ).bytes_reclaimed == 7
        fact = await maintenance.get_abandonment(ALICE, run_id)
        assert fact.unknown_operations[0].status == "unknown"
    assert not (tmp_path / "files" / stage).exists()
