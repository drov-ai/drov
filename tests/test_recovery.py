"""Explicit recovery reuses durable model inputs, plans and actual external results."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from drov import Agent, ApprovalDecision, MemoryStorage, RunContext, Runtime, SQLiteStorage
from drov.errors import CapacityExceeded, Conflict, PersistenceUnavailable
from drov.models import ScriptedModel
from drov.storage.records import SessionScope, StartRequest
from drov.tools.operations import OperationJournal
from drov.types import ApprovalRequest, ReconciliationRequest, RunSnapshot, Suspended
from examples.item_service_fixture import ItemService
from tests.test_reconciliation import ALICE, approval, build, final, plan


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[ItemService]:
    value = ItemService(tmp_path)
    try:
        await value.open()
        yield value
    finally:
        await value.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["dispatched", "succeeded"])
async def test_restart_from_tool_boundary_without_blind_write(
    tmp_path: Path, service: ItemService, boundary: str
) -> None:
    path = tmp_path / "recover.db"
    agent = build(ScriptedModel([plan(), final()]), service)
    original = OperationJournal.commit
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        first = await approval(runtime, agent)
        assert first.pending is not None

        async def commit(journal: OperationJournal, operation: Any, event: Any) -> None:
            await original(journal, operation, event)
            if event.type == "tool." + boundary:
                raise PersistenceUnavailable("injected after durable boundary")

        with patch.object(OperationJournal, "commit", commit):
            await runtime.respond(
                first.run_id,
                request_id=first.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
            with pytest.raises(PersistenceUnavailable):
                await runtime.wait(first.run_id, context=ALICE)
    assert (await service.stats())["writes"] == (1 if boundary == "succeeded" else 0)
    model = ScriptedModel([final()])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        snapshot = (
            await runtime.inspect(first.run_id, context=ALICE, include_content=True)
        ).snapshot
        assert snapshot.status == "suspended"
        if boundary == "succeeded":
            outcome = await runtime.recover(first.run_id, context=ALICE)
            assert outcome.status == "completed" and len(model.calls) == 1
            assert (await service.stats())["writes"] == 1
        else:
            assert snapshot.reason == "reconciliation" and isinstance(
                snapshot.pending, ReconciliationRequest
            )
            recovered = await runtime.recover(first.run_id, context=ALICE)
            assert isinstance(recovered, Suspended) and recovered.pending is not None
            assert recovered.pending.request_id == snapshot.pending.request_id
            decision = await runtime.verify(
                first.run_id, request_id=snapshot.pending.request_id, context=ALICE
            )
            assert decision.resolution == "not_applied"
            second = await runtime.resume(
                first.run_id,
                request_id=snapshot.pending.request_id,
                response=decision,
                context=ALICE,
            )
            assert isinstance(second, Suspended) and isinstance(second.pending, ApprovalRequest)
            assert second.pending.request_id != first.pending.request_id
            await runtime.resume(
                first.run_id,
                request_id=second.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
            assert (await service.stats())["writes"] == 1
        checkpoint = await runtime.storage.get_checkpoint(ALICE, first.run_id)
        assert checkpoint.data["tool_dispatch_count"] == (1 if boundary == "succeeded" else 2)


@pytest.mark.asyncio
async def test_committed_final_response_recovered_without_another_model(
    tmp_path: Path, service: ItemService
) -> None:
    path = tmp_path / "output.db"
    agent = build(ScriptedModel([final()]), service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        with patch.object(
            runtime._runs, "finish_response", side_effect=PersistenceUnavailable("output gap")
        ):
            ref = await runtime.start(agent, "one", session_id="s", context=ALICE)
            with pytest.raises(PersistenceUnavailable):
                await runtime.wait(ref.run_id, context=ALICE)
    model = ScriptedModel([])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        outcome = await runtime.recover(ref.run_id, context=ALICE)
        assert outcome.status == "completed" and outcome.output == "actual status confirmed"  # type: ignore[union-attr]
        assert not model.calls
        assert (await runtime.storage.get_checkpoint(ALICE, ref.run_id)).data[
            "model_step_count"
        ] == 1
        with pytest.raises(Conflict):
            await runtime.recover(ref.run_id, context=ALICE)
    assert (await service.stats())["writes"] == 0


@pytest.mark.asyncio
async def test_interrupted_model_reuses_original_input_and_count(
    tmp_path: Path, service: ItemService
) -> None:
    path = tmp_path / "model.db"
    model = ScriptedModel([plan()])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        original = runtime._runs.model_call

        async def interrupted(*args: Any, **kwargs: Any) -> Any:
            await original(*args, **kwargs)
            raise PersistenceUnavailable("complete response not committed")

        with patch.object(runtime._runs, "model_call", interrupted):
            ref = await runtime.start(agent, "one", session_id="s", context=ALICE)
            with pytest.raises(PersistenceUnavailable):
                await runtime.wait(ref.run_id, context=ALICE)
        saved = model.calls[0]
    assert (await service.stats())["writes"] == 0
    retried = ScriptedModel([plan(), final()])
    agent = build(retried, service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        result = await runtime.recover(ref.run_id, context=ALICE)
        assert isinstance(result, Suspended) and isinstance(result.pending, ApprovalRequest)
        assert retried.calls[0] == saved
        checkpoint = await runtime.storage.get_checkpoint(ALICE, ref.run_id)
        assert (
            checkpoint.data["model_step_count"] == 1 and checkpoint.data["model_attempt_count"] == 2
        )
        await runtime.resume(
            ref.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        checkpoint = await runtime.storage.get_checkpoint(ALICE, ref.run_id)
        assert (
            checkpoint.data["model_step_count"] == 2 and checkpoint.data["model_attempt_count"] == 3
        )
        assert (await service.stats())["writes"] == 1


@pytest.mark.asyncio
async def test_missing_revision_capacity_and_waiting_request_do_not_advance(
    tmp_path: Path, service: ItemService
) -> None:
    path = tmp_path / "config.db"
    agent = build(ScriptedModel([final()]), service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        with (
            patch.object(runtime, "_spawn", side_effect=RuntimeError("initial handoff")),
            pytest.raises(RuntimeError),
        ):
            await runtime.start(agent, "one", session_id="s", context=ALICE)
        async with runtime.storage.transaction() as tx:
            runs = await tx.runs(SessionScope(**ALICE.model_dump(), session_id="s"))
        run_id = runs[0].state.run_id
    async with Runtime(agents=[], storage=SQLiteStorage(path)) as runtime:
        with pytest.raises(Conflict):
            await runtime.recover(run_id, context=ALICE)
        assert (await runtime.inspect(run_id, context=ALICE)).snapshot.reason == "recovery"
    agent = build(ScriptedModel([plan(), final()]), service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        with (
            patch.object(runtime, "_reserve", side_effect=CapacityExceeded("full")),
            pytest.raises(CapacityExceeded),
        ):
            await runtime.recover(run_id, context=ALICE, wait=False)
        result = await runtime.recover(run_id, context=ALICE)
        assert isinstance(result, Suspended) and isinstance(result.pending, ApprovalRequest)
        pending = await runtime.recover(run_id, context=ALICE, wait=False)
        assert isinstance(pending, RunSnapshot) and pending.pending is not None
        assert (
            pending.pending.request_id == result.pending.request_id and not pending.content_visible
        )
        assert (await service.stats())["writes"] == 0


@pytest.mark.asyncio
async def test_recovery_budget_never_resets(tmp_path: Path, service: ItemService) -> None:
    from drov import Limits

    path = tmp_path / "budget.db"
    model = ScriptedModel([final()])
    agent = build(model, service, limits=Limits(run_active_seconds=5, model_timeout_seconds=5))
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        with patch.object(
            runtime._runs, "model_call", side_effect=PersistenceUnavailable("interrupted")
        ):
            ref = await runtime.start(agent, "one", session_id="s", context=ALICE)
            with pytest.raises(PersistenceUnavailable):
                await runtime.wait(ref.run_id, context=ALICE)
    agent = build(
        ScriptedModel([]),
        service,
        limits=Limits(run_active_seconds=5, model_timeout_seconds=5),
    )
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        outcome = await runtime.recover(ref.run_id, context=ALICE)
        assert outcome.status == "failed" and outcome.error.code == "limit_exceeded"  # type: ignore[union-attr]
        assert (await runtime.storage.get_checkpoint(ALICE, ref.run_id)).data[
            "model_attempt_count"
        ] == 1
    assert (await service.stats())["writes"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_startup_scan_pages_all_owners_without_executing(
    tmp_path: Path, backend: str
) -> None:
    storage = MemoryStorage() if backend == "memory" else SQLiteStorage(tmp_path / "scan.db")
    model = ScriptedModel([])
    agent: Agent[str] = Agent(name="scan", revision="1", model=model)
    from drov.runtime.registration import fingerprint

    identity = fingerprint(agent)
    await storage.open()
    records = []
    try:
        for i in range(104):
            actor = RunContext(tenant_id=f"tenant-{i % 3}", user_id=f"user-{i % 2}")
            ref = await storage.start(
                StartRequest(
                    scope=SessionScope(**actor.model_dump(), session_id=f"s-{i}"),
                    agent_name=identity.name,
                    revision=identity.revision,
                    config_hash=identity.config_hash,
                    input="one",
                )
            )
            records.append((actor, ref.run_id))
        async with Runtime(agents=[agent], storage=storage) as runtime:
            for actor, run_id in records:
                snapshot = (await runtime.inspect(run_id, context=actor)).snapshot
                assert snapshot.status == "suspended" and snapshot.reason == "recovery"
            assert not model.calls and runtime.active_slots == 0
    finally:
        await storage.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["engine", "response"])
@pytest.mark.parametrize("version", [True, 2])
async def test_incompatible_saved_versions_do_not_guess(
    tmp_path: Path,
    service: ItemService,
    field: str,
    version: Any,
) -> None:
    path = tmp_path / "version.db"
    agent = build(ScriptedModel([final()]), service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        with patch.object(
            runtime._runs, "finish_response", side_effect=PersistenceUnavailable("boundary")
        ):
            ref = await runtime.start(agent, "one", session_id="s", context=ALICE)
            with pytest.raises(PersistenceUnavailable):
                await runtime.wait(ref.run_id, context=ALICE)
        async with runtime.storage.transaction() as tx:
            if field == "engine":
                record = await tx.get_run(ALICE, ref.run_id)
                checkpoint = await tx.get_checkpoint(ALICE, ref.run_id)
                new_version = record.state.state_version + 1
                from drov.storage.records import EventDraft

                await tx.save_step(
                    ALICE,
                    state=record.state.model_copy(update={"state_version": new_version}),
                    expected_version=new_version - 1,
                    checkpoint=checkpoint.model_copy(
                        update={
                            "state_version": new_version,
                            "data": checkpoint.data | {"engine_schema": version},
                        }
                    ),
                    events=[EventDraft(type="run.running")],
                )
            else:
                responses = await tx.items(ALICE, ref.run_id, "model_response")
                item = responses[-1]
                await tx.put_item(
                    ALICE,
                    item.model_copy(
                        update={
                            "version": item.version + 1,
                            "data": item.data | {"schema_version": version},
                        }
                    ),
                    expected_version=item.version,
                )
    model = ScriptedModel([])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        with pytest.raises(Conflict):
            await runtime.recover(ref.run_id, context=ALICE)
        assert (await runtime.inspect(ref.run_id, context=ALICE)).snapshot.reason == "recovery"
        assert not model.calls and (await service.stats())["writes"] == 0
