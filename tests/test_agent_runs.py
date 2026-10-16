"""T-06 public-entry integration tests with real read tools and offline adapters."""

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from drov import (
    Agent,
    Limits,
    Memory,
    MemoryStorage,
    RunContext,
    Runtime,
    SQLiteStorage,
    tool,
)
from drov.auth import Action, OwnerAuthorizer, Resource
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    InvalidCursor,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
    SessionBusy,
    ShutdownIncomplete,
    StorageInUse,
    TransientModelError,
)
from drov.models import ScriptedModel, ScriptedStep
from drov.models.protocol import (
    ModelComplete,
    ModelDelta,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ToolCall,
)
from drov.storage.records import EventDraft
from drov.tools.schema import compile_tool
from drov.types import Completed, DurableEvent, Failed, StreamGap, TokenDelta, Usage
from tests.fixtures import close_released_runtime

ALICE = RunContext(tenant_id="test", user_id="alice")
BOB = RunContext(tenant_id="test", user_id="bob")


def final(text: str = "answer", usage: Usage | None = None) -> ModelResponse:
    return ModelResponse(message=text, finish_reason="completed", usage=usage or Usage())


def plan(*names: str) -> ModelResponse:
    return ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[
            ToolCall(call_id=f"call-{i}", name=name, arguments={}) for i, name in enumerate(names)
        ],
    )


def definition(model: ScriptedModel, **kwargs: Any) -> Agent[Any]:
    return Agent(name="research", revision="1", model=model, **kwargs)


class GateModel(ScriptedModel):
    def __init__(self, response: ModelResponse | None = None) -> None:
        super().__init__([response or final()])
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.entered.set()
        await self.release.wait()
        async for event in super().generate(request):
            yield event


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_tool_plan_committed_before_real_call_and_typed_output(
    tmp_path: Path, backend: str
) -> None:
    count = 0
    storage = MemoryStorage() if backend == "memory" else SQLiteStorage(tmp_path / "run.db")

    @tool(name="search", effect="read")
    async def search() -> list[str]:
        nonlocal count
        count += 1
        assert len(model.calls) == 1
        async with runtime.storage.transaction() as tx:
            responses = await tx.items(ALICE, ref.run_id, "model_response")
            assert responses[0].data["operation_ids"]
        return ["synthetic-source"]

    @dataclass
    class Report:
        summary: str
        citations: list[str]

    model = ScriptedModel(
        [plan("search"), final('{"summary":"supported","citations":["synthetic-source"]}')]
    )
    agent = definition(model, tools=[search], output_type=Report)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        ref = await runtime.start(agent, "research", session_id="a", context=ALICE)
        result = await runtime.wait(ref.run_id, context=ALICE)
        assert isinstance(result, Completed) and type(result.output) is Report
        assert result.output.citations == ["synthetic-source"] and count == 1
        messages = model.calls[1].messages
        assert [message.role for message in messages] == ["user", "assistant", "tool"]
        assert messages[-1].call_id == "call-0"
        snapshot = await runtime.inspect(ref.run_id, context=ALICE, include_content=True)
        assert snapshot.events[-1].data["output"] == {
            "summary": "supported",
            "citations": ["synthetic-source"],
        }
        assert snapshot.snapshot.outcome is not None
        assert snapshot.snapshot.outcome.state_version == result.state_version
    assert not model.closed  # A borrowed model survives a temporary or persistent Runtime.


@pytest.mark.asyncio
async def test_dedup_precedes_session_busy_and_full_capacity() -> None:
    model = GateModel()
    agent = definition(model)
    async with Runtime(agents=[agent], limits=Limits(active_slots=1)) as runtime:
        refs = await asyncio.gather(
            *(
                runtime.start(
                    agent,
                    "same",
                    session_id="one",
                    context=ALICE,
                    request_key="one",
                )
                for _ in range(20)
            )
        )
        assert all(ref == refs[0] for ref in refs) and runtime.active_slots == 1
        with pytest.raises(Conflict):
            await runtime.start(
                agent, "different", session_id="one", context=ALICE, request_key="one"
            )
        with pytest.raises(SessionBusy):
            await runtime.start(agent, "new key", session_id="one", context=ALICE)
        with pytest.raises(CapacityExceeded):
            await runtime.start(agent, "new", session_id="two", context=ALICE)
        model.release.set()
        await runtime.wait(refs[0].run_id, context=ALICE)
        assert (
            await runtime.start(agent, "same", session_id="one", context=ALICE, request_key="one")
            == refs[0]
        )
        assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_busy_session_without_capacity_pressure() -> None:
    model = GateModel()
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        ref = await runtime.start(agent, "one", session_id="one", context=ALICE)
        with pytest.raises(SessionBusy):
            await runtime.start(agent, "two", session_id="one", context=ALICE)
        model.release.set()
        await runtime.wait(ref.run_id, context=ALICE)


