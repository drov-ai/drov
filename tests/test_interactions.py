"""T-09 approval/input subrange: real tools, durable receipts and no implicit replan."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from drov import (
    Agent,
    ApprovalDecision,
    HumanInput,
    InputResponse,
    Limits,
    Memory,
    MemoryStorage,
    PermissionPolicy,
    RunContext,
    Runtime,
    SQLiteStorage,
    tool,
)
from drov._json import StrictJson
from drov.auth import Action, OwnerAuthorizer, Resource
from drov.errors import (
    CapacityExceeded,
    Conflict,
    InteractionConflict,
    InteractionExpired,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
)
from drov.models import ScriptedModel
from drov.models.protocol import ModelEvent, ModelRequest, ModelResponse, ToolCall
from drov.storage.base import Storage
from drov.types import ApprovalRequest, InputRequest, Suspended

ALICE = RunContext(tenant_id="test", user_id="alice")
BOB = RunContext(tenant_id="test", user_id="bob")
OTHER = RunContext(tenant_id="other", user_id="alice")


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(params=["memory", "sqlite"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Storage:
    return MemoryStorage() if request.param == "memory" else SQLiteStorage(tmp_path / "run.db")


def plan(*calls: tuple[str, dict[str, Any]]) -> ModelResponse:
    return ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[
            ToolCall(call_id=f"call-{i}", name=name, arguments=args)
            for i, (name, args) in enumerate(calls)
        ],
    )


def final() -> ModelResponse:
    return ModelResponse(message="done", finish_reason="completed")


class Service:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []


def build(model: ScriptedModel, service: Service, **kwargs: Any) -> Agent[str]:
    from drov import ToolContext

    @tool(name="read_item", effect="read", approval="required")
    def read_item(value: int, context: ToolContext) -> str:
        service.calls.append((context.user_id, context.operation_id, value))
        return "item-" + str(value)

    return Agent(name="operations", revision="1", model=model, tools=[read_item], **kwargs)


async def pause(runtime: Runtime, agent: Agent[Any]) -> Suspended:
    result = await runtime.run(agent, "synthetic", session_id="s", context=ALICE)
    assert isinstance(result, Suspended) and result.pending is not None
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_saved_plan_approval_and_denial(storage: Storage, approved: bool) -> None:
    service = Service()
    model = ScriptedModel([plan(("read_item", {"value": 7})), final()])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert isinstance(result.pending, ApprovalRequest)
        assert result.pending.arguments_summary == {"value": 7} and not service.calls
        assert runtime.active_slots == 0
        outcome = await runtime.resume(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=approved),
            context=ALICE,
        )
        assert outcome.status == "completed"
        assert len(model.calls) == 2 and len(service.calls) == int(approved)
        message = model.calls[-1].messages[-1]
        assert isinstance(message.content, dict)
        assert message.call_id == "call-0"
        if approved:
            assert message.content["result"] == "item-7"
            assert service.calls[0] == ("alice", result.pending.operation_id, 7)
        else:
            assert message.content["error_code"] == "approval_denied"
        async with storage.transaction() as tx:
            receipts = await tx.items(ALICE, result.run_id, "receipt")
            assert len(receipts) == 1
            events = await tx.events(ALICE, result.run_id)
        consumed = next(e for e in events if e.type == "interaction.responded")
        saved_receipt = receipts[0].data["receipt"]
        assert isinstance(saved_receipt, dict)
        assert saved_receipt["state_version"] == consumed.state_version


@pytest.mark.asyncio
async def test_twenty_duplicate_responses_dispatch_once(storage: Storage) -> None:
    service = Service()
    model = ScriptedModel([plan(("read_item", {"value": 1})), final()])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        receipts = await asyncio.gather(
            *(
                runtime.respond(
                    result.run_id,
                    request_id=result.pending.request_id,
                    response=ApprovalDecision(approved=True),
                    context=ALICE,
                )
                for _ in range(20)
            )
        )
        assert all(receipt == receipts[0] for receipt in receipts)
        assert (await runtime.wait(result.run_id, context=ALICE)).status == "completed"
        assert len(service.calls) == 1 and len(model.calls) == 2
        with pytest.raises(InteractionConflict):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=False),
                context=ALICE,
            )
        assert (
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
            == receipts[0]
        )


@pytest.mark.asyncio
async def test_multiple_calls_keep_prior_results_and_use_new_requests(storage: Storage) -> None:
    service = Service()
    model = ScriptedModel([plan(("read_item", {"value": 1}), ("read_item", {"value": 2})), final()])
    agent = build(model, service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        first = await pause(runtime, agent)
        assert first.pending is not None
        second = await runtime.resume(
            first.run_id,
            request_id=first.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        assert isinstance(second, Suspended) and second.pending is not None
        assert second.pending.request_id != first.pending.request_id
        assert len(model.calls) == 1 and len(service.calls) == 1
        repeat = await runtime.resume(
            first.run_id,
            request_id=first.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        assert repeat == second and len(service.calls) == 1
        outcome = await runtime.resume(
            first.run_id,
            request_id=second.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        assert outcome.status == "completed" and [call[-1] for call in service.calls] == [1, 2]
        assert len(model.calls) == 2
        checkpoint = await storage.get_checkpoint(ALICE, first.run_id)
        assert checkpoint.data["tool_dispatch_count"] == 2
        assert checkpoint.data["model_step_count"] == 2
        assert checkpoint.data["model_attempt_count"] == 2


@pytest.mark.asyncio
async def test_wrong_request_kind_owner_tenant_and_revision_do_not_consume(
    storage: Storage,
) -> None:
    service = Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1})), final()]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        for caller in (BOB, OTHER):
            with pytest.raises((PermissionDenied, RecordNotFound)):
                await runtime.respond(
                    result.run_id,
                    request_id=result.pending.request_id,
                    response=ApprovalDecision(approved=True),
                    context=caller,
                )
        with pytest.raises(InteractionConflict):
            await runtime.respond(
                result.run_id,
                request_id="wrong",
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        with pytest.raises(InteractionConflict):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=InputResponse(value={}),
                context=ALICE,
            )
        agent.instructions = "changed"
        with pytest.raises(Conflict):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        agent.instructions = ""
        state = (await storage.get_run(ALICE, result.run_id)).state
        assert state.state_version == result.state_version and state.pending == result.pending
        assert not service.calls


class Reviewer(OwnerAuthorizer):
    revoked = False
    body = False
    executor = True
    actors: list[RunContext]

    def __init__(self) -> None:
        self.actors = []

    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
        if action == "execute_tool":
            self.actors.append(actor)
            return self.executor and actor == resource.owner
        if actor == BOB:
            return not self.revoked and (
                action == "respond" or (self.body and action in ("read", "view_content"))
            )
        return await super().authorize(actor, action, resource)


@pytest.mark.asyncio
async def test_reviewer_has_response_only_and_executor_stays_owner(storage: Storage) -> None:
    auth, service = Reviewer(), Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1})), final()]), service)
    async with Runtime(agents=[agent], storage=storage, authorizer=auth) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        with pytest.raises((PermissionDenied, RecordNotFound)):
            await runtime.resume(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=BOB,
            )
        assert (await storage.get_run(ALICE, result.run_id)).state.pending == result.pending
        receipt = await runtime.respond(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=BOB,
        )
        assert (await runtime.wait(result.run_id, context=ALICE)).status == "completed"
        assert service.calls[0][0] == "alice" and set(auth.actors) == {ALICE}
        auth.revoked = True
        with pytest.raises(PermissionDenied):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=BOB,
            )
        auth.revoked, auth.body = False, True
        assert (
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=BOB,
            )
            == receipt
        )
        inspection = await runtime.inspect(result.run_id, context=BOB, include_content=True)
        assert inspection.snapshot.status == "completed"


@pytest.mark.asyncio
async def test_revoke_executor_after_response_never_dispatches(storage: Storage) -> None:
    auth, service = Reviewer(), Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1})), final()]), service)
    async with Runtime(agents=[agent], storage=storage, authorizer=auth) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        auth.executor = False
        await runtime.respond(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=BOB,
        )
        assert (await runtime.wait(result.run_id, context=ALICE)).status == "completed"
        assert not service.calls
        assert (await storage.get_run(ALICE, result.run_id)).state.operations[
            0
        ].status == "not_executed"


class GateModel(ScriptedModel):
    def __init__(self) -> None:
        super().__init__([final()])
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.entered.set()
        await self.release.wait()
        async for event in super().generate(request):
            yield event


@pytest.mark.asyncio
async def test_capacity_failure_does_not_consume_response(storage: Storage) -> None:
    service = Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1})), final()]), service)
    busy_model = GateModel()
    busy: Agent[str] = Agent(name="busy", revision="1", model=busy_model)
    async with Runtime(
        agents=[agent, busy], storage=storage, limits=Limits(active_slots=1)
    ) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        ref = await runtime.start(busy, "busy", session_id="busy", context=ALICE)
        await busy_model.entered.wait()
        with pytest.raises(CapacityExceeded):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        assert (await storage.get_run(ALICE, result.run_id)).state.pending == result.pending
        async with storage.transaction() as tx:
            assert not await tx.items(ALICE, result.run_id, "receipt")
        busy_model.release.set()
        await runtime.wait(ref.run_id, context=ALICE)
        assert (
            await runtime.resume(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        ).status == "completed"
        assert len(service.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("read_expiry", [True, False])
async def test_pending_expiry_finishes_without_dispatch(tmp_path: Path, read_expiry: bool) -> None:
    clock, service = Clock(), Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1}))]), service)
    storage = SQLiteStorage(tmp_path / "expiry.db", clock=clock)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        clock.now = result.pending.expires_at
        if read_expiry:
            assert (await runtime.inspect(result.run_id, context=ALICE)).snapshot.status == "failed"
            with pytest.raises(InteractionConflict):
                await runtime.respond(
                    result.run_id,
                    request_id=result.pending.request_id,
                    response=ApprovalDecision(approved=True),
                    context=ALICE,
                )
        else:
            with pytest.raises(InteractionExpired):
                await runtime.respond(
                    result.run_id,
                    request_id=result.pending.request_id,
                    response=ApprovalDecision(approved=True),
                    context=ALICE,
                )
        outcome = await runtime.wait(result.run_id, context=ALICE)
        assert outcome.status == "failed" and outcome.error.code == "interaction_expired"
        assert not service.calls
        async with storage.transaction() as tx:
            assert not await tx.items(ALICE, result.run_id, "receipt")


@pytest.mark.asyncio
async def test_consumed_response_retries_after_expiry(tmp_path: Path) -> None:
    clock, service = Clock(), Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1})), final()]), service)
    storage = SQLiteStorage(tmp_path / "receipt.db", clock=clock)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        receipt = await runtime.respond(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        await runtime.wait(result.run_id, context=ALICE)
        clock.now = result.pending.expires_at + timedelta(seconds=1)
        assert (
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
            == receipt
        )
        assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_cancel_pending_prevents_response_and_preserves_prior_memory(
    storage: Storage,
) -> None:
    model = ScriptedModel(
        [
            plan(
                ("memory.remember", {"key": "k", "content": "v", "expected_version": 0}),
                ("memory.recall", {"key": "k", "query": None, "limit": 5}),
            )
        ]
    )
    agent: Agent[str] = Agent(name="operations", revision="1", model=model, memory=Memory())
    async with Runtime(
        agents=[agent],
        storage=storage,
        policy=PermissionPolicy(allow={"memory.remember"}, ask={"memory.recall"}),
    ) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        await runtime.cancel(result.run_id, context=ALICE)
        assert (await runtime.wait(result.run_id, context=ALICE)).status == "cancelled"
        with pytest.raises(InteractionConflict):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        from drov.storage.records import MemoryScope

        async with storage.transaction() as tx:
            memory = await tx.get_memory(
                MemoryScope(**ALICE.model_dump(), agent_name="operations"), "k"
            )
            assert memory is not None and memory.content == "v" and memory.version == 1
        assert len(model.calls) == 1


@dataclass
class Choice:
    item_id: str


@pytest.mark.asyncio
async def test_human_input_validation_then_approval_without_replan(storage: Storage) -> None:
    service = Service()
    model = ScriptedModel(
        [
            plan(("ask_item", {"prompt": "Choose an item"})),
            plan(("read_item", {"value": 7})),
            final(),
        ]
    )
    agent = build(model, service)
    input_tool = HumanInput(name="ask_item", input_type=Choice)
    agent.tools = (input_tool, *agent.tools)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert isinstance(result.pending, InputRequest)
        for value in ({}, {"item_id": 7}, {"item_id": "one", "user_id": "bob"}):
            with pytest.raises(ValueError):
                await runtime.respond(
                    result.run_id,
                    request_id=result.pending.request_id,
                    response=InputResponse(value=cast(StrictJson, value)),
                    context=ALICE,
                )
        assert (await storage.get_run(ALICE, result.run_id)).state.pending == result.pending
        second = await runtime.resume(
            result.run_id,
            request_id=result.pending.request_id,
            response=InputResponse(value={"item_id": "one"}),
            context=ALICE,
        )
        assert isinstance(second, Suspended) and isinstance(second.pending, ApprovalRequest)
        assert len(model.calls) == 2 and not service.calls
        content = model.calls[1].messages[-1].content
        assert isinstance(content, dict) and content["result"] == {"item_id": "one"}
        assert (
            await runtime.resume(
                result.run_id,
                request_id=second.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        ).status == "completed"
        assert len(service.calls) == 1 and len(model.calls) == 3
        assert (await storage.get_checkpoint(ALICE, result.run_id)).data["tool_dispatch_count"] == 2


@pytest.mark.asyncio
async def test_memory_ask_mutates_once_after_approval(storage: Storage) -> None:
    from drov.storage.records import MemoryScope

    model = ScriptedModel(
        [plan(("memory.remember", {"key": "k", "content": "v", "expected_version": 0})), final()]
    )
    agent: Agent[str] = Agent(name="operations", revision="1", model=model, memory=Memory())
    async with Runtime(
        agents=[agent], storage=storage, policy=PermissionPolicy(ask={"memory.remember"})
    ) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        receipts = await asyncio.gather(
            *(
                runtime.respond(
                    result.run_id,
                    request_id=result.pending.request_id,
                    response=ApprovalDecision(approved=True),
                    context=ALICE,
                )
                for _ in range(20)
            )
        )
        assert all(receipt == receipts[0] for receipt in receipts)
        assert (await runtime.wait(result.run_id, context=ALICE)).status == "completed"
        async with storage.transaction() as tx:
            memory = await tx.get_memory(
                MemoryScope(**ALICE.model_dump(), agent_name="operations"), "k"
            )
            assert memory is not None and memory.version == 1
            events = await tx.events(ALICE, result.run_id)
        assert len([e for e in events if e.type == "memory.changed"]) == 1
        assert not any(e.type == "tool.dispatched" for e in events)


@pytest.mark.asyncio
async def test_metadata_does_not_expose_prompt_parameters_or_answer(storage: Storage) -> None:
    agent: Agent[str] = Agent(
        name="operations",
        revision="1",
        model=ScriptedModel([plan(("ask_item", {"prompt": "private-prompt"})), final()]),
        tools=[HumanInput(name="ask_item", input_type=Choice)],
    )
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        metadata = await runtime.inspect(result.run_id, context=ALICE)
        assert "private-prompt" not in metadata.model_dump_json()
        assert (
            "private-prompt"
            in (
                await runtime.inspect(result.run_id, context=ALICE, include_content=True)
            ).model_dump_json()
        )
        await runtime.resume(
            result.run_id,
            request_id=result.pending.request_id,
            response=InputResponse(value={"item_id": "private-answer"}),
            context=ALICE,
        )
        assert (
            "private-answer"
            not in (await runtime.inspect(result.run_id, context=ALICE)).model_dump_json()
        )


@pytest.mark.asyncio
async def test_temporary_human_input_has_no_request_id() -> None:
    model = ScriptedModel([plan(("ask_item", {"prompt": "choose"}))])
    agent: Agent[str] = Agent(model=model, tools=[HumanInput(name="ask_item", input_type=Choice)])
    result = await agent.run("one")
    assert result.status == "failed" and result.error.code == "interaction_unavailable"
    assert "request_id" not in result.model_dump_json() and len(model.calls) == 1


@pytest.mark.asyncio
async def test_missing_revision_on_reopen_keeps_request(tmp_path: Path) -> None:
    path, service = tmp_path / "reopen.db", Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1}))]), service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
    async with Runtime(agents=[], storage=SQLiteStorage(path)) as runtime:
        with pytest.raises(Conflict):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        assert (await runtime.inspect(result.run_id, context=ALICE)).snapshot.pending is not None
    agent = build(ScriptedModel([final()]), service)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        assert (
            await runtime.resume(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        ).status == "completed"
    assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_scheduling_failure_keeps_receipt_and_requires_recovery(storage: Storage) -> None:
    service = Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1}))]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        with (
            patch.object(runtime, "_spawn", side_effect=RuntimeError("handoff")),
            pytest.raises(RuntimeError),
        ):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        snapshot = (await runtime.inspect(result.run_id, context=ALICE)).snapshot
        assert (
            snapshot.status == "suspended"
            and snapshot.reason == "recovery"
            and snapshot.pending is None
        )
        receipt = await runtime.respond(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        assert receipt.request_id == result.pending.request_id and not service.calls
        assert runtime.active_slots == 0


@pytest.mark.asyncio
async def test_response_rollback_leaves_pending_and_no_receipt(storage: Storage) -> None:
    from drov.storage.transaction import Transaction

    service = Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1}))]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        with (
            patch.object(
                Transaction, "save_step", side_effect=PersistenceUnavailable("known rollback")
            ),
            pytest.raises(PersistenceUnavailable),
        ):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        assert (await storage.get_run(ALICE, result.run_id)).state.pending == result.pending
        async with storage.transaction() as tx:
            assert not await tx.items(ALICE, result.run_id, "receipt")
            assert (
                await tx.get_item(ALICE, result.run_id, "interaction", result.pending.request_id)
            ).state == "pending"
        assert runtime.active_slots == 0 and not service.calls


@pytest.mark.asyncio
async def test_current_answer_permission_revoked_keeps_input(storage: Storage) -> None:
    auth = Reviewer()
    model = ScriptedModel([plan(("ask_item", {"prompt": "choose"})), final()])
    agent: Agent[str] = Agent(
        name="operations",
        revision="1",
        model=model,
        tools=[HumanInput(name="ask_item", input_type=Choice)],
    )
    async with Runtime(agents=[agent], storage=storage, authorizer=auth) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        auth.executor = False
        with pytest.raises(PermissionDenied):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=InputResponse(value={"item_id": "one"}),
                context=ALICE,
            )
        assert (await storage.get_run(ALICE, result.run_id)).state.pending == result.pending
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_approved_grant_expired_before_dispatch(tmp_path: Path) -> None:
    clock, service = Clock(), Service()

    class Auth(OwnerAuthorizer):
        expiry: datetime | None = None

        async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
            if action == "execute_tool" and self.expiry is not None:
                clock.now = self.expiry
            return await super().authorize(actor, action, resource)

    auth = Auth()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1}))]), service)
    async with Runtime(
        agents=[agent], storage=SQLiteStorage(tmp_path / "expired.db", clock=clock), authorizer=auth
    ) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        auth.expiry = result.pending.expires_at
        receipt = await runtime.respond(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        outcome = await runtime.wait(result.run_id, context=ALICE)
        assert outcome.status == "failed" and outcome.error.code == "interaction_expired"
        assert not service.calls
        assert (
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
            == receipt
        )


@pytest.mark.asyncio
async def test_cumulative_model_steps_not_reset_after_response(storage: Storage) -> None:
    service = Service()
    agent = build(
        ScriptedModel(
            [plan(("read_item", {"value": 1})), plan(("read_item", {"value": 2})), final()]
        ),
        service,
        limits=Limits(max_model_steps=2),
    )
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        second = await runtime.resume(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        assert isinstance(second, Suspended) and second.pending is not None
        outcome = await runtime.resume(
            result.run_id,
            request_id=second.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        assert outcome.status == "failed" and outcome.error.code == "limit_exceeded"
        assert len(service.calls) == 2 and len(agent.model.calls) == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_human_input_ask_first_approval_then_input(storage: Storage) -> None:
    agent: Agent[str] = Agent(
        name="operations",
        revision="1",
        model=ScriptedModel([plan(("ask_item", {"prompt": "choose"})), final()]),
        tools=[HumanInput(name="ask_item", input_type=Choice)],
    )
    async with Runtime(
        agents=[agent], storage=storage, policy=PermissionPolicy(ask={"ask_item"})
    ) as runtime:
        first = await pause(runtime, agent)
        assert isinstance(first.pending, ApprovalRequest)
        second = await runtime.resume(
            first.run_id,
            request_id=first.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        assert isinstance(second, Suspended) and isinstance(second.pending, InputRequest)
        outcome = await runtime.resume(
            first.run_id,
            request_id=second.pending.request_id,
            response=InputResponse(value={"item_id": "one"}),
            context=ALICE,
        )
        assert outcome.status == "completed"
        assert (await storage.get_checkpoint(ALICE, first.run_id)).data["tool_dispatch_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["approval", "input"])
async def test_cancel_response_race_never_dispatches_twice(storage: Storage, kind: str) -> None:
    service = Service()
    if kind == "approval":
        agent = build(ScriptedModel([plan(("read_item", {"value": 1})), final()]), service)
        response = ApprovalDecision(approved=True)
    else:
        agent = Agent(
            name="operations",
            revision="1",
            model=ScriptedModel([plan(("ask_item", {"prompt": "choose"})), final()]),
            tools=[HumanInput(name="ask_item", input_type=Choice)],
        )
        response = InputResponse(value={"item_id": "one"})  # type: ignore[assignment]
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        outcomes = await asyncio.gather(
            runtime.cancel(result.run_id, context=ALICE),
            runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=response,
                context=ALICE,
            ),
            return_exceptions=True,
        )
        assert all(
            not isinstance(value, BaseException) or isinstance(value, InteractionConflict)
            for value in outcomes
        )
        assert (await runtime.wait(result.run_id, context=ALICE)).status == "cancelled"
        assert len(service.calls) <= 1
        async with storage.transaction() as tx:
            assert len(await tx.items(ALICE, result.run_id, "receipt")) <= 1


@pytest.mark.asyncio
async def test_original_parameter_binding_tamper_does_not_consume(storage: Storage) -> None:
    service = Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1})), final()]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await pause(runtime, agent)
        assert result.pending is not None
        async with storage.transaction() as tx:
            item = await tx.get_item(ALICE, result.run_id, "interaction", result.pending.request_id)
            data = dict(item.data)
            data["request_hash"] = "0" * 64
            await tx.put_item(
                ALICE,
                item.model_copy(update={"version": item.version + 1, "data": data}),
                expected_version=item.version,
            )
        with pytest.raises(InteractionConflict):
            await runtime.respond(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=ALICE,
            )
        assert not service.calls
        async with storage.transaction() as tx:
            assert not await tx.items(ALICE, result.run_id, "receipt")


@pytest.mark.asyncio
async def test_stream_resubscription_ignores_historical_suspend(storage: Storage) -> None:
    service = Service()
    agent = build(ScriptedModel([plan(("read_item", {"value": 1})), final()]), service)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        async with runtime.stream(agent, "one", session_id="s", context=ALICE) as stream:
            initial = [event async for event in stream]
            result = await stream.result()
        assert isinstance(result, Suspended) and result.pending is not None
        assert initial[-1].type == "run.suspended"
        await runtime.respond(
            result.run_id,
            request_id=result.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=ALICE,
        )
        async with runtime.events(result.run_id, context=ALICE, include_content=True) as sub:
            events = [event async for event in sub]
        assert events[-1].type == "run.completed"
        assert len([event for event in events if event.type == "interaction.responded"]) == 1
        assert len(service.calls) == 1
