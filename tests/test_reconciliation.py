"""T-09 external effect/verify/recovery evidence with independent counted service."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from drov import (
    Agent,
    ApprovalDecision,
    Limits,
    MemoryStorage,
    ReconciliationDecision,
    RunContext,
    Runtime,
    SQLiteStorage,
    ToolContext,
    tool,
)
from drov.auth import Action, OwnerAuthorizer, Resource
from drov.errors import (
    CapacityExceeded,
    InteractionConflict,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
    ShutdownIncomplete,
    StorageInUse,
    TransientToolError,
)
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse, ToolCall
from drov.storage.base import Storage
from drov.storage.records import EventDraft
from drov.tools.operations import OperationJournal
from drov.types import ApprovalRequest, ReconciliationRequest, Suspended
from examples.item_service_fixture import ItemService
from tests.fixtures import close_released_runtime

ALICE = RunContext(tenant_id="test", user_id="alice")
BOB = RunContext(tenant_id="test", user_id="bob")


@pytest.fixture(params=["memory", "sqlite"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Storage:
    return MemoryStorage() if request.param == "memory" else SQLiteStorage(tmp_path / "runs.db")


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[ItemService]:
    value = ItemService(tmp_path)
    try:
        await value.open()
        yield value
    finally:
        await value.aclose()


def plan() -> ModelResponse:
    return ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[
            ToolCall(
                call_id="write-1",
                name="update_item",
                arguments={"item_id": "item-01", "status": "processing"},
            )
        ],
    )


def final() -> ModelResponse:
    return ModelResponse(message="actual status confirmed", finish_reason="completed")


def build(model: ScriptedModel, service: ItemService, **kwargs: Any) -> Agent[str]:
    async def verify(ctx: ToolContext) -> ReconciliationDecision:
        result = await service.query(ctx.operation_id, ctx.tenant_id, ctx.user_id)
        return ReconciliationDecision.model_validate(result)

    @tool(name="update_item", effect="write", approval="required", verifier=verify)
    async def update(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
        result = await service.apply(ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status)
        if service.mode == "transient_after_write":
            raise TransientToolError("Write completed before an application error")
        return result

    return Agent(name="operations", revision="1", model=model, tools=[update], **kwargs)


async def approval(runtime: Runtime, agent: Agent[Any]) -> Suspended:
    result = await runtime.run(agent, "update synthetic item", session_id="s", context=ALICE)
    assert isinstance(result, Suspended) and isinstance(result.pending, ApprovalRequest)
    return result


async def unknown(runtime: Runtime, agent: Agent[Any]) -> Suspended:
    first = await approval(runtime, agent)
    assert first.pending is not None
    result = await runtime.resume(
        first.run_id,
        request_id=first.pending.request_id,
        response=ApprovalDecision(approved=True),
        context=ALICE,
    )
    assert isinstance(result, Suspended) and isinstance(result.pending, ReconciliationRequest)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_external_approval_independent_write_count(
    storage: Storage, service: ItemService, approved: bool
) -> None:
    agent = build(ScriptedModel([plan(), final()]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        first = await approval(runtime, agent)
        assert first.pending is not None and (await service.stats())["writes"] == 0
        outcomes = await asyncio.gather(
            *(
                runtime.resume(
                    first.run_id,
                    request_id=first.pending.request_id,
                    response=ApprovalDecision(approved=approved),
                    context=ALICE,
                )
                for _ in range(20)
            )
        )
        assert all(outcome.status == "completed" for outcome in outcomes)
        assert (await service.stats())["writes"] == int(approved)
        assert (await service.stats())["attempts"] == int(approved)
        checkpoint = await storage.get_checkpoint(ALICE, first.run_id)
        assert checkpoint.data.get("tool_dispatch_count", 0) == int(approved)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["reply_lost", "invalid_result", "transient_after_write"])
async def test_write_unknown_verified_without_repeating(
    storage: Storage, service: ItemService, mode: str
) -> None:
    service.mode = mode
    model = ScriptedModel([plan(), final()])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await unknown(runtime, agent)
        assert result.pending is not None
        assert (await service.stats())["writes"] == 1 and len(model.calls) == 1
        before = result.pending.request_id
        suggestion = await runtime.verify(result.run_id, request_id=before, context=ALICE)
        assert suggestion.resolution == "applied" and suggestion.result == {
            "item_id": "item-01",
            "status": "processing",
        }
        assert (
            await runtime.inspect(result.run_id, context=ALICE)
        ).snapshot.pending.request_id == before  # type: ignore[union-attr]
        outcome = await runtime.resume(
            result.run_id, request_id=before, response=suggestion, context=ALICE
        )
        assert outcome.status == "completed" and (await service.stats())["writes"] == 1
        assert len(model.calls) == 2
        operation = await OperationJournal(storage, ALICE).get(
            result.run_id, result.pending.operation_id
        )
        assert (
            operation is not None
            and operation.status == "succeeded"
            and operation.effect_resolution == "applied"
        )
        metadata = await runtime.inspect(result.run_id, context=ALICE)
        assert "Independent service" not in metadata.model_dump_json()
        events = await storage.events(ALICE, result.run_id)
        assert len([event for event in events if event.type == "tool.dispatched"]) == 1
        assert [e.type for e in events if e.type.startswith("verification.")] == [
            "verification.started",
            "verification.completed",
        ]


@pytest.mark.asyncio
async def test_unresolved_refresh_and_expired_reminder_accepts_evidence(
    storage: Storage, service: ItemService
) -> None:
    service.mode = "reply_lost"
    agent = build(ScriptedModel([plan(), final()]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        first = await unknown(runtime, agent)
        assert first.pending is not None
        response = ReconciliationDecision(
            resolution="unresolved", evidence="Need independent evidence"
        )
        receipts = await asyncio.gather(
            *(
                runtime.respond(
                    first.run_id,
                    request_id=first.pending.request_id,
                    response=response,
                    context=ALICE,
                )
                for _ in range(20)
            )
        )
        assert all(receipt == receipts[0] for receipt in receipts)
        second = await runtime.wait(first.run_id, context=ALICE)
        assert isinstance(second, Suspended) and isinstance(second.pending, ReconciliationRequest)
        assert second.pending.request_id != first.pending.request_id
        storage._clock = lambda: second.pending.expires_at  # type: ignore[union-attr]
        assert (await runtime.inspect(first.run_id, context=ALICE)).snapshot.status == "suspended"
        suggestion = await runtime.verify(
            first.run_id, request_id=second.pending.request_id, context=ALICE
        )
        assert (
            await runtime.resume(
                first.run_id,
                request_id=second.pending.request_id,
                response=suggestion,
                context=ALICE,
            )
        ).status == "completed"
        assert (await service.stats())["writes"] == 1
        assert (
            await runtime.respond(
                first.run_id, request_id=first.pending.request_id, response=response, context=ALICE
            )
            == receipts[0]
        )


@pytest.mark.asyncio
async def test_not_applied_requires_fresh_approval(storage: Storage, service: ItemService) -> None:
    service.mode = "not_applied"
    agent = build(ScriptedModel([plan(), final()]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        first = await unknown(runtime, agent)
        assert first.pending is not None
        suggestion = await runtime.verify(
            first.run_id, request_id=first.pending.request_id, context=ALICE
        )
        assert suggestion.resolution == "not_applied" and (await service.stats())["writes"] == 0
        service.mode = "normal"
        second = await runtime.resume(
            first.run_id, request_id=first.pending.request_id, response=suggestion, context=ALICE
        )
        assert isinstance(second, Suspended) and isinstance(second.pending, ApprovalRequest)
        assert second.pending.request_id != first.pending.request_id
        assert second.pending.operation_id == first.pending.operation_id
        assert (await service.stats())["attempts"] == 1
        assert (
            await runtime.resume(
                first.run_id,
                request_id=second.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        ).status == "completed"
        assert (await service.stats())["writes"] == 1 and (await service.stats())["attempts"] == 2
        assert (await storage.get_checkpoint(ALICE, first.run_id)).data["tool_dispatch_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["applied", "not_applied"])
async def test_cancel_unknown_evidence_only_never_restarts(
    storage: Storage, service: ItemService, resolution: str
) -> None:
    service.mode = "reply_lost" if resolution == "applied" else "not_applied"
    model = ScriptedModel([plan()])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await unknown(runtime, agent)
        assert result.pending is not None
        await runtime.cancel(result.run_id, context=ALICE)
        pending = await runtime.wait(result.run_id, context=ALICE)
        assert isinstance(pending, Suspended) and pending.pending == result.pending
        decision = await runtime.verify(
            result.run_id, request_id=result.pending.request_id, context=ALICE
        )
        assert decision.resolution == resolution
        evidence = ReconciliationDecision(
            resolution=resolution, evidence=decision.evidence
        )  # No output required for cancellation.
        outcome = await runtime.resume(
            result.run_id, request_id=result.pending.request_id, response=evidence, context=ALICE
        )
        assert outcome.status == "cancelled" and len(model.calls) == 1
        assert (await service.stats())["writes"] == (1 if resolution == "applied" else 0)
        assert (await storage.get_run(ALICE, result.run_id)).state.cancel_requested


@pytest.mark.asyncio
async def test_invalid_evidence_result_and_capacity_leave_pending(
    storage: Storage, service: ItemService
) -> None:
    service.mode = "reply_lost"
    agent = build(ScriptedModel([plan(), final()]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        first = await unknown(runtime, agent)
        assert first.pending is not None
        invalid = [
            ReconciliationDecision(resolution="applied", evidence="confirmed"),
            ReconciliationDecision(resolution="applied", evidence="confirmed", result=7),
            ReconciliationDecision(resolution="applied", evidence=" ", result={}),
        ]
        for response in invalid:
            with pytest.raises(ValueError):
                await runtime.respond(
                    first.run_id,
                    request_id=first.pending.request_id,
                    response=response,
                    context=ALICE,
                )
        with (
            patch.object(runtime, "_reserve", side_effect=CapacityExceeded("full")),
            pytest.raises(CapacityExceeded),
        ):
            await runtime.respond(
                first.run_id,
                request_id=first.pending.request_id,
                response=ReconciliationDecision(
                    resolution="applied",
                    evidence="verified",
                    result={"item_id": "item-01", "status": "processing"},
                ),
                context=ALICE,
            )
        assert (
            await runtime.inspect(first.run_id, context=ALICE)
        ).snapshot.pending.request_id == first.pending.request_id  # type: ignore[union-attr]
        assert (await service.stats())["writes"] == 1


@pytest.mark.asyncio
async def test_verifier_current_permissions(storage: Storage, service: ItemService) -> None:
    class Auth(OwnerAuthorizer):
        allowed = True

        async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
            return self.allowed and await super().authorize(actor, action, resource)

    auth = Auth()
    service.mode = "reply_lost"
    agent = build(ScriptedModel([plan()]), service)
    async with Runtime(agents=[agent], storage=storage, authorizer=auth) as runtime:
        result = await unknown(runtime, agent)
        assert result.pending is not None
        with pytest.raises((PermissionDenied, RecordNotFound)):
            await runtime.verify(result.run_id, request_id=result.pending.request_id, context=BOB)
        auth.allowed = False
        with pytest.raises((PermissionDenied, RecordNotFound)):
            await runtime.verify(result.run_id, request_id=result.pending.request_id, context=ALICE)
        assert (await service.stats())["writes"] == 1


@pytest.mark.asyncio
async def test_verifier_timeout_retains_actual_task_slot_and_lock(
    tmp_path: Path, service: ItemService
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    service.mode = "reply_lost"

    async def verifier(ctx: ToolContext) -> ReconciliationDecision:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return ReconciliationDecision(
            resolution="applied",
            evidence="fixture query",
            result={"item_id": "item-01", "status": "processing"},
        )

    @tool(name="update_item", effect="write", approval="required", verifier=verifier)
    async def update(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
        return await service.apply(ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status)

    agent: Agent[str] = Agent(
        name="operations", revision="1", model=ScriptedModel([plan()]), tools=[update]
    )
    path = tmp_path / "verify.db"
    runtime = Runtime(
        agents=[agent],
        storage=SQLiteStorage(path),
        limits=Limits(verify_timeout_seconds=0.5, shutdown_grace_seconds=0.03),
    )
    await runtime.open()
    try:
        result = await unknown(runtime, agent)
        assert result.pending is not None
        decision = await runtime.verify(
            result.run_id, request_id=result.pending.request_id, context=ALICE
        )
        assert (
            entered.is_set() and decision.resolution == "unresolved" and runtime.active_slots == 1
        )
        with pytest.raises(CapacityExceeded):
            await runtime.verify(result.run_id, request_id=result.pending.request_id, context=ALICE)
        with pytest.raises(ShutdownIncomplete):
            await runtime.aclose()
        other = SQLiteStorage(path)
        with pytest.raises(StorageInUse):
            await other.open()
    finally:
        release.set()
        await close_released_runtime(runtime)


@pytest.mark.asyncio
async def test_late_verifier_cannot_advance_consumed_request(
    storage: Storage, service: ItemService
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    service.mode = "reply_lost"

    async def verifier(ctx: ToolContext) -> ReconciliationDecision:
        entered.set()
        await release.wait()
        return ReconciliationDecision(
            resolution="applied",
            evidence="late query",
            result={"item_id": "item-01", "status": "processing"},
        )

    @tool(name="update_item", effect="write", approval="required", verifier=verifier)
    async def update(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
        return await service.apply(ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status)

    agent: Agent[str] = Agent(
        name="operations", revision="1", model=ScriptedModel([plan(), final()]), tools=[update]
    )
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await unknown(runtime, agent)
        assert result.pending is not None
        query = asyncio.create_task(
            runtime.verify(result.run_id, request_id=result.pending.request_id, context=ALICE)
        )
        await entered.wait()
        await runtime.resume(
            result.run_id,
            request_id=result.pending.request_id,
            response=ReconciliationDecision(
                resolution="applied",
                evidence="separate evidence",
                result={"item_id": "item-01", "status": "processing"},
            ),
            context=ALICE,
        )
        release.set()
        with pytest.raises(InteractionConflict):
            await query
        assert (await service.stats())["writes"] == 1
        async with storage.transaction() as tx:
            audits = [
                item
                for item in await tx.items(ALICE, result.run_id, "receipt")
                if item.data.get("record_type") == "verification"
            ]
        assert len(audits) == 2 and {item.state for item in audits} == {"started", "completed"}


@pytest.mark.asyncio
async def test_not_applied_cannot_repeat_while_original_call_alive(storage: Storage) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    count = 0

    @tool(name="update_item", effect="write", approval="required")
    async def update(item_id: str, status: str) -> dict[str, str]:
        nonlocal count
        count += 1
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return {"item_id": item_id, "status": status}

    agent: Agent[str] = Agent(
        name="operations",
        revision="1",
        model=ScriptedModel([plan()]),
        tools=[update],
        limits=Limits(tool_timeout_seconds=10),
    )
    runtime = Runtime(agents=[agent], storage=storage)
    await runtime.open()
    try:
        first = await approval(runtime, agent)
        assert first.pending is not None
        await runtime.respond(
            first.run_id,
            request_id=first.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        async with asyncio.timeout(5):
            await entered.wait()
        await runtime.cancel(first.run_id, context=ALICE)
        result = await runtime.wait(first.run_id, context=ALICE)
        assert isinstance(result, Suspended) and isinstance(result.pending, ReconciliationRequest)
        with pytest.raises(InteractionConflict):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ReconciliationDecision(
                    resolution="not_applied", evidence="insufficient while original call remains"
                ),
                context=ALICE,
            )
        assert count == 1 and runtime.active_slots == 1
        await runtime.cancel(result.run_id, context=ALICE)
    finally:
        release.set()
        await close_released_runtime(runtime)


@pytest.mark.asyncio
async def test_verifier_failure_is_unresolved_and_does_not_consume(
    storage: Storage, service: ItemService
) -> None:
    service.mode = "reply_lost"

    async def verifier(ctx: ToolContext) -> ReconciliationDecision:
        raise RuntimeError("private transport failure must not be shown")

    @tool(name="update_item", effect="write", approval="required", verifier=verifier)
    async def update(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
        return await service.apply(ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status)

    agent: Agent[str] = Agent(
        name="operations", revision="1", model=ScriptedModel([plan()]), tools=[update]
    )
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await unknown(runtime, agent)
        assert result.pending is not None
        decision = await runtime.verify(
            result.run_id, request_id=result.pending.request_id, context=ALICE
        )
        assert decision.resolution == "unresolved" and "private transport" not in decision.evidence
        assert (
            await runtime.inspect(result.run_id, context=ALICE)
        ).snapshot.pending.request_id == result.pending.request_id  # type: ignore[union-attr]
        assert (await service.stats())["writes"] == 1


@pytest.mark.asyncio
async def test_no_verifier_configured_does_not_consume(
    storage: Storage, service: ItemService
) -> None:
    from drov.errors import ConfigurationError

    service.mode = "reply_lost"

    @tool(name="update_item", effect="unknown", approval="required")
    async def update(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
        return await service.apply(ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status)

    agent: Agent[str] = Agent(
        name="operations", revision="1", model=ScriptedModel([plan()]), tools=[update]
    )
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await unknown(runtime, agent)
        assert result.pending is not None
        with pytest.raises(ConfigurationError):
            await runtime.verify(result.run_id, request_id=result.pending.request_id, context=ALICE)
        assert (await service.stats())["writes"] == 1
        assert (
            await runtime.inspect(result.run_id, context=ALICE)
        ).snapshot.pending.request_id == result.pending.request_id  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_budget_exhausted_unknown_can_resolve_without_result(
    storage: Storage, service: ItemService
) -> None:
    service.mode = "reply_lost"
    agent = build(ScriptedModel([plan()]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await unknown(runtime, agent)
        assert result.pending is not None
        async with storage.transaction() as tx:
            record = await tx.get_run(ALICE, result.run_id)
            checkpoint = await tx.get_checkpoint(ALICE, result.run_id)
            version = record.state.state_version + 1
            await tx.save_step(
                ALICE,
                state=record.state.model_copy(update={"state_version": version}),
                expected_version=version - 1,
                checkpoint=checkpoint.model_copy(
                    update={
                        "state_version": version,
                        "data": checkpoint.data
                        | {"active_seconds": record.limits.run_active_seconds},
                    }
                ),
                events=[EventDraft(type="run.suspended")],
            )
        decision = await runtime.verify(
            result.run_id, request_id=result.pending.request_id, context=ALICE
        )
        assert decision.resolution == "applied"
        outcome = await runtime.resume(
            result.run_id,
            request_id=result.pending.request_id,
            response=ReconciliationDecision(resolution="applied", evidence=decision.evidence),
            context=ALICE,
        )
        assert outcome.status == "failed" and outcome.error.code == "limit_exceeded"
        assert (await service.stats())["writes"] == 1


@pytest.mark.asyncio
async def test_reconciliation_commit_rollback_preserves_unknown(
    storage: Storage, service: ItemService
) -> None:
    from drov.storage.transaction import Transaction

    service.mode = "reply_lost"
    agent = build(ScriptedModel([plan(), final()]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await unknown(runtime, agent)
        assert result.pending is not None
        decision = await runtime.verify(
            result.run_id, request_id=result.pending.request_id, context=ALICE
        )
        with (
            patch.object(
                Transaction, "save_step", side_effect=PersistenceUnavailable("known rollback")
            ),
            pytest.raises(PersistenceUnavailable),
        ):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=decision,
                context=ALICE,
            )
        assert (
            await runtime.inspect(result.run_id, context=ALICE)
        ).snapshot.pending.request_id == result.pending.request_id  # type: ignore[union-attr]
        operation = await OperationJournal(storage, ALICE).get(
            result.run_id, result.pending.operation_id
        )
        assert (
            operation is not None
            and operation.status == "unknown"
            and (await service.stats())["writes"] == 1
        )
        assert (
            await runtime.resume(
                result.run_id,
                request_id=result.pending.request_id,
                response=decision,
                context=ALICE,
            )
        ).status == "completed"


def test_verifier_registration_is_explicit_async_and_fingerprinted() -> None:
    from drov.errors import ConfigurationError
    from drov.runtime.registration import fingerprint

    def sync(ctx: ToolContext) -> ReconciliationDecision:
        return ReconciliationDecision(resolution="unresolved", evidence="test")

    with pytest.raises(ConfigurationError):
        tool(name="test", verifier=sync)(lambda: "one")  # type: ignore[arg-type]

    async def verifier(ctx: ToolContext) -> ReconciliationDecision:
        return ReconciliationDecision(resolution="unresolved", evidence="test")

    def action() -> str:
        return "one"

    first = tool(name="test", effect="write", verifier=verifier, verifier_revision="1")(action)
    second = tool(name="test", effect="write", verifier=verifier, verifier_revision="2")(action)
    one: Agent[str] = Agent(name="test", revision="1", model=ScriptedModel([]), tools=[first])
    two: Agent[str] = Agent(name="test", revision="1", model=ScriptedModel([]), tools=[second])
    assert fingerprint(one).config_hash != fingerprint(two).config_hash


def test_fixture_server_startup_never_resolves_loopback_dns() -> None:
    from http.server import BaseHTTPRequestHandler

    from examples.item_service_fixture import LocalHTTPServer

    with patch("socket.getfqdn", side_effect=AssertionError("Fixture must not resolve DNS")):
        with LocalHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
            assert server.server_port > 0 and server.server_name == "localhost"