@pytest.mark.asyncio
async def test_wait_and_stream_disconnect_preserve_execution() -> None:
    model = GateModel()
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        async with runtime.stream(agent, "one", session_id="one", context=ALICE) as stream:
            event = await anext(stream)
            assert isinstance(event, DurableEvent) and event.type == "run.accepted"
            run_id = stream.run_id
            waiter = asyncio.create_task(runtime.wait(run_id, context=ALICE))
            await model.entered.wait()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        record = await runtime.storage.get_run(ALICE, run_id)
        assert record.state.status == "running" and not record.state.cancel_requested
        assert runtime.active_slots == 1 and not runtime._runs.subscribers
        model.release.set()
        result = await stream.result()
        assert result.status == "completed" and len(model.calls) == 1


@pytest.mark.asyncio
async def test_stream_and_paged_reconnect_agree_with_result() -> None:
    model = ScriptedModel(
        [
            ScriptedStep(
                events=(ModelDelta(text="provisional"), ModelComplete(response=final("committed")))
            )
        ]
    )
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        async with runtime.stream(agent, "one", session_id="one", context=ALICE) as stream:
            events = [event async for event in stream]
            result = await stream.result()
            assert isinstance(result, Completed)
        durable = [event for event in events if isinstance(event, DurableEvent)]
        assert durable[-1].type == "run.completed" and durable[-1].data["output"] == result.output
        assert any(isinstance(event, TokenDelta) for event in events)
        assert [event.seq for event in durable] == list(range(1, len(durable) + 1))
        cursor = None
        replay: list[DurableEvent] = []
        while True:
            page = await runtime.inspect(stream.run_id, context=ALICE, after=cursor, limit=2)
            replay.extend(page.events)
            cursor = page.next_cursor
            if not page.has_more:
                break
        assert [event.seq for event in replay] == [event.seq for event in durable]
        assert all(not event.content_visible and "output" not in event.data for event in replay)
        async with runtime.events(stream.run_id, context=ALICE, after=f"{stream.run_id}:2") as sub:
            later = [event async for event in sub]
        assert [event.seq for event in later if isinstance(event, DurableEvent)] == list(
            range(3, len(durable) + 1)
        )
        with pytest.raises(InvalidCursor):
            await runtime.inspect(stream.run_id, context=ALICE, after="foreign:1")
        with pytest.raises(InvalidCursor):
            await runtime.inspect(stream.run_id, context=ALICE, after=f"{stream.run_id}:999")


@pytest.mark.asyncio
async def test_bounded_delta_buffer_and_metadata_subscribers() -> None:
    model = ScriptedModel(
        [
            ScriptedStep(
                events=tuple(ModelDelta(text=str(i)) for i in range(8))
                + (ModelDelta(text="oversized"), ModelComplete(response=final()))
            )
        ]
    )
    agent = definition(model)
    async with Runtime(
        agents=[agent], limits=Limits(subscription_buffer=2, max_delta_bytes=3)
    ) as runtime:
        async with runtime.stream(agent, "one", session_id="one", context=ALICE) as stream:
            await stream.result()  # Deliberately slow consumer.
            assert len(stream._deltas) <= 2
            events = [event async for event in stream]
        assert sum(isinstance(event, TokenDelta) for event in events) == 2
        gaps = [event for event in events if isinstance(event, StreamGap)]
        assert gaps[0].data["dropped_deltas"] == 7
        assert any(
            isinstance(event, DurableEvent) and event.type == "run.completed" for event in events
        )
        async with runtime.events(stream.run_id, context=ALICE) as sub:
            metadata = [event async for event in sub]
        assert all(isinstance(event, DurableEvent) for event in metadata)


class ContentAccess(OwnerAuthorizer):
    content = True

    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
        return actor == resource.owner and (self.content or action != "view_content")


