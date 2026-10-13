"""T-07 contract integration: budgets, complete turns, immutable inputs and projections."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from drov import (
    Limits,
    MemoryStorage,
    ModelCapabilities,
    RunContext,
    Runtime,
    SQLiteStorage,
    tool,
)
from drov._json import canonical_json
from drov.context.assembly import digest, estimate
from drov.errors import (
    ConfigurationError,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
    TransientModelError,
)
from drov.models import ScriptedModel, ScriptedStep
from drov.models.protocol import ModelRequest, ModelResponse, ToolCall
from drov.runtime.runs import RunController
from drov.storage.records import RunItem
from drov.storage.transaction import Transaction
from drov.types import ContextManifest, Failed, Usage
from tests.test_agent_runs import ALICE, BOB, definition, final, plan


async def manifests(
    runtime: Runtime, run_id: str, *, content: bool = False
) -> list[ContextManifest]:
    page = await runtime.inspect(run_id, context=ALICE)
    return [
        await runtime.get_context(
            run_id, item.model_call_id, context=ALICE, include_content=content
        )
        for item in page.context_manifests
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_inputs_and_manifests_are_committed_before_dispatch(
    tmp_path: Path, backend: str
) -> None:
    storage = MemoryStorage() if backend == "memory" else SQLiteStorage(tmp_path / "context.db")

    async def verify_saved(request: ModelRequest) -> None:
        async with runtime.storage.transaction() as tx:
            runs = await tx.runs((await tx.sessions(ALICE))[0].scope)
            run_id = runs[0].state.run_id
            saved = await tx.get_item(ALICE, run_id, "model_input", request.model_call_id)
            context_item = await tx.get_item(ALICE, run_id, "context", request.model_call_id)
        manifest = ContextManifest.model_validate(context_item.data["manifest"])
        assert saved.data["request"] == request.model_dump(mode="json")
        assert manifest.input_hash == digest(saved.data["request"])
        assert manifest.estimated_tokens == estimate(request) <= manifest.budget
        assert manifest.retrieval_status == "disabled"
        assert {s.kind for s in manifest.sources} == {"instructions", "input", "schema"}
        assert all(s.included and s.reason == "protected" for s in manifest.sources)

    class CheckingModel(ScriptedModel):
        async def generate(self, request: ModelRequest) -> Any:
            await verify_saved(request)
            async for event in super().generate(request):
                yield event

    model = CheckingModel(
        [final(usage=Usage(input_tokens=17, output_tokens=2, total_tokens=19, complete=True))]
    )
    agent = definition(model, instructions="Keep these rules.")
    async with Runtime(agents=[agent], storage=storage) as runtime:
        result = await runtime.run(agent, "current request", session_id="session", context=ALICE)
        assert result.status == "completed"
        summary = (await runtime.inspect(result.run_id, context=ALICE)).context_manifests[0]
        assert summary.sources == [] and not summary.content_visible
        manifest = (await manifests(runtime, result.run_id, content=True))[0]
        assert manifest.reported_usage.input_tokens == 17
        async with runtime.storage.transaction() as tx:
            saved = await tx.get_item(ALICE, result.run_id, "context", manifest.model_call_id)
        assert (
            ContextManifest.model_validate(saved.data["manifest"]).reported_usage.input_tokens
            is None
        )
        assert saved.version == 1  # Reported usage is joined, never rewrites the assembly snapshot.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "window, explicit, reserve, expected",
    [
        (32768, None, 2048, 16384),
        (4096, None, 512, 3584),
        (4096, 1400, 512, 1400),
        (None, 1400, 512, 1400),
    ],
)
async def test_window_budget_defaults_and_explicit_limit(
    window: int | None, explicit: int | None, reserve: int, expected: int
) -> None:
    model = ScriptedModel([final()])
    model.capabilities = ModelCapabilities(text=True, context_window=window)
    agent = definition(
        model, limits=Limits(max_input_tokens=explicit, output_reserve_tokens=reserve)
    )
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        manifest = (await manifests(runtime, result.run_id))[0]
        assert result.status == "completed" and manifest.budget == expected
        assert manifest.estimated_tokens == estimate(model.calls[0])


def test_unknown_window_requires_explicit_budget() -> None:
    model = ScriptedModel([final()])
    model.capabilities = ModelCapabilities(text=True)
    with pytest.raises(ConfigurationError):
        definition(model)


@pytest.mark.asyncio
async def test_effective_output_reserve_can_leave_zero_input_budget() -> None:
    model = ScriptedModel([final()])
    model.capabilities = ModelCapabilities(text=True, context_window=1000)
    agent = definition(model, limits=Limits(output_reserve_tokens=100))
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(
            agent, "one", session_id="one", context=ALICE, limits=Limits(output_reserve_tokens=1100)
        )
        assert isinstance(result, Failed) and result.error.code == "context_overflow"
        assert not model.calls
        manifest = (await manifests(runtime, result.run_id))[0]
        assert manifest.budget == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["instructions", "input", "tool_schema", "output_schema", "protocol"]
)
async def test_all_protected_parts_count_toward_overflow(kind: str) -> None:
    @tool(name="lookup", description="schema-text-" * 300, effect="read")
    def lookup() -> str:
        return "ok"

    from dataclasses import dataclass

    @dataclass
    class LargeSchema:
        extremely_long_property_name_with_schema_overhead: str

    model = ScriptedModel([plan("lookup"), final()])
    kwargs: dict[str, Any] = {"limits": Limits(max_input_tokens=500)}
    input_text = "one"
    if kind == "instructions":
        kwargs["instructions"] = "rules" * 200
    elif kind == "input":
        input_text = "input" * 200
    elif kind == "tool_schema":
        kwargs["tools"] = [lookup]
    elif kind == "output_schema":
        kwargs["output_type"] = LargeSchema
    else:
        # The request JSON and explicit per-message/request overhead count even for short text.
        kwargs["limits"] = Limits(max_input_tokens=300)
    agent = definition(model, **kwargs)
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, input_text, session_id="one", context=ALICE)
        assert isinstance(result, Failed) and result.error.code == "context_overflow"
        assert not model.calls
        manifest = (await manifests(runtime, result.run_id, content=True))[0]
        assert manifest.estimated_tokens > manifest.budget
        async with runtime.storage.transaction() as tx:
            item = await tx.get_item(ALICE, result.run_id, "model_input", manifest.input_ref)
        assert item.state == "rejected"
        checkpoint = await runtime.storage.get_checkpoint(ALICE, result.run_id)
        assert checkpoint.data["model_step_count"] == 0
        assert "model_attempt_count" not in checkpoint.data


@pytest.mark.asyncio
@pytest.mark.parametrize("cap,budget", [(16384, 16384), (2048, 3000), (4096, 2400)])
async def test_large_result_preview_preserves_reference_and_original(cap: int, budget: int) -> None:
    original = {"source_id": "synthetic-doc", "body": '中文\\"引用' * 5000}

    @tool(name="lookup", effect="read")
    def lookup() -> dict[str, str]:
        return original

    model = ScriptedModel([plan("lookup"), final("Used a marked excerpt.")])
    agent = definition(
        model,
        tools=[lookup],
        instructions="Respect marked previews.",
        limits=Limits(max_input_tokens=budget, tool_preview_bytes=cap),
    )
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "find sources", session_id="one", context=ALICE)
        assert result.status == "completed" and len(model.calls) == 2
        message = model.calls[1].messages[-1]
        assert isinstance(message.content, dict)
        preview = message.content["result"]
        assert isinstance(preview, dict) and preview["type"] == "drov.tool_preview"
        assert preview["truncated"] is True
        assert preview["sha256"] == digest(original)
        assert preview["original_bytes"] == len(canonical_json(original).encode("utf-8"))
        assert len(canonical_json(preview).encode("utf-8")) <= cap
        assert canonical_json(original).startswith(preview["text"])  # type: ignore[arg-type]
        manifest = (await manifests(runtime, result.run_id, content=True))[1]
        selected = next(s for s in manifest.sources if s.kind == "tool")
        assert selected.included and selected.transform == "preview"
        assert (
            selected.id == preview["source_ref"] and selected.version == preview["source_version"]
        )
        assert manifest.estimated_tokens == estimate(model.calls[1]) <= budget
        assert (
            model.calls[1].messages[0].role == "system"
        )  # Inserting rules keeps preview indices correct.
        async with runtime.storage.transaction() as tx:
            operation = (await tx.items(ALICE, result.run_id, "operation"))[0]
        assert operation.data["result"] == original and operation.data["has_result"] is True
        assert [m.role for m in model.calls[1].messages] == ["system", "user", "assistant", "tool"]


@pytest.mark.asyncio
async def test_preview_metadata_must_fit_and_is_never_silently_omitted() -> None:
    @tool(name="lookup", effect="read")
    def lookup() -> str:
        return "x" * 5000

    model = ScriptedModel([plan("lookup"), final()])
    agent = definition(model, tools=[lookup], limits=Limits(tool_preview_bytes=10))
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        assert isinstance(result, Failed) and result.error.code == "context_overflow"
        assert len(model.calls) == 1
        manifest = (await manifests(runtime, result.run_id, content=True))[1]
        assert next(s for s in manifest.sources if s.kind == "tool").transform == "preview"
        checkpoint = await runtime.storage.get_checkpoint(ALICE, result.run_id)
        assert checkpoint.data["model_attempt_count"] == 1


@pytest.mark.asyncio
async def test_complete_old_turn_and_provider_items_excluded_together() -> None:
    @tool(name="lookup", effect="read")
    def lookup() -> str:
        return "old-result"

    old_plan = plan("lookup").model_copy(
        update={"provider_items": [{"raw": "private-provider-" * 400}]}
    )
    model = ScriptedModel([old_plan, final("old answer"), final("new answer")])
    agent = definition(model, tools=[lookup], limits=Limits(max_input_tokens=16384))
    async with Runtime(agents=[agent]) as runtime:
        old = await runtime.run(agent, "old request", session_id="one", context=ALICE)
        assert old.status == "completed"
        new = await runtime.run(
            agent,
            "new request",
            session_id="one",
            context=ALICE,
            limits=Limits(max_input_tokens=1600),
        )
        assert new.status == "completed"
        assert [m.content for m in model.calls[-1].messages] == ["new request"]
        assert model.calls[-1].provider_items == []
        manifest = (await manifests(runtime, new.run_id, content=True))[0]
        removed = [s for s in manifest.sources if s.id.startswith(old.run_id + ":")]
        assert len(removed) == 5 and {s.kind for s in removed} == {"history", "tool", "provider"}
        assert all(
            not s.included and s.transform == "excluded" and s.content is None for s in removed
        )
        assert all(s.reason == "history_budget" for s in removed)
        assert "private-provider" not in manifest.model_dump_json()


@pytest.mark.asyncio
async def test_recent_history_selected_and_oversized_middle_turn_skipped() -> None:
    model = ScriptedModel([final("old answer"), final("x" * 4000), final("recent answer"), final()])
    agent = definition(model, instructions="rules")
    async with Runtime(agents=[agent]) as runtime:
        old = await runtime.run(agent, "old", session_id="one", context=ALICE)
        large = await runtime.run(agent, "large", session_id="one", context=ALICE)
        recent = await runtime.run(agent, "recent", session_id="one", context=ALICE)
        result = await runtime.run(
            agent, "current", session_id="one", context=ALICE, limits=Limits(max_input_tokens=1000)
        )
        assert result.status == "completed"
        assert [m.content for m in model.calls[-1].messages] == [
            "rules",
            "old",
            "old answer",
            "recent",
            "recent answer",
            "current",
        ]
        manifest = (await manifests(runtime, result.run_id))[0]
        for run, included in [(old, True), (large, False), (recent, True)]:
            sources = [s for s in manifest.sources if s.id.startswith(run.run_id + ":")]
            assert len(sources) == 2 and all(s.included is included for s in sources)
        assert all(s.content is None for s in manifest.sources)


@pytest.mark.asyncio
async def test_retry_uses_one_manifest_same_hash_without_reassembly() -> None:
    model = ScriptedModel([ScriptedStep(error=TransientModelError("retry")), final()])
    agent = definition(model, limits=Limits(retry_backoff_seconds=0.001))
    async with Runtime(agents=[agent]) as runtime:
        original = RunController.history
        assemblies = 0

        async def counted(self: RunController, *args: Any, **kwargs: Any) -> Any:
            nonlocal assemblies
            assemblies += 1
            return await original(self, *args, **kwargs)

        with patch.object(RunController, "history", counted):
            result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        assert result.status == "completed" and assemblies == 1
        assert model.calls[0] == model.calls[1]
        contexts = await manifests(runtime, result.run_id)
        assert len(contexts) == 1 and contexts[0].input_hash == digest(
            model.calls[0].model_dump(mode="json")
        )
        async with runtime.storage.transaction() as tx:
            assert len(await tx.items(ALICE, result.run_id, "context")) == 1


@pytest.mark.asyncio
async def test_context_write_failure_rolls_back_input_and_prevents_model_call() -> None:
    model = ScriptedModel([final()])
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        original = Transaction.put_item

        async def fail_context(
            self: Transaction, scope: RunContext, item: RunItem, **kwargs: Any
        ) -> None:
            if item.kind == "context":
                raise PersistenceUnavailable("synthetic context failure")
            await original(self, scope, item, **kwargs)

        with patch.object(Transaction, "put_item", fail_context):
            ref = await runtime.start(agent, "one", session_id="one", context=ALICE)
            with pytest.raises(PersistenceUnavailable):
                await runtime.wait(ref.run_id, context=ALICE)
        assert not model.calls
        async with runtime.storage.transaction() as tx:
            assert not await tx.items(ALICE, ref.run_id, "context")
            assert not await tx.items(ALICE, ref.run_id, "model_input")
        assert (await runtime.storage.get_checkpoint(ALICE, ref.run_id)).data.get(
            "model_step_count", 0
        ) == 0
        assert all(
            e.type != "context.prepared" for e in await runtime.storage.events(ALICE, ref.run_id)
        )


@pytest.mark.asyncio
async def test_metadata_projection_and_reauthorization_after_storage_read() -> None:
    from drov.auth import Action, OwnerAuthorizer, Resource

    class Auth(OwnerAuthorizer):
        content = True

        async def authorize(self, context: RunContext, action: Action, resource: Resource) -> bool:
            return (
                self.content
                if action == "view_content"
                else await super().authorize(context, action, resource)
            )

    model = ScriptedModel([final("private-output")])
    agent = definition(model, instructions="private-rules")
    auth = Auth()
    async with Runtime(agents=[agent], authorizer=auth) as runtime:
        result = await runtime.run(agent, "private-input", session_id="one", context=ALICE)
        manifest = (await manifests(runtime, result.run_id))[0]
        assert "private-" not in manifest.model_dump_json()
        auth.content = False
        assert (
            await runtime.get_context(result.run_id, manifest.model_call_id, context=ALICE)
        ).sources
        with pytest.raises(PermissionDenied):
            await runtime.get_context(
                result.run_id, manifest.model_call_id, context=ALICE, include_content=True
            )
        with pytest.raises(RecordNotFound):
            await runtime.get_context(result.run_id, manifest.model_call_id, context=BOB)
        auth.content = True
        original = RunController.context_record

        async def revoke(self: RunController, *args: Any, **kwargs: Any) -> ContextManifest:
            value = await original(self, *args, **kwargs)
            auth.content = False
            return value

        with patch.object(RunController, "context_record", revoke):
            with pytest.raises(PermissionDenied):
                await runtime.get_context(
                    result.run_id, manifest.model_call_id, context=ALICE, include_content=True
                )


@pytest.mark.asyncio
async def test_provider_items_count_but_body_is_never_projected() -> None:
    @tool(name="lookup", effect="read")
    def lookup() -> str:
        return "ok"

    response = plan("lookup").model_copy(update={"provider_items": [{"raw": "private-provider"}]})
    model = ScriptedModel([response, final()])
    agent = definition(model, tools=[lookup])
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        manifest = (await manifests(runtime, result.run_id, content=True))[1]
        provider = next(s for s in manifest.sources if s.kind == "provider")
        assert provider.included and provider.content is None
        assert provider.estimated_tokens == len(
            canonical_json(response.provider_items[0]).encode("utf-8")
        )
        assert model.calls[1].provider_items == response.provider_items
        assert manifest.estimated_tokens == estimate(model.calls[1])
        assert "private-provider" not in manifest.model_dump_json()


@pytest.mark.asyncio
async def test_inspection_context_index_follows_event_pagination() -> None:
    @tool(name="lookup", effect="read")
    def lookup() -> str:
        return "ok"

    model = ScriptedModel([plan("lookup"), final()])
    agent = definition(model, tools=[lookup])
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        after = None
        seen = []
        while True:
            page = await runtime.inspect(
                result.run_id, context=ALICE, after=after, limit=1, include_content=True
            )
            assert len(page.context_manifests) <= 1
            assert all(not m.content_visible and not m.sources for m in page.context_manifests)
            expected = [
                e.data["model_call_id"] for e in page.events if e.type == "context.prepared"
            ]
            assert [m.model_call_id for m in page.context_manifests] == expected
            seen.extend(expected)
            if not page.has_more:
                break
            after = page.next_cursor
        assert seen == [request.model_call_id for request in model.calls]


@pytest.mark.asyncio
async def test_sqlite_reopen_preserves_manifests_and_detaches_returned_bodies(
    tmp_path: Path,
) -> None:
    path = tmp_path / "saved.db"
    model = ScriptedModel([final()])
    agent = definition(model)
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        result = await runtime.run(agent, "private-input", session_id="one", context=ALICE)
        before = (await manifests(runtime, result.run_id, content=True))[0]
        original_json = before.model_dump_json()
        before.sources.clear()
        saved = (await manifests(runtime, result.run_id, content=True))[0]
        assert saved.model_dump_json() == original_json
    async with Runtime(agents=[agent], storage=SQLiteStorage(path)) as runtime:
        after = (await manifests(runtime, result.run_id, content=True))[0]
        assert after.model_dump_json() == original_json
        assert len(model.calls) == 1
        with pytest.raises(RecordNotFound):
            await runtime.get_context(result.run_id, "unknown-call", context=ALICE)


@pytest.mark.asyncio
async def test_context_hash_mismatch_fails_closed() -> None:
    model = ScriptedModel([final()])
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        call_id = model.calls[0].model_call_id
        async with runtime.storage.transaction() as tx:
            item = await tx.get_item(ALICE, result.run_id, "model_input", call_id)
            await tx.put_item(
                ALICE,
                item.model_copy(update={"version": 2, "data": item.data | {"request": {}}}),
                expected_version=1,
            )
        with pytest.raises(PersistenceUnavailable):
            await runtime.get_context(result.run_id, call_id, context=ALICE, include_content=True)


@pytest.mark.asyncio
async def test_history_pagination_keeps_complete_exclusion_inventory() -> None:
    model = ScriptedModel([final("answer") for _ in range(103)])
    agent = definition(model, limits=Limits(max_input_tokens=1300))
    async with Runtime(agents=[agent]) as runtime:
        results = []
        for number in range(103):
            results.append(
                await runtime.run(agent, f"request-{number}", session_id="one", context=ALICE)
            )
        assert all(result.status == "completed" for result in results)
        manifest = (await manifests(runtime, results[-1].run_id))[0]
        prior_inputs = [
            s for s in manifest.sources if s.kind == "history" and s.id.endswith(":input")
        ]
        assert len(prior_inputs) == 102
        assert {s.id for s in prior_inputs} == {f"{result.run_id}:input" for result in results[:-1]}
        assert any(not s.included for s in prior_inputs)
        request = model.calls[-1]
        assert request.messages[-1].content == "request-102"
        assert sum(m.content == "request-102" for m in request.messages) == 1
        assert manifest.estimated_tokens == estimate(request) <= 1300


@pytest.mark.asyncio
async def test_current_provider_protocol_cannot_be_cropped_to_fit() -> None:
    @tool(name="lookup", effect="read")
    def lookup() -> str:
        return "ok"

    response = plan("lookup").model_copy(update={"provider_items": [{"raw": "p" * 10000}]})
    model = ScriptedModel([response, final()])
    agent = definition(model, tools=[lookup], limits=Limits(max_input_tokens=1800))
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        assert isinstance(result, Failed) and result.error.code == "context_overflow"
        assert len(model.calls) == 1
        manifest = (await manifests(runtime, result.run_id))[1]
        assert next(s for s in manifest.sources if s.kind == "provider").included
        assert manifest.estimated_tokens > manifest.budget
        assert (await runtime.storage.get_checkpoint(ALICE, result.run_id)).data[
            "model_attempt_count"
        ] == 1


@pytest.mark.asyncio
async def test_multiple_tools_shrink_together_without_orphaning_calls() -> None:
    @tool(name="lookup", effect="read")
    def lookup() -> str:
        return "可见来源正文" * 5000

    response = ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[
            ToolCall(call_id="first", name="lookup", arguments={}),
            ToolCall(call_id="second", name="lookup", arguments={}),
        ],
    )
    model = ScriptedModel([response, final()])
    agent = definition(model, tools=[lookup], limits=Limits(max_input_tokens=3000))
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        assert result.status == "completed"
        request = model.calls[1]
        assert [m.call_id for m in request.messages if m.role == "tool"] == ["first", "second"]
        manifest = (await manifests(runtime, result.run_id, content=True))[1]
        selected = [s for s in manifest.sources if s.kind == "tool"]
        assert len(selected) == 2 and all(s.included and s.transform == "preview" for s in selected)
        assert len({s.id for s in selected}) == 2
        assert manifest.estimated_tokens <= 3000


@pytest.mark.asyncio
async def test_contexts_never_mix_sessions_and_reject_wrong_call_reference() -> None:
    model = ScriptedModel([final("first"), final("second")])
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        one = await runtime.run(agent, "private-one", session_id="one", context=ALICE)
        two = await runtime.run(agent, "private-two", session_id="two", context=ALICE)
        manifest = (await manifests(runtime, two.run_id, content=True))[0]
        assert "private-one" not in manifest.model_dump_json()
        assert "private-one" not in model.calls[1].model_dump_json()
        with pytest.raises(RecordNotFound):
            await runtime.get_context(two.run_id, model.calls[0].model_call_id, context=ALICE)
        assert all(not s.id.startswith(one.run_id) for s in manifest.sources)


@pytest.mark.asyncio
async def test_incompatible_history_payload_does_not_dispatch_or_rewrite_fact() -> None:
    model = ScriptedModel([final(), final()])
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        one = await runtime.run(agent, "one", session_id="one", context=ALICE)
        async with runtime.storage.transaction() as tx:
            item = await tx.get_item(
                ALICE, one.run_id, "model_response", model.calls[0].model_call_id
            )
            incompatible = item.model_copy(
                update={"version": 2, "data": item.data | {"schema_version": 99}}
            )
            await tx.put_item(ALICE, incompatible, expected_version=1)
        ref = await runtime.start(agent, "two", session_id="one", context=ALICE)
        with pytest.raises(PersistenceUnavailable):
            await runtime.wait(ref.run_id, context=ALICE)
        assert len(model.calls) == 1
        async with runtime.storage.transaction() as tx:
            saved = await tx.get_item(
                ALICE, one.run_id, "model_response", model.calls[0].model_call_id
            )
            assert saved == incompatible
            assert not await tx.items(ALICE, ref.run_id, "context")
            assert not await tx.items(ALICE, ref.run_id, "model_input")


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_preview_example_cites_visible_source_without_claiming_full_read(
    streamed: bool,
) -> None:
    from examples.context_preview_offline import inspect_preview

    report = await inspect_preview(streamed=streamed)
    assert report.source_id == "synthetic-guide" and report.full_document_read is False


@pytest.mark.asyncio
async def test_index_does_not_load_detailed_context_or_input() -> None:
    model = ScriptedModel([final()])
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        result = await runtime.run(agent, "one", session_id="one", context=ALICE)
        with patch.object(
            RunController, "context_record", side_effect=AssertionError("detail read")
        ):
            page = await runtime.inspect(result.run_id, context=ALICE)
        assert len(page.context_manifests) == 1
        assert page.context_manifests[0].input_hash == digest(
            model.calls[0].model_dump(mode="json")
        )
        assert not page.context_manifests[0].sources


@pytest.mark.asyncio
async def test_t06_event_without_manifest_keeps_legacy_state_readable() -> None:
    from drov.storage.records import EventDraft, SessionScope, StartRequest

    model = ScriptedModel([final()])
    agent = definition(model)
    async with Runtime(agents=[agent]) as runtime:
        registration = runtime.registration("research", "1")
        ref = await runtime.storage.start(
            StartRequest(
                scope=SessionScope(**ALICE.model_dump(), session_id="legacy"),
                agent_name=registration.name,
                revision=registration.revision,
                config_hash=registration.config_hash,
                input="old input",
            )
        )
        await runtime._runs.step(
            ref.run_id,
            ALICE,
            [EventDraft(type="context.prepared", data={"model_call_id": "old-call"})],
        )
        page = await runtime.inspect(ref.run_id, context=ALICE)
        assert page.snapshot.status == "running" and not page.context_manifests
        assert page.events[-1].type == "context.prepared"
        with pytest.raises(RecordNotFound):
            await runtime.get_context(ref.run_id, "old-call", context=ALICE)
        assert not model.calls
