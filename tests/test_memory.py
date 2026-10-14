"""T-08 actual storage, transaction, scope and input-snapshot boundaries."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from drov import (
    Agent,
    ContextPolicy,
    Limits,
    Memory,
    MemoryStorage,
    PermissionPolicy,
    Runtime,
    SQLiteStorage,
)
from drov._json import canonical_json
from drov.auth import Action, OwnerAuthorizer, Resource
from drov.context.assembly import estimate
from drov.errors import ConfigurationError, Conflict, PersistenceUnavailable, TransientModelError
from drov.memory.execution import MemoryDispatcher
from drov.memory.tools import TOOLS
from drov.models import ScriptedModel, ScriptedStep
from drov.models.protocol import ModelRequest, ModelResponse, ToolCall
from drov.runtime.core import _Work
from drov.storage.base import Storage
from drov.storage.records import MemoryRecord, MemoryScope, RunItem, RunRecord
from drov.storage.transaction import Transaction
from drov.tools.execution import ToolExecution
from drov.types import Failed
from tests.test_agent_runs import ALICE, BOB, final
from tests.test_context import manifests
from tests.test_tool_execution import create_run

ALLOW = PermissionPolicy(allow={"memory.remember", "memory.forget"})
SCOPE = MemoryScope(**ALICE.model_dump(), agent_name="assistant")
NOW = datetime(2026, 10, 13, tzinfo=UTC)


def memory_agent(model: ScriptedModel, **kwargs: Any) -> Agent[Any]:
    return Agent(
        name="assistant", revision="1", model=model, memory=kwargs.pop("memory", Memory()), **kwargs
    )


def call(name: str, **arguments: Any) -> ModelResponse:
    return ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[ToolCall(call_id="call", name="memory." + name, arguments=arguments)],
    )


async def seed(
    storage: Storage,
    key: str = "preference",
    content: str = "中文简洁",
    *,
    scope: MemoryScope = SCOPE,
    version: int = 1,
    deleted: bool = False,
    when: datetime = NOW,
) -> MemoryRecord:
    record = MemoryRecord(
        scope=scope,
        key=key,
        content=None if deleted else content,
        version=version,
        deleted=deleted,
        source="synthetic:fixture",
        updated_at=when,
    )
    async with storage.transaction() as tx:
        await tx.put_memory(record, expected_version=version - 1)
    return record


def backend_for(name: str, path: Path) -> Storage:
    return MemoryStorage() if name == "memory" else SQLiteStorage(path)


def dispatch(
    runtime: Runtime,
    record: RunRecord,
    name: str,
    arguments: dict[str, Any],
    operation_id: str = "op",
) -> _Work[ToolExecution]:
    descriptor = next(tool for tool in TOOLS if tool.name == "memory." + name)
    handler = MemoryDispatcher(
        storage=runtime.storage,
        policy=runtime.policy,
        authorizer=runtime.authorizer,
        validate=runtime.validate_record,
    )

    async def execute(work: _Work[ToolExecution]) -> ToolExecution:
        return await handler.execute(
            descriptor,
            canonical_json(arguments),
            work=work,
            record=record,
            operation_id=operation_id,
            deadline=asyncio.get_running_loop().time() + 2,
        )

    return runtime._spawn(runtime._reserve(), execute)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_recall_order_unicode_pagination_and_scoped_queries(
    tmp_path: Path, backend: str
) -> None:
    async with backend_for(backend, tmp_path / "memory.db") as storage:
        for number in range(110):
            await seed(storage, f"k-{number:03}", "ＡＢＣ Straße" if number >= 105 else "ordinary")
        await seed(storage, "newest", "最 新", when=NOW + timedelta(seconds=1))
        await seed(storage, "dead", version=1, deleted=True)
        await seed(
            storage,
            "private",
            "forbidden",
            scope=MemoryScope(**BOB.model_dump(), agent_name="assistant"),
        )
        async with storage.transaction() as tx:
            recent = await tx.recall_memory(SCOPE)
            assert [r.key for r in recent] == ["newest", "k-000", "k-001", "k-002", "k-003"]
            assert [
                r.key for r in await tx.recall_memory(SCOPE, query="abc STRASSE", limit=50)
            ] == [f"k-{n}" for n in range(105, 110)]
            assert [r.key for r in await tx.recall_memory(SCOPE, query="Ｋ－１０９")] == ["k-109"]
            assert (await tx.recall_memory(SCOPE, key="dead"))[0].deleted
            assert not await tx.recall_memory(SCOPE, query="forbidden")
            assert not await tx.recall_memory(SCOPE, key="absent")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_full_memory_cycle_atomic_events_tombstone_and_recreation(
    tmp_path: Path, backend: str
) -> None:
    model = ScriptedModel(
        [
            call("remember", key="preference", content="简洁", expected_version=0),
            final(),
            call("recall", key="preference"),
            final(),
            call("forget", key="preference", expected_version=1),
            final(),
            call("recall", key="preference"),
            final(),
            call("remember", key="preference", content="详细", expected_version=2),
            final(),
        ]
    )
    agent = memory_agent(model)
    async with Runtime(
        agents=[agent], storage=backend_for(backend, tmp_path / "memory.db"), policy=ALLOW
    ) as runtime:
        results = []
        for i in range(5):
            result = await runtime.run(agent, "explicit action", session_id=f"s-{i}", context=ALICE)
            assert result.status == "completed"
            results.append(result)
        async with runtime.storage.transaction() as tx:
            stored = await tx.get_memory(SCOPE, "preference")
            operations = [(await tx.items(ALICE, r.run_id, "operation"))[0] for r in results]
        assert stored is not None and stored.version == 3 and stored.content == "详细"
        assert operations[0].data["result"] == {"key": "preference", "version": 1}
        tombstone = operations[3].data["result"]
        assert isinstance(tombstone, list) and isinstance(tombstone[0], dict)
        assert (
            isinstance(tombstone, list)
            and tombstone[0]["deleted"] is True
            and tombstone[0]["content"] is None
        )
        for r in (results[0], results[2], results[4]):
            events = await runtime.storage.events(ALICE, r.run_id)
            changed = next(e for e in events if e.type == "memory.changed")
            succeeded = next(e for e in events if e.type == "tool.succeeded")
            assert changed.state_version == succeeded.state_version
            assert not any(e.type == "tool.dispatched" for e in events)
            assert (await runtime.storage.get_checkpoint(ALICE, r.run_id)).data[
                "tool_dispatch_count"
            ] == 1
        first_manifest = (await manifests(runtime, results[1].run_id, content=True))[0]
        assert first_manifest.retrieval_status == "included"
        assert next(s for s in first_manifest.sources if s.kind == "memory").version == "1"
        recall_manifest = (await manifests(runtime, results[1].run_id, content=True))[1]
        assert any(
            s.reason == "explicit_recall" and s.version == "1" for s in recall_manifest.sources
        )
        assert (await manifests(runtime, results[3].run_id))[0].retrieval_status == "empty"


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", ["user", "tenant", "agent"])
async def test_automatic_and_explicit_reads_isolate_every_scope(dimension: str) -> None:
    from drov import RunContext

    model = ScriptedModel([call("recall", query=""), final()])
    agent = memory_agent(model)
    owner = ALICE
    scope = SCOPE
    if dimension == "user":
        owner = BOB
    elif dimension == "tenant":
        owner = RunContext(tenant_id="other", user_id="alice")
    else:
        scope = MemoryScope(**ALICE.model_dump(), agent_name="other-agent")
    async with Runtime(agents=[agent]) as runtime:
        await seed(runtime.storage, content="private-content", scope=scope)
        result = await runtime.run(agent, "read", session_id="s", context=owner)
        assert result.status == "completed" and "private-content" not in canonical_json(
            [c.model_dump(mode="json") for c in model.calls]
        )
        async with runtime.storage.transaction() as tx:
            op = (await tx.items(owner, result.run_id, "operation"))[0]
        assert op.data["result"] == []


@pytest.mark.asyncio
async def test_normal_conversation_does_not_save_memory() -> None:
    model = ScriptedModel([final("a claim is not a storage fact")])
    agent = memory_agent(model)
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "hello", session_id="s", context=ALICE)
        async with runtime.storage.transaction() as tx:
            assert not await tx.recall_memory(SCOPE)
        assert not any(
            e.type == "memory.changed" for e in await runtime.storage.events(ALICE, result.run_id)
        )
        assert (await manifests(runtime, result.run_id))[0].retrieval_status == "empty"
        assert {t.name for t in model.calls[0].tools} == {t.name for t in TOOLS}
    with pytest.raises(ConfigurationError):
        await agent.run("temporary")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["deny", "ask", "business", "revoke", "cancel"])
async def test_write_permissions_and_cancellation_prevent_mutation(mode: str) -> None:
    class Auth(OwnerAuthorizer):
        calls = 0

        async def authorize(self, context: Any, action: Action, resource: Resource) -> bool:
            if action == "execute_tool":
                self.calls += 1
                if mode == "cancel" and self.calls == 2:
                    asyncio.current_task().cancel()  # type: ignore[union-attr]
                    await asyncio.sleep(0)
                return mode != "business" and not (mode == "revoke" and self.calls == 2)
            return await super().authorize(context, action, resource)

    policy = PermissionPolicy(**{mode: {"memory.remember"}}) if mode in ("deny", "ask") else ALLOW
    model = ScriptedModel(
        [call("remember", key="private-key", content="private-body", expected_version=0), final()]
    )
    agent = memory_agent(model)
    async with Runtime(agents=[agent], policy=policy, authorizer=Auth()) as runtime:
        result = await runtime.run(agent, "save", session_id="s", context=ALICE)
        async with runtime.storage.transaction() as tx:
            assert await tx.get_memory(SCOPE, "private-key") is None
        assert not any(
            e.type == "memory.changed" for e in await runtime.storage.events(ALICE, result.run_id)
        )
        assert (await runtime.storage.get_checkpoint(ALICE, result.run_id)).data.get(
            "tool_dispatch_count", 0
        ) == 0
        if mode == "ask":
            assert result.status == "suspended" and result.reason == "approval"
        elif mode == "cancel":
            assert result.status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_concurrent_cas_and_duplicate_operation_never_rewrite(
    tmp_path: Path, backend: str
) -> None:
    agent = memory_agent(ScriptedModel([]))
    async with Runtime(
        agents=[agent], storage=backend_for(backend, tmp_path / "memory.db"), policy=ALLOW
    ) as runtime:
        one, two = await create_run(runtime, agent, "one"), await create_run(runtime, agent, "two")
        scope = MemoryScope(**one.scope.model_dump(exclude={"session_id"}), agent_name="assistant")
        await seed(runtime.storage, "k", "old", scope=scope)
        args = {"key": "k", "content": "new", "expected_version": 1}
        first, second = await asyncio.gather(
            dispatch(runtime, one, "remember", args).wait(),
            dispatch(runtime, two, "remember", args).wait(),
        )
        assert sorted(r.operation.status for r in (first, second)) == ["failed_known", "succeeded"]
        failed = next(r for r in (first, second) if not r.operation.has_result)
        assert failed.operation.error_code == "memory_conflict"
        successful = first if first.operation.has_result else second
        record = one if first.operation.has_result else two
        again = await dispatch(runtime, record, "remember", args).wait()
        assert again.operation == successful.operation
        with pytest.raises(Conflict):
            await dispatch(runtime, record, "remember", args | {"content": "different"}).wait()
        async with runtime.storage.transaction() as tx:
            saved = await tx.get_memory(scope, "k")
        assert saved is not None and saved.version == 2 and saved.content == "new"
        assert (
            sum(
                e.type == "memory.changed"
                for e in await runtime.storage.events(record.scope, record.state.run_id)
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("failure", ["mutation", "operation", "step", "commit"])
async def test_failed_transaction_has_no_memory_success_or_budget(
    tmp_path: Path, backend: str, failure: str
) -> None:
    storage = backend_for(backend, tmp_path / "memory.db")
    model = ScriptedModel([call("remember", key="k", content="value", expected_version=0), final()])
    agent = memory_agent(model)
    mutated = False
    original_put, original_item, original_step, original_finish = (
        Transaction.put_memory,
        Transaction.put_item,
        Transaction.save_step,
        storage._finish,
    )

    async def put(self: Transaction, *args: Any, **kwargs: Any) -> None:
        nonlocal mutated
        await original_put(self, *args, **kwargs)
        mutated = True
        if failure == "mutation":
            raise PersistenceUnavailable("synthetic after mutation")

    async def item(self: Transaction, scope: Any, record: RunItem, **kwargs: Any) -> None:
        await original_item(self, scope, record, **kwargs)
        if failure == "operation" and record.kind == "operation" and record.state == "succeeded":
            raise PersistenceUnavailable("synthetic after result")

    async def step(self: Transaction, *args: Any, **kwargs: Any) -> Any:
        result = await original_step(self, *args, **kwargs)
        if failure == "step" and any(e.type == "memory.changed" for e in kwargs["events"]):
            raise PersistenceUnavailable("synthetic after events")
        return result

    async def finish(commit: bool) -> None:
        nonlocal mutated
        if commit and mutated and failure == "commit":
            mutated = False
            await original_finish(False)
            raise PersistenceUnavailable("synthetic commit failure with known rollback")
        await original_finish(commit)

    async with Runtime(agents=[agent], storage=storage, policy=ALLOW) as runtime:
        with (
            patch.object(Transaction, "put_memory", put),
            patch.object(Transaction, "put_item", item),
            patch.object(Transaction, "save_step", step),
            patch.object(storage, "_finish", finish),
        ):
            ref = await runtime.start(agent, "save", session_id="s", context=ALICE)
            with pytest.raises(PersistenceUnavailable):
                await runtime.wait(ref.run_id, context=ALICE)
        async with storage.transaction() as tx:
            assert await tx.get_memory(SCOPE, "k") is None
            op = (await tx.items(ALICE, ref.run_id, "operation"))[0]
        assert op.state == "prepared" and not op.data["has_result"]
        assert (await storage.get_checkpoint(ALICE, ref.run_id)).data.get(
            "tool_dispatch_count", 0
        ) == 0
        assert not any(
            e.type in ("tool.succeeded", "memory.changed")
            for e in await storage.events(ALICE, ref.run_id)
        )
        assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("failure", ["timeout", "exception", "late"])
async def test_retrieval_deadline_failure_and_late_cleanup(required: bool, failure: str) -> None:
    original = Transaction.recall_memory
    cancelled = asyncio.Event()
    reads = 0

    async def delayed(self: Transaction, *args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        reads += 1
        if failure == "exception":
            raise RuntimeError("synthetic retrieval error")
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.set()
            if failure != "late":
                raise
        return await original(self, *args, **kwargs)

    model = ScriptedModel([final()])
    agent = memory_agent(
        model,
        memory=Memory(retrieval="required" if required else "best_effort"),
        # Exception classification must not race an OS scheduling tick. The separate
        # timeout/late cases still expire before their one-second test wait.
        limits=Limits(
            memory_required_seconds=1.0 if failure == "exception" else 0.1,
            memory_best_effort_seconds=1.0 if failure == "exception" else 0.1,
        ),
    )
    async with Runtime(agents=[agent]) as runtime:
        await seed(runtime.storage, content="late-private")
        with patch.object(Transaction, "recall_memory", delayed):
            result = await runtime.run(agent, "read", session_id="s", context=ALICE)
        assert reads == 1
        context = (await manifests(runtime, result.run_id, content=True))[0]
        assert context.retrieval_status == ("failed" if failure == "exception" else "timeout")
        assert not any(s.kind == "memory" for s in context.sources)
        if failure != "exception":
            assert cancelled.is_set()
        if required:
            assert isinstance(result, Failed) and result.error.code == "memory_unavailable"
            assert not model.calls
            assert (await runtime.storage.get_checkpoint(ALICE, result.run_id)).data[
                "model_step_count"
            ] == 0
        else:
            assert (
                result.status == "completed"
                and "late-private" not in model.calls[0].model_dump_json()
            )
        assert all(not work._calls for work in runtime._works)


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted", [True, False])
async def test_revalidation_before_manifest_and_retry_snapshot_after_commit(deleted: bool) -> None:
    from drov.memory.retrieval import retrieve as original

    async def changed(*args: Any, **kwargs: Any) -> Any:
        retrieval = await original(*args, **kwargs)
        await seed(runtime.storage, content="updated", version=2, deleted=deleted)
        return retrieval

    model = ScriptedModel([ScriptedStep(error=TransientModelError("again")), final()])
    agent = memory_agent(model, limits=Limits(retry_backoff_seconds=0.001))
    async with Runtime(agents=[agent]) as runtime:
        await seed(runtime.storage, content="stale")
        with patch("drov.runtime.runs.retrieve", changed):
            result = await runtime.run(agent, "read", session_id="s", context=ALICE)
        assert result.status == "completed" and model.calls[0] == model.calls[1]
        context = (await manifests(runtime, result.run_id, content=True))[0]
        stale = next(s for s in context.sources if s.kind == "memory" and s.version == "1")
        assert not stale.included and stale.content is None
        assert stale.reason == ("memory_deleted" if deleted else "memory_version_changed")
        assert "stale" not in model.calls[0].model_dump_json()
        if not deleted:
            assert "updated" in model.calls[0].model_dump_json()
        await seed(runtime.storage, version=3, deleted=True)
        fixed = await runtime.get_context(
            result.run_id, context.model_call_id, context=ALICE, include_content=True
        )
        assert fixed == context  # forget never rewrites a saved input snapshot.


@pytest.mark.asyncio
async def test_retry_does_not_retrieve_or_replace_deleted_snapshot() -> None:
    class DeletingModel(ScriptedModel):
        async def generate(self, request: ModelRequest) -> Any:
            if not self.calls:
                await seed(runtime.storage, version=2, deleted=True)
            async for event in super().generate(request):
                yield event

    model = DeletingModel([ScriptedStep(error=TransientModelError("retry")), final(), final()])
    agent = memory_agent(model, limits=Limits(retry_backoff_seconds=0.001))
    async with Runtime(agents=[agent]) as runtime:
        await seed(runtime.storage, content="snapshot-content")
        result = await runtime.run(agent, "read", session_id="s", context=ALICE)
        assert result.status == "completed" and model.calls[0] == model.calls[1]
        assert "snapshot-content" in model.calls[1].model_dump_json()
        later = await runtime.run(agent, "read again", session_id="fresh", context=ALICE)
        assert (
            later.status == "completed"
            and "snapshot-content" not in model.calls[2].model_dump_json()
        )


@pytest.mark.asyncio
async def test_memory_subbudget_counts_envelope_and_leaves_protected_input_intact() -> None:
    model = ScriptedModel([final()])
    agent = memory_agent(
        model, context_policy=ContextPolicy(memory_fraction=0.5, memory_max_tokens=500)
    )
    async with Runtime(agents=[agent]) as runtime:
        await seed(runtime.storage, "large", "x" * 5000)
        await seed(runtime.storage, "small", "short")
        result = await runtime.run(agent, "current", session_id="s", context=ALICE)
        context = (await manifests(runtime, result.run_id, content=True))[0]
        selected = [s for s in context.sources if s.kind == "memory"]
        assert len(selected) == 2 and sum(s.included for s in selected) == 1
        assert next(s for s in selected if not s.included).reason == "memory_budget"
        request = model.calls[0]
        memory_messages = [
            m
            for m in request.messages
            if isinstance(m.content, dict) and m.content.get("type") == "drov.memory"
        ]
        assert len(memory_messages) == 1
        without = request.model_copy(
            update={"messages": [m for m in request.messages if m not in memory_messages]}
        )
        assert estimate(request) - estimate(without) <= 500
        assert request.messages[-1].content == "current" and estimate(request) <= context.budget
        metadata = (await manifests(runtime, result.run_id))[0]
        assert (
            "preference" not in metadata.model_dump_json()
            and "short" not in metadata.model_dump_json()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"key": "", "content": "x", "expected_version": 0},
        {"key": "k", "content": "x", "expected_version": -1},
        {"key": "k", "content": "x", "expected_version": True},
        {"key": "k", "content": "x", "expected_version": 0, "user_id": "bob"},
    ],
)
async def test_invalid_memory_arguments_never_write(arguments: dict[str, Any]) -> None:
    model = ScriptedModel([call("remember", **arguments), final()])
    agent = memory_agent(model)
    async with Runtime(agents=[agent], policy=ALLOW) as runtime:
        result = await runtime.run(agent, "save", session_id="s", context=ALICE)
        async with runtime.storage.transaction() as tx:
            assert not await tx.recall_memory(SCOPE)
            op = (await tx.items(ALICE, result.run_id, "operation"))[0]
        assert op.data["error_code"] == "invalid_arguments" and op.data["attempts"] == 0


@pytest.mark.asyncio
async def test_memory_byte_and_call_limits_rollback_mutation() -> None:
    model = ScriptedModel(
        [
            call("remember", key="huge", content="中文" * 100, expected_version=0),
            call("remember", key="one", content="ok", expected_version=0),
            call("remember", key="two", content="ok", expected_version=0),
        ]
    )
    agent = memory_agent(model, limits=Limits(max_memory_bytes=100, max_tool_calls=1))
    async with Runtime(agents=[agent], policy=ALLOW) as runtime:
        result = await runtime.run(agent, "save", session_id="s", context=ALICE)
        assert isinstance(result, Failed) and result.error.code == "limit_exceeded"
        async with runtime.storage.transaction() as tx:
            assert [r.key for r in await tx.recall_memory(SCOPE)] == ["one"]
        assert (await runtime.storage.get_checkpoint(ALICE, result.run_id)).data[
            "tool_dispatch_count"
        ] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fraction", "cap", "remaining"])
async def test_all_memory_excluded_reports_reason(mode: str) -> None:
    model = ScriptedModel([final()])
    policy = (
        ContextPolicy(memory_fraction=0.0001)
        if mode == "fraction"
        else ContextPolicy(memory_max_tokens=1)
        if mode == "cap"
        else ContextPolicy(memory_fraction=1)
    )
    agent = memory_agent(model, context_policy=policy, limits=Limits(max_input_tokens=4000))
    async with Runtime(agents=[agent]) as runtime:
        await seed(runtime.storage, content="x" * 5000)
        result = await runtime.run(agent, "protected", session_id="s", context=ALICE)
        assert result.status == "completed"
        context = (await manifests(runtime, result.run_id))[0]
        assert context.retrieval_status == "excluded"
        item = next(s for s in context.sources if s.kind == "memory")
        assert not item.included and item.reason == "memory_budget"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [{"key": "k", "query": "q"}, {"limit": 0}, {"limit": 51}, {"limit": True}, {"key": ""}],
)
async def test_invalid_recall_arguments_do_not_read(arguments: dict[str, Any]) -> None:
    model = ScriptedModel([call("recall", **arguments), final()])
    agent = memory_agent(model)
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "read", session_id="s", context=ALICE)
        async with runtime.storage.transaction() as tx:
            operation = (await tx.items(ALICE, result.run_id, "operation"))[0]
        assert (
            operation.data["error_code"] == "invalid_arguments" and operation.data["attempts"] == 0
        )


@pytest.mark.asyncio
async def test_explicit_recall_preview_does_not_claim_full_memory_body() -> None:
    model = ScriptedModel([call("recall", key="preference"), final()])
    agent = memory_agent(model, instructions="rules", limits=Limits(tool_preview_bytes=500))
    async with Runtime(agents=[agent]) as runtime:
        await seed(runtime.storage, content="private-body" * 500)
        result = await runtime.run(agent, "read", session_id="s", context=ALICE)
        context = (await manifests(runtime, result.run_id, content=True))[1]
        recalled = next(s for s in context.sources if s.reason == "explicit_recall")
        assert recalled.transform == "preview" and recalled.content is None
        envelope = model.calls[1].messages[-1].content
        assert isinstance(envelope, dict) and isinstance(envelope["result"], dict)
        assert envelope["result"]["truncated"] is True


@pytest.mark.asyncio
async def test_output_limit_does_not_write_or_return_success() -> None:
    model = ScriptedModel(
        [call("remember", key="k", content="value", expected_version=0), final("x")]
    )
    agent = memory_agent(model, limits=Limits(max_tool_result_bytes=5))
    async with Runtime(agents=[agent], policy=ALLOW) as runtime:
        result = await runtime.run(agent, "save", session_id="s", context=ALICE)
        async with runtime.storage.transaction() as tx:
            assert await tx.get_memory(SCOPE, "k") is None
            operation = (await tx.items(ALICE, result.run_id, "operation"))[0]
        assert (
            operation.data["error_code"] == "output_unavailable"
            and not operation.data["has_result"]
        )


@pytest.mark.asyncio
async def test_best_effort_next_step_can_retrieve_after_skipped_call() -> None:
    from drov import tool
    from tests.test_agent_runs import plan

    @tool(name="lookup", effect="read")
    def lookup() -> str:
        return "ok"

    original = Transaction.recall_memory
    reads = 0

    async def first_delayed(self: Transaction, *args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        reads += 1
        if reads == 1:
            await asyncio.sleep(1)
        return await original(self, *args, **kwargs)

    model = ScriptedModel([plan("lookup"), final()])
    agent = memory_agent(
        model,
        tools=[lookup],
        memory=Memory(retrieval="best_effort"),
        limits=Limits(memory_best_effort_seconds=0.01),
    )
    async with Runtime(agents=[agent]) as runtime:
        await seed(runtime.storage, content="next-step")
        with patch.object(Transaction, "recall_memory", first_delayed):
            result = await runtime.run(agent, "read", session_id="s", context=ALICE)
        assert result.status == "completed" and reads == 2
        assert "next-step" not in model.calls[0].model_dump_json()
        assert "next-step" in model.calls[1].model_dump_json()
        assert [m.retrieval_status for m in await manifests(runtime, result.run_id)] == [
            "timeout",
            "included",
        ]


@pytest.mark.asyncio
async def test_noncooperative_retrieval_keeps_ownership_and_no_model_call() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    original = Transaction.recall_memory

    async def stuck(self: Transaction, *args: Any, **kwargs: Any) -> Any:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return await original(self, *args, **kwargs)

    model = ScriptedModel([final()])
    agent = memory_agent(
        model,
        memory=Memory(retrieval="best_effort"),
        limits=Limits(memory_best_effort_seconds=0.01),
    )
    async with Runtime(agents=[agent]) as runtime:
        with patch.object(Transaction, "recall_memory", stuck):
            ref = await runtime.start(agent, "read", session_id="s", context=ALICE)
            work = runtime._runs.works[ref.run_id]
            await entered.wait()
            try:
                with pytest.raises(PersistenceUnavailable, match="did not stop"):
                    await work.wait()
                assert work._calls and not work.slot.released and not model.calls
            finally:
                release.set()
                calls = list(work._calls)
                assert all(isinstance(item, asyncio.Future) for item in calls)
                await asyncio.gather(*(item for item in calls if isinstance(item, asyncio.Future)))
        await asyncio.sleep(0)
        assert work.slot.released


@pytest.mark.asyncio
async def test_timeout_after_settled_commit_preserves_success_fact() -> None:
    agent = memory_agent(ScriptedModel([]), limits=Limits(tool_timeout_seconds=0.01))
    async with Runtime(agents=[agent], policy=ALLOW) as runtime:
        record = await create_run(runtime, agent)
        original_put, original_finish = Transaction.put_memory, runtime.storage._finish
        mutated = False

        async def put(self: Transaction, *args: Any, **kwargs: Any) -> None:
            nonlocal mutated
            await original_put(self, *args, **kwargs)
            mutated = True

        async def finish(commit: bool) -> None:
            nonlocal mutated
            await original_finish(commit)
            if commit and mutated:
                mutated = False
                await asyncio.sleep(1)

        with (
            patch.object(Transaction, "put_memory", put),
            patch.object(runtime.storage, "_finish", finish),
        ):
            result = await dispatch(
                runtime, record, "remember", {"key": "k", "content": "value", "expected_version": 0}
            ).wait()
        assert result.operation.status == "succeeded"
        scope = MemoryScope(
            **record.scope.model_dump(exclude={"session_id"}), agent_name="assistant"
        )
        async with runtime.storage.transaction() as tx:
            memory = await tx.get_memory(scope, "k")
        assert memory is not None and memory.version == 1


@pytest.mark.parametrize("streamed", [False, True])
def test_independent_process_example(streamed: bool) -> None:
    from examples.memory_offline import run_processes

    report = run_processes(streamed=streamed)
    assert report["checks"] == "passed"


@pytest.mark.asyncio
async def test_memory_metadata_events_and_context_never_expose_private_body() -> None:
    model = ScriptedModel(
        [call("remember", key="private-key", content="private-body", expected_version=0), final()]
    )
    agent = memory_agent(model)
    async with Runtime(agents=[agent], policy=ALLOW) as runtime:
        result = await runtime.run(agent, "save", session_id="s", context=ALICE)
        after = None
        while True:
            page = await runtime.inspect(result.run_id, context=ALICE, after=after, limit=1)
            assert (
                "private-key" not in page.model_dump_json()
                and "private-body" not in page.model_dump_json()
            )
            if not page.has_more:
                break
            after = page.next_cursor
        for manifest in await manifests(runtime, result.run_id):
            assert (
                "private-key" not in manifest.model_dump_json()
                and "private-body" not in manifest.model_dump_json()
            )


def test_reserved_memory_descriptors_cannot_be_supplied_by_application() -> None:
    from drov.tools.schema import compile_tool

    def pretend(key: str) -> str:
        return key

    with pytest.raises(ConfigurationError, match="reserved"):
        Agent(
            model=ScriptedModel([]),
            tools=[compile_tool(pretend, name="memory.recall", revision="1", effect="read")],
        )


@pytest.mark.asyncio
async def test_read_completed_after_deadline_is_still_discarded() -> None:
    original = Transaction.recall_memory

    async def blocking(self: Transaction, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        until = loop.time() + 0.02
        while loop.time() < until:
            pass
        return await original(self, *args, **kwargs)

    model = ScriptedModel([final()])
    agent = memory_agent(
        model,
        memory=Memory(retrieval="best_effort"),
        limits=Limits(memory_best_effort_seconds=0.001),
    )
    async with Runtime(agents=[agent]) as runtime:
        await seed(runtime.storage, content="too-late")
        with patch.object(Transaction, "recall_memory", blocking):
            result = await runtime.run(agent, "read", session_id="s", context=ALICE)
        assert result.status == "completed" and "too-late" not in model.calls[0].model_dump_json()
        assert (await manifests(runtime, result.run_id))[0].retrieval_status == "timeout"