@pytest.mark.asyncio
async def test_body_preflight_before_acceptance_and_metadata_is_useful() -> None:
    access = ContentAccess()
    access.content = False
    model = ScriptedModel([final("private-answer")])
    agent = definition(model)
    async with Runtime(agents=[agent], authorizer=access) as runtime:
        with pytest.raises(PermissionDenied):
            await runtime.run(agent, "private-input", session_id="one", context=ALICE)
        with pytest.raises(PermissionDenied):
            async with runtime.stream(agent, "private-input", session_id="one", context=ALICE):
                pass
        assert not model.calls
        async with runtime.storage.transaction() as tx:
            assert not await tx.sessions(ALICE)
        ref = await runtime.start(agent, "private-input", session_id="one", context=ALICE)
        async with runtime.events(ref.run_id, context=ALICE) as sub:
            events = [event async for event in sub]
        page = await runtime.inspect(ref.run_id, context=ALICE)
        serialized = page.model_dump_json() + "".join(event.model_dump_json() for event in events)
        assert "private-input" not in serialized and "private-answer" not in serialized
        assert page.snapshot.status == "completed" and page.snapshot.outcome is None
        with pytest.raises(PermissionDenied):
            await runtime.wait(ref.run_id, context=ALICE)
        with pytest.raises(RecordNotFound):
            await runtime.inspect(ref.run_id, context=BOB)


@pytest.mark.asyncio
async def test_cached_events_and_final_wait_reauthorize_after_revocation() -> None:
    access = ContentAccess()
    model = GateModel()
    agent = definition(model)
    async with Runtime(agents=[agent], authorizer=access) as runtime:
        async with runtime.stream(agent, "one", session_id="one", context=ALICE) as stream:
            await anext(stream)
            waiter = asyncio.create_task(stream.result())
            await model.entered.wait()
            access.content = False
            with pytest.raises(PermissionDenied):
                await anext(stream)
            model.release.set()
            with pytest.raises(PermissionDenied):
                await waiter
        async with runtime.events(stream.run_id, context=ALICE) as metadata:
            assert [event.type async for event in metadata][-1] == "run.completed"


@pytest.mark.asyncio
async def test_provider_items_round_trip_without_body_exposure() -> None:
    count = 0

    @tool(name="search", effect="read")
    def search() -> str:
        nonlocal count
        count += 1
        return "visible-tool-result"

    response = plan("search").model_copy(
        update={"provider_items": [{"raw": "private-provider-item"}]}
    )
    model = ScriptedModel([response, final()])
    agent = definition(model, tools=[search])
    async with Runtime(agents=[agent]) as runtime:
        outcome = await runtime.run(agent, "one", session_id="one", context=ALICE)
        page = await runtime.inspect(outcome.run_id, context=ALICE, include_content=True)
        assert model.calls[1].provider_items == [{"raw": "private-provider-item"}]
        assert "private-provider-item" not in page.model_dump_json()
        assert "visible-tool-result" in page.model_dump_json() and count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unsupported", ["schema"])
async def test_unsupported_configuration_rejected_before_acceptance(unsupported: str) -> None:
    def function() -> str:
        raise AssertionError("Must not execute")

    model = ScriptedModel([final()])
    tools = (
        [compile_tool(function, name="search", revision="1", effect="read")]
        if unsupported == "schema"
        else []
    )
    if unsupported in ("unknown", "write"):
        tools = [tool(name="search", effect=unsupported)(function)]  # type: ignore[arg-type]
    agent = definition(model, tools=tools, memory=Memory() if unsupported == "memory" else None)
    async with Runtime(agents=[agent]) as runtime:
        with pytest.raises(ConfigurationError):
            await runtime.start(agent, "one", session_id="one", context=ALICE)
        async with runtime.storage.transaction() as tx:
            assert not await tx.sessions(ALICE)
        assert not model.calls


@pytest.mark.asyncio
async def test_temporary_ask_has_no_dispatch_or_resume_id() -> None:
    count = 0

    @tool(name="search", effect="read", approval="required")
    def search() -> str:
        nonlocal count
        count += 1
        return "cannot execute"

    agent: Agent[str] = Agent(model=ScriptedModel([plan("search")]), tools=[search])
    outcome = await agent.run("one")
    assert isinstance(outcome, Failed) and outcome.error.code == "interaction_unavailable"
    assert outcome.operations[0].status == "not_executed" and count == 0
    assert "request_id" not in outcome.model_dump_json()


def test_sync_entry_and_definition_identity_preserved() -> None:
    model = ScriptedModel([final()])
    agent: Agent[str] = Agent(model=model)
    assert agent.run_sync("one").status == "completed"
    assert agent.name is None and agent.revision is None and not model.closed


@pytest.mark.asyncio
async def test_sync_entry_rejects_existing_loop_without_model_call() -> None:
    model = ScriptedModel([final()])
    agent: Agent[str] = Agent(model=model)
    with pytest.raises(ConfigurationError, match="await"):
        agent.run_sync("one")
    assert not model.calls


@pytest.mark.asyncio
async def test_model_retry_uses_identical_saved_input_and_independent_attempts() -> None:
    model = ScriptedModel(
        [ScriptedStep(error=TransientModelError("sensitive provider message")), final()]
    )
    agent = definition(model, limits=Limits(retry_backoff_seconds=0.001))
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        assert result.status == "completed" and model.calls[0] == model.calls[1]
        page = await runtime.inspect(result.run_id, context=ALICE, include_content=True)
        attempts = [
            event.data["attempt_id"]
            for event in page.events
            if event.type == "model.attempt_started"
        ]
        assert len(set(attempts)) == 2
        assert not result.usage.complete and result.usage.total_tokens is None
        assert "sensitive provider message" not in page.model_dump_json()
        async with runtime.storage.transaction() as tx:
            inputs = await tx.items(ALICE, result.run_id, "model_input")
        assert len(inputs) == 1 and inputs[0].data["request"] == model.calls[0].model_dump(
            mode="json"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        (),
        (ModelDelta(text="partial"),),
        (ModelComplete(response=plan("search")), ModelDelta(text="after-complete")),
    ],
)
async def test_incomplete_or_malformed_model_stream_never_executes_tools(
    events: tuple[ModelEvent, ...],
) -> None:
    count = 0

    @tool(name="search", effect="read")
    def search() -> str:
        nonlocal count
        count += 1
        return "read"

    model = ScriptedModel([ScriptedStep(events=events)])
    agent = definition(model, tools=[search])
    outcome = await agent.run("one")
    assert isinstance(outcome, Failed) and outcome.error.code == "model_protocol_error"
    assert count == 0 and len(model.calls) == 1


@pytest.mark.asyncio
async def test_model_timeout_retains_unknown_usage_and_does_not_retry() -> None:
    model = GateModel()
    agent = definition(model, limits=Limits(model_timeout_seconds=0.02))
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        assert isinstance(result, Failed) and result.error.code == "model_timeout"
        assert result.usage.total_tokens is None and not result.usage.complete
        page = await runtime.inspect(result.run_id, context=ALICE)
        assert sum(event.type == "model.attempt_started" for event in page.events) == 1
        assert any(event.type == "model.attempt_failed" for event in page.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ['{"n":"1"}', '{"n":1,"n":2}', '{"n":NaN}', '{"n":1,"extra":2}'])
async def test_final_output_strict_validation(text: str) -> None:
    @dataclass
    class Report:
        n: int

    outcome = await Agent(model=ScriptedModel([final(text)]), output_type=Report).run("one")
    assert isinstance(outcome, Failed) and outcome.error.code == "invalid_output"


@pytest.mark.asyncio
async def test_model_and_tool_budgets_stop_new_dispatch() -> None:
    count = 0

    @tool(name="search", effect="read")
    def search() -> str:
        nonlocal count
        count += 1
        return "read"

    model = ScriptedModel([plan("search", "search"), final()])
    outcome = await definition(model, tools=[search], limits=Limits(max_tool_calls=1)).run("one")
    assert isinstance(outcome, Failed) and outcome.error.code == "limit_exceeded"
    assert count == 1 and len(model.calls) == 1
    model = ScriptedModel([plan("search"), final()])
    outcome = await definition(model, tools=[search], limits=Limits(max_model_steps=1)).run("one")
    assert isinstance(outcome, Failed) and outcome.error.code == "limit_exceeded"
    assert len(model.calls) == 1 and count == 2


@pytest.mark.asyncio
async def test_expired_run_and_context_overflow_make_no_model_call() -> None:
    for limits, code in [
        (Limits(expires_at=datetime.now(UTC) - timedelta(seconds=1)), "deadline_exceeded"),
        (Limits(max_input_tokens=1), "context_overflow"),
    ]:
        model = ScriptedModel([final()])
        agent: Agent[str] = Agent(model=model, limits=limits)
        outcome = await agent.run("one")
        assert isinstance(outcome, Failed) and outcome.error.code == code and not model.calls


@pytest.mark.asyncio
async def test_limit_override_cannot_loosen_admin_ceiling() -> None:
    model = ScriptedModel([final()])
    agent = definition(model)
    async with Runtime(agents=[agent], limits=Limits(max_model_steps=1)) as runtime:
        with pytest.raises(ConfigurationError):
            await runtime.start(
                agent, "one", session_id="one", context=ALICE, limits=Limits(max_model_steps=2)
            )
        result = await runtime.run(
            agent, "one", session_id="one", context=ALICE, limits=Limits(model_retries=0)
        )
        assert result.status == "completed"
        record = await runtime.storage.get_run(ALICE, result.run_id)
        assert record.limits.max_model_steps == 1 and record.limits.model_retries == 0


@pytest.mark.asyncio
async def test_explicit_cancel_is_durable_and_terminal_cancel_idempotent() -> None:
    model = GateModel()
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        ref = await runtime.start(agent, "one", session_id="one", context=ALICE)
        await model.entered.wait()
        await runtime.cancel(ref.run_id, context=ALICE)
        result = await runtime.wait(ref.run_id, context=ALICE)
        assert result.status == "cancelled"
        receipt = await runtime.cancel(ref.run_id, context=ALICE)
        assert receipt.status == "cancelled" and receipt.state_version == result.state_version
        page = await runtime.inspect(ref.run_id, context=ALICE)
        assert sum(event.type == "run.cancel_requested" for event in page.events) == 1
        assert page.events[-1].type == "run.cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed_notification", [False, True])
async def test_cancelled_actual_thread_retains_slot_and_sqlite_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delayed_notification: bool
) -> None:
    entered, release = asyncio.Event(), threading.Event()
    delivered = asyncio.Event()
    loop = asyncio.get_running_loop()

    @tool(name="search", effect="read")
    def search() -> str:
        loop.call_soon_threadsafe(entered.set)
        release.wait(30)
        return "late-result"

    model = ScriptedModel([plan("search")])
    agent = definition(model, tools=[search])
    path = tmp_path / "calls.db"
    runtime = Runtime(
        agents=[agent], storage=SQLiteStorage(path), limits=Limits(shutdown_grace_seconds=0.02)
    )
    await runtime.open()
    try:
        ref = await runtime.start(agent, "one", session_id="one", context=ALICE)
        # Setup scheduling is independent of the 20ms shutdown grace under test.
        await asyncio.wait_for(entered.wait(), 10)
        if delayed_notification:
            work = runtime._runs.works[ref.run_id]
            original = work._call_done

            def notify_later(call: Any) -> None:
                def deliver() -> None:
                    original(call)
                    delivered.set()

                loop.call_later(1.1, deliver)

            monkeypatch.setattr(work, "_call_done", notify_later)
        await runtime.cancel(ref.run_id, context=ALICE)
        assert (await runtime.wait(ref.run_id, context=ALICE)).status == "cancelled"
        with pytest.raises(ShutdownIncomplete):
            await runtime.aclose()
        assert runtime.active_slots == 1
        with pytest.raises(StorageInUse):
            await SQLiteStorage(path).open()
    finally:
        release.set()
        await close_released_runtime(runtime)
    if delayed_notification:
        assert delivered.is_set()
    async with SQLiteStorage(path) as storage:
        record = await storage.get_run(ALICE, ref.run_id)
        assert record.state.status == "cancelled"
        assert "late-result" not in record.model_dump_json()
        async with storage.transaction() as tx:
            operation = await tx.get_item(
                ALICE, ref.run_id, "operation", record.state.operations[0].operation_id
            )
        assert operation.data["has_result"] is False and operation.data["result"] is None


@pytest.mark.asyncio
async def test_history_keeps_failed_and_cancelled_inputs_once_after_reopen(tmp_path: Path) -> None:
    model = ScriptedModel([ScriptedStep(events=(ModelDelta(text="uncommitted"),)), final("second")])
    agent = definition(model)
    path = tmp_path / "history.db"
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        first = await runtime.run(agent, "first-input", session_id="one", context=ALICE)
        assert first.status == "failed"
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        second = await runtime.run(agent, "second-input", session_id="one", context=ALICE)
        assert second.status == "completed"
        assert [m.content for m in model.calls[1].messages] == ["first-input", "second-input"]


class FailingCommit(MemoryStorage):
    def __init__(self, event_type: str) -> None:
        super().__init__()
        self.event_type = event_type

    async def _finish(self, commit: bool) -> None:
        if (
            commit
            and self._working is not None
            and any(
                row.key.kind == "event" and json.loads(row.payload)["type"] == self.event_type
                for row in self._working.values()
            )
        ):
            await super()._finish(False)
            raise PersistenceUnavailable("injected commit failure")
        await super()._finish(commit)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type", ["model.attempt_completed", "tool.dispatched", "run.completed"]
)
async def test_commit_failure_stops_pipeline_without_fabricating_terminal(event_type: str) -> None:
    count = 0

    @tool(name="search", effect="read")
    def search() -> str:
        nonlocal count
        count += 1
        return "read"

    model = ScriptedModel([plan("search"), final()])
    agent = definition(model, tools=[search])
    async with Runtime(agents=[agent], storage=FailingCommit(event_type)) as runtime:
        ref = await runtime.start(agent, "one", session_id="one", context=ALICE)
        with pytest.raises(PersistenceUnavailable):
            await runtime.wait(ref.run_id, context=ALICE)
        page = await runtime.inspect(ref.run_id, context=ALICE)
        assert page.snapshot.status == "running" and page.snapshot.outcome is None
        assert count == (1 if event_type == "run.completed" else 0)


@pytest.mark.asyncio
async def test_handoff_failure_suspends_instead_of_orphaned_accepted() -> None:
    agent = definition(ScriptedModel([final()]))
    async with Runtime(agents=[agent]) as runtime:
        with patch.object(runtime, "_spawn", side_effect=RuntimeError("schedule failure")):
            with pytest.raises(RuntimeError):
                await runtime.start(agent, "one", session_id="one", context=ALICE)
        async with runtime.storage.transaction() as tx:
            runs = await tx.runs((await tx.sessions(ALICE))[0].scope)
        assert runs[0].state.status == "suspended" and runs[0].state.reason == "recovery"
        assert runtime.active_slots == 0
        async with runtime.events(runs[0].state.run_id, context=ALICE) as sub:
            assert [event.type async for event in sub][-1] == "recovery.required"


@pytest.mark.asyncio
async def test_old_suspension_event_does_not_end_current_running_stream() -> None:
    model = GateModel()
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        ref = await runtime.start(agent, "one", session_id="one", context=ALICE)
        await model.entered.wait()
        async with runtime.storage.transaction() as tx:
            run = await tx.get_run(ALICE, ref.run_id)
            checkpoint = await tx.get_checkpoint(ALICE, ref.run_id)
            version = run.state.state_version + 1
            await tx.save_step(
                ALICE,
                state=run.state.model_copy(update={"state_version": version}),
                expected_version=version - 1,
                checkpoint=checkpoint.model_copy(update={"state_version": version}),
                events=[EventDraft(type="run.suspended"), EventDraft(type="run.running")],
            )
        async with runtime.events(ref.run_id, context=ALICE) as sub:
            received: list[str] = []
            async for event in sub:
                received.append(event.type)
                if event.type == "run.suspended":
                    model.release.set()
            assert received[-1] == "run.completed"


class ReceivingGate(MemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.gated = False

    async def _finish(self, commit: bool) -> None:
        if (
            commit
            and not self.gated
            and self._working
            and any(row.key.kind == "run" for row in self._working.values())
        ):
            self.gated = True
            self.entered.set()
            await self.release.wait()
        await super()._finish(commit)


@pytest.mark.asyncio
async def test_cancel_receiving_waiter_keeps_committed_run_owned() -> None:
    storage = ReceivingGate()
    model = GateModel()
    agent = definition(model)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        receiving = asyncio.create_task(
            runtime.start(agent, "one", session_id="one", context=ALICE)
        )
        await storage.entered.wait()
        receiving.cancel()
        assert runtime.active_slots == 1 and runtime._controls
        storage.release.set()
        with pytest.raises(asyncio.CancelledError):
            await receiving
        async with runtime.storage.transaction() as tx:
            run = (await tx.runs((await tx.sessions(ALICE))[0].scope))[0]
        assert run.state.run_id in runtime._runs.works
        model.release.set()
        assert (await runtime.wait(run.state.run_id, context=ALICE)).status == "completed"


@pytest.mark.asyncio
async def test_shutdown_during_receiving_retains_resources_until_handoff_settles() -> None:
    storage = ReceivingGate()
    await storage.open()
    model = ScriptedModel([final()])
    agent = definition(model)
    runtime = Runtime(agents=[agent], storage=storage, limits=Limits(shutdown_grace_seconds=0.02))
    await runtime.open()
    receiving = asyncio.create_task(runtime.start(agent, "one", session_id="one", context=ALICE))
    await storage.entered.wait()
    with pytest.raises(ShutdownIncomplete, match="transactions"):
        await runtime.aclose()
    assert runtime.active_slots == 1 and storage.is_open
    storage.release.set()
    with pytest.raises(ConfigurationError):
        await receiving
    await close_released_runtime(runtime)
    async with storage.transaction() as tx:
        run = (await tx.runs((await tx.sessions(ALICE))[0].scope))[0]
    assert run.state.status == "suspended" and run.state.reason == "recovery" and not model.calls
    await storage.aclose()


@pytest.mark.asyncio
async def test_receiving_commit_failure_creates_no_run_or_call() -> None:
    model = ScriptedModel([final()])
    agent = definition(model)
    async with Runtime(agents=[agent], storage=FailingCommit("run.accepted")) as runtime:
        with pytest.raises(PersistenceUnavailable):
            await runtime.start(agent, "one", session_id="one", context=ALICE)
        assert runtime.active_slots == 0 and not model.calls
        async with runtime.storage.transaction() as tx:
            assert not await tx.sessions(ALICE)


@pytest.mark.asyncio
async def test_cancelled_tool_group_projects_explicit_results_into_next_run() -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    @tool(name="search", effect="read")
    async def search() -> str:
        entered.set()
        await release.wait()
        return "read"

    model = ScriptedModel([plan("search", "search"), final("new answer")])
    agent = definition(model, tools=[search])
    async with Runtime(agents=[agent]) as runtime:
        ref = await runtime.start(agent, "first", session_id="one", context=ALICE)
        await entered.wait()
        await runtime.cancel(ref.run_id, context=ALICE)
        assert (await runtime.wait(ref.run_id, context=ALICE)).status == "cancelled"
        result = await runtime.run(agent, "second", session_id="one", context=ALICE)
        assert result.status == "completed"
        messages = model.calls[1].messages
        assert [m.role for m in messages] == ["user", "assistant", "tool", "tool", "user"]
        assert [m.call_id for m in messages if m.role == "tool"] == ["call-0", "call-1"]
        assert messages[-2].content == {
            "status": "not_executed",
            "has_result": False,
            "result": None,
            "error_code": "run_ended_before_dispatch",
        }


@pytest.mark.asyncio
async def test_saved_budget_and_terminal_result_survive_reopen_and_dedup(tmp_path: Path) -> None:
    @tool(name="search", effect="read")
    def search() -> str:
        return "read"

    model = ScriptedModel([plan("search", "search")])
    agent = definition(model, tools=[search], limits=Limits(max_tool_calls=1))
    path = tmp_path / "budget.db"
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        ref = await runtime.start(agent, "one", session_id="one", context=ALICE, request_key="same")
        outcome = await runtime.wait(ref.run_id, context=ALICE)
        assert isinstance(outcome, Failed) and outcome.operations[-1].status == "not_executed"
        saved = await runtime.storage.get_checkpoint(ALICE, ref.run_id)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        assert (
            await runtime.start(agent, "one", session_id="one", context=ALICE, request_key="same")
            == ref
        )
        assert await runtime.wait(ref.run_id, context=ALICE) == outcome
        checkpoint = await runtime.storage.get_checkpoint(ALICE, ref.run_id)
        assert checkpoint.data == saved.data
        assert (
            checkpoint.data["tool_dispatch_count"] == 1 and checkpoint.data["model_step_count"] == 1
        )
        assert checkpoint.data["model_attempt_count"] == 1
        assert isinstance(checkpoint.data["active_seconds"], float)
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_noncooperative_model_keeps_slot_after_timeout_until_actual_stop() -> None:
    class StubbornModel(GateModel):
        async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()
            yield ModelComplete(response=final("late result"))

    model = StubbornModel()
    agent = definition(model, limits=Limits(model_timeout_seconds=0.01))
    runtime = Runtime(agents=[agent], limits=Limits(shutdown_grace_seconds=0.01))
    await runtime.open()
    try:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        assert isinstance(result, Failed) and runtime.active_slots == 1
        with pytest.raises(ShutdownIncomplete):
            await runtime.aclose()
    finally:
        model.release.set()
        await close_released_runtime(runtime)


@pytest.mark.asyncio
async def test_cancel_after_completed_keeps_success_version() -> None:
    agent = definition(ScriptedModel([final()]))
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        receipt = await runtime.cancel(result.run_id, context=ALICE)
        assert receipt.status == "completed" and not receipt.cancel_requested
        assert receipt.state_version == result.state_version


@pytest.mark.asyncio
async def test_known_usage_accumulates_and_invalid_output_keeps_consumption() -> None:
    @tool(name="search", effect="read")
    def search() -> str:
        return "read"

    known = Usage(
        input_tokens=3,
        output_tokens=2,
        total_tokens=5,
        cost_amount="0.01",
        currency="USD",
        complete=True,
    )
    response = plan("search").model_copy(update={"usage": known})
    model = ScriptedModel([response, final("answer", known)])
    result = await definition(model, tools=[search]).run("one")
    assert (
        result.usage.total_tokens == 10
        and result.usage.cost_amount == "0.02"
        and result.usage.complete
    )
    result = await definition(
        ScriptedModel([final("oversized", known)]), limits=Limits(max_tool_result_bytes=2)
    ).run("one")
    assert isinstance(result, Failed) and result.error.code == "invalid_output"
    assert result.usage.total_tokens == 5 and result.usage.complete


@pytest.mark.asyncio
async def test_model_error_not_retried_or_exposed_to_caller() -> None:
    model = ScriptedModel([ScriptedStep(error=RuntimeError("private failure content")), final()])
    result = await definition(model).run("one")
    assert isinstance(result, Failed) and result.error.code == "model_error"
    assert len(model.calls) == 1 and "private failure content" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_unknown_tool_never_dispatches_and_refusal_keeps_usage() -> None:
    model = ScriptedModel([plan("not_registered")])
    result = await definition(model).run("one")
    assert isinstance(result, Failed) and result.error.code == "unknown_tool"
    response = ModelResponse(
        message="refused", finish_reason="refusal", usage=Usage(total_tokens=5, complete=True)
    )
    result = await definition(ScriptedModel([response])).run("one")
    assert isinstance(result, Failed) and result.error.code == "model_refusal"
    assert result.usage.total_tokens == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_offline_reference_example_matches_retrieved_facts(streamed: bool) -> None:
    from examples.research_offline import research

    report = await research(streamed=streamed)
    assert report.source_ids == ["cache-a", "cache-b"]


@pytest.mark.asyncio
async def test_temporary_limits_are_application_owned() -> None:
    agent: Agent[str] = Agent(model=ScriptedModel([final()]))
    result = await agent.run("one", limits=Limits(max_model_steps=21))
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_unauthorized_tool_result_is_explicit_and_model_may_explain() -> None:
    class Access(OwnerAuthorizer):
        async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
            return actor == resource.owner and action != "execute_tool"

    count = 0

    @tool(name="search", effect="read")
    def search() -> str:
        nonlocal count
        count += 1
        return "cannot execute"

    model = ScriptedModel([plan("search"), final("retrieval unavailable")])
    agent = definition(model, tools=[search])
    async with Runtime(agents=[agent], authorizer=Access()) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        assert result.status == "completed" and count == 0
        content = model.calls[1].messages[-1].content
        assert isinstance(content, dict) and content["error_code"] == "permission_denied"
        assert content["status"] == "not_executed"


@pytest.mark.asyncio
async def test_eager_factory_enters_model_only_after_task_ownership() -> None:
    eager = getattr(asyncio, "eager_task_factory", None)
    if eager is None:
        pytest.skip("Python 3.12+ API")
    model = ScriptedModel([final()])
    agent = definition(model)
    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()
    loop.set_task_factory(eager)
    try:
        async with Runtime(agents=[agent]) as runtime:
            result = await runtime.run(agent, "one", session_id="one", context=ALICE)
            assert result.status == "completed" and len(model.calls) == 1
    finally:
        loop.set_task_factory(previous)


@pytest.mark.asyncio
async def test_equal_timestamps_do_not_reorder_current_input_before_history() -> None:
    fixed = datetime(2026, 10, 13, tzinfo=UTC)
    storage = MemoryStorage(clock=lambda: fixed)
    model = ScriptedModel([final("first answer"), final("second answer")])
    agent = definition(model)
    async with Runtime(agents=[agent], storage=storage) as runtime:
        with patch("drov.storage.transaction.uuid4", side_effect=["z-first", "a-second"]):
            await runtime.run(agent, "first input", session_id="one", context=ALICE)
            await runtime.run(agent, "second input", session_id="one", context=ALICE)
        assert [m.content for m in model.calls[1].messages] == [
            "first input",
            "first answer",
            "second input",
        ]
