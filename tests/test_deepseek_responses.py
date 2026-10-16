"""DeepSeek dialect over the real optional client and loopback HTTP/SSE."""

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("openai")

from drov import Agent, ApprovalDecision, Memory, PermissionPolicy, Runtime, SQLiteStorage, tool
from drov.errors import ModelProtocolError, TransientModelError
from drov.models.deepseek import DeepSeekResponses
from drov.models.openai import OpenAIResponses, _name
from drov.models.protocol import ModelMessage, ModelTool, collect_response
from drov.runtime.registration import fingerprint
from evals.budget import Ledger
from evals.models import RecordedModel
from evals.wire import WireReceipt
from tests.test_openai_responses import (
    CAPS,
    CTX,
    Reply,
    call,
    completed,
    event,
    fixture,
    message,
    request,
    response,
)


def reasoning() -> dict[str, Any]:
    return {
        "type": "reasoning",
        "id": "rs_1",
        "content": [{"type": "reasoning_text", "text": "synthetic-private-continuation"}],
        "summary": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_plaintext_history_and_reasoning_never_becomes_delta(streaming: bool) -> None:
    output = [reasoning(), message()]
    body: Any = response(output)
    if streaming:
        body = [event("response.reasoning_text.delta", 1, delta="private"), *completed(body)]
    async with fixture([Reply(body)], streaming=streaming) as (server, client, _):
        adapter = DeepSeekResponses(
            model="deepseek-flash",
            capabilities=CAPS.model_copy(update={"streaming": streaming}),
            client=client,
            reasoning_effort="low",
        )
        events = [e async for e in adapter.generate(request())]
        assert len(events) == 1 and events[0].type == "complete"
        result = events[0].response
        assert result.message == "hello" and result.usage.complete
        body = server.requests[0]
        assert body["reasoning"] == {"effort": "low"}
        assert not {"include", "store", "truncation", "parallel_tool_calls"} & body.keys()
        assert result.provider_items[0]["type"] == "drov.deepseek.responses.v1"
        history = request(provider_items=result.provider_items).model_copy(
            update={"messages": [ModelMessage(role="assistant", content="hello")]}
        )
        assert adapter._body(history)["input"] == output
        assert fingerprint(Agent(name="a", revision="1", model=adapter)) != fingerprint(
            Agent(
                name="a",
                revision="1",
                model=DeepSeekResponses(
                    model="deepseek-flash",
                    capabilities=CAPS,
                    client=client,
                    reasoning_effort="none",
                ),
            )
        )
        await adapter.aclose()
        assert not client.is_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["encrypted", "empty", "summary", "unknown", "duplicate", "cut"])
async def test_incomplete_or_unsupported_plans_never_complete(bad: str) -> None:
    item = reasoning()
    output = [item, message()]
    if bad == "encrypted":
        item["encrypted_content"] = "unsupported"
        item["content"] = []
    elif bad == "empty":
        item["content"] = []
    elif bad == "summary":
        item["content"][0]["type"] = "summary_text"
    elif bad == "unknown":
        output.append({"type": "web_search_call", "id": "web"})
    elif bad == "duplicate":
        output.append(message())
    body = [] if bad == "cut" else completed(response(output))
    async with fixture([Reply(body)]) as (_, client, _):
        adapter = DeepSeekResponses(model="deepseek-flash", capabilities=CAPS, client=client)
        with pytest.raises(ModelProtocolError):
            await collect_response(adapter.generate(request()))


@pytest.mark.asyncio
async def test_dialect_cannot_replay_openai_bundle() -> None:
    async with fixture([Reply(completed())]) as (_, client, original):
        result = await collect_response(original.generate(request()))
        deepseek = DeepSeekResponses(model="deepseek-flash", capabilities=CAPS, client=client)
        with pytest.raises(ModelProtocolError):
            deepseek._body(
                request(provider_items=result.provider_items).model_copy(
                    update={"messages": [ModelMessage(role="assistant", content="hello")]}
                )
            )


@pytest.mark.asyncio
async def test_http_error_has_one_attempt_and_safe_message() -> None:
    async with fixture(
        [Reply({"error": {"message": "secret"}}, status=429, headers={"retry-after": "2"})]
    ) as (server, client, _):
        adapter = DeepSeekResponses(model="deepseek-flash", capabilities=CAPS, client=client)
        with pytest.raises(TransientModelError) as caught:
            await collect_response(adapter.generate(request()))
        assert caught.value.retry_after_seconds == 2
        assert "secret" not in str(caught.value) and len(server.requests) == 1


@pytest.mark.asyncio
async def test_original_plan_approval_and_tool_result_history() -> None:
    writes: list[int] = []

    @tool(name="apply", effect="write", approval="required")
    def apply(value: int) -> int:
        writes.append(value)
        return value

    async with fixture([Reply(), Reply(completed())]) as (server, client, _):
        adapter = DeepSeekResponses(model="deepseek-flash", capabilities=CAPS, client=client)
        wire = adapter._body(
            request(
                tools=[
                    ModelTool(
                        name=apply.name, description=apply.description, parameters=apply.parameters
                    )
                ]
            )
        )["tools"][0]
        server.replies[0].body = completed(
            response([reasoning(), call(wire["name"], '{"value":7}')])
        )
        agent: Agent[str] = Agent(
            name="deepseek-offline", revision="1", model=adapter, tools=[apply]
        )
        async with Runtime(agents=[agent]) as runtime:
            paused = await runtime.run(agent, "synthetic", session_id="s", context=CTX)
            assert paused.status == "suspended" and paused.pending and writes == []
            done = await runtime.resume(
                paused.run_id,
                request_id=paused.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=CTX,
            )
            assert done.status == "completed" and writes == [7]
            inputs = server.requests[1]["input"]
            assert inputs[1] == reasoning()
            assert inputs[2]["type"] == "function_call"
            assert json.loads(inputs[3]["output"])["result"] == 7
            assert len(server.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("deepseek", [False, True])
async def test_real_client_memory_save_and_reopened_injection(
    tmp_path: Path, deepseek: bool
) -> None:
    first = completed(
        response(
            [
                call(
                    _name("memory.remember"),
                    '{"key":"style","content":"synthetic preference","expected_version":0}',
                )
            ]
        )
    )
    async with fixture([Reply(first), Reply(completed()), Reply(completed())]) as (
        server,
        client,
        _,
    ):
        adapter_type = DeepSeekResponses if deepseek else OpenAIResponses
        adapter = adapter_type(model="offline", capabilities=CAPS, client=client)
        agent: Agent[str] = Agent(
            name="memory-client", revision="1", model=adapter, memory=Memory()
        )
        for index in (0, 1):
            async with Runtime(
                agents=[agent],
                storage=SQLiteStorage(tmp_path / "memory.db"),
                policy=PermissionPolicy(allow={"memory.remember"}),
            ) as runtime:
                result = await runtime.run(
                    agent, "save" if index == 0 else "read", session_id=str(index), context=CTX
                )
                assert result.status == "completed"
        inputs = server.requests[2]["input"]
        memory = [
            json.loads(m["content"])
            for m in inputs
            if m.get("role") == "user" and m["content"].startswith('{"record":')
        ]
        assert len(memory) == 1 and memory[0]["type"] == "drov.memory"
        assert memory[0]["record"]["content"] == "synthetic preference"
        assert memory[0]["record"]["version"] == 1
        assert len(server.requests) == 3


@pytest.mark.asyncio
async def test_complete_usage_survives_invalid_plan_without_accepting_plan(tmp_path: Path) -> None:
    bad = response([reasoning(), {"type": "web_search_call", "id": "unsupported"}])
    async with fixture([Reply(completed(bad))]) as (_, client, _):
        source = DeepSeekResponses(model="offline", capabilities=CAPS, client=client)
        ledger = Ledger(tmp_path / "ledger.json")
        ledger.create(batch_hash="fixed")
        model = RecordedModel(
            source, tmp_path / "calls", ledger=ledger, batch_hash="fixed", trial="fixture"
        )
        with pytest.raises(ModelProtocolError):
            await collect_response(model.generate(request()))
        attempts = json.loads(ledger.path.read_text())["attempts"]
        assert attempts[0]["status"] == "settled" and attempts[0]["usage"]["total_tokens"] == 14
        diagnostics = json.loads((tmp_path / "calls/model-01.error.json").read_text())
        assert diagnostics["usage_settled"] is True
        assert diagnostics["last_event"]["type"] == "response.completed"
        assert "synthetic-private-continuation" not in json.dumps(diagnostics)
        assert not (tmp_path / "calls/model-01.response.json").exists()


@pytest.mark.asyncio
async def test_truncated_next_request_never_reuses_previous_usage(tmp_path: Path) -> None:
    async with fixture([Reply(completed()), Reply([])]) as (_, client, _):
        source = DeepSeekResponses(model="offline", capabilities=CAPS, client=client)
        ledger = Ledger(tmp_path / "ledger.json")
        ledger.create(batch_hash="fixed")
        model = RecordedModel(
            source, tmp_path / "calls", ledger=ledger, batch_hash="fixed", trial="fixture"
        )
        await collect_response(model.generate(request()))
        with pytest.raises(ModelProtocolError):
            await collect_response(
                model.generate(request().model_copy(update={"model_call_id": "second"}))
            )
        attempts = json.loads(ledger.path.read_text())["attempts"]
        assert [a["status"] for a in attempts] == ["settled", "pending"]
        diagnostics = json.loads((tmp_path / "calls/model-02.error.json").read_text())
        assert diagnostics["provider"] == {} and diagnostics["usage_settled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_plan", [False, True])
async def test_wire_receipt_preserves_billing_without_accepting_early_bad_event(
    tmp_path: Path, valid_plan: bool
) -> None:
    body = completed(response([reasoning(), message()]))
    if not valid_plan:
        body.insert(0, event("response.unsupported", 1))
    receipt = WireReceipt()
    async with fixture([Reply(body)]) as (server, client, _):
        client._client.event_hooks["response"].append(receipt.capture)
        source = DeepSeekResponses(model="offline", capabilities=CAPS, client=client)
        ledger = Ledger(tmp_path / "ledger.json")
        ledger.create(batch_hash="fixed")
        model = RecordedModel(
            source,
            tmp_path / "calls",
            ledger=ledger,
            batch_hash="fixed",
            trial="fixture",
            wire_receipt=receipt,
        )
        if valid_plan:
            await collect_response(model.generate(request()))
        else:
            with pytest.raises(ModelProtocolError):
                await collect_response(model.generate(request()))
            diagnostic = (tmp_path / "calls/model-01.error.json").read_text()
            assert "synthetic-private-continuation" not in diagnostic
            assert json.loads(diagnostic)["usage_settled"] is True
            assert not (tmp_path / "calls/model-01.response.json").exists()
        attempts = json.loads(ledger.path.read_text())["attempts"]
        assert attempts[0]["status"] == "settled" and attempts[0]["usage"]["total_tokens"] == 14
        assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_wire_receipt_limit_and_missing_terminal_leave_usage_unknown(tmp_path: Path) -> None:
    receipt = WireReceipt(limit=32)
    async with fixture([Reply(completed())]) as (_, client, _):
        client._client.event_hooks["response"].append(receipt.capture)
        source = DeepSeekResponses(model="offline", capabilities=CAPS, client=client)
        ledger = Ledger(tmp_path / "ledger.json")
        ledger.create(batch_hash="fixed")
        model = RecordedModel(
            source, tmp_path / "calls", ledger=ledger, batch_hash="fixed", wire_receipt=receipt
        )
        with pytest.raises(ModelProtocolError):
            await collect_response(model.generate(request()))
        assert receipt.metadata == {}
        assert json.loads(ledger.path.read_text())["attempts"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_plaintext_with_empty_encrypted_placeholder_round_trips() -> None:
    item = reasoning() | {"encrypted_content": ""}
    async with fixture([Reply(completed(response([item, message()])))]) as (_, client, _):
        source = DeepSeekResponses(model="offline", capabilities=CAPS, client=client)
        result = await collect_response(source.generate(request()))
        assert result.message == "hello"
        history = request(provider_items=result.provider_items).model_copy(
            update={"messages": [ModelMessage(role="assistant", content="hello")]}
        )
        assert source._body(history)["input"][0] == reasoning()
        assert source._last_response_metadata["reasoning_shapes"] == [
            {"encrypted_type": "str", "encrypted_length": 0, "plaintext_parts": 1}
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("opaque", ["opaque-provider-token", 7])
async def test_plaintext_is_authoritative_and_opaque_metadata_is_not_replayed(opaque: Any) -> None:
    item = reasoning() | {"encrypted_content": opaque}
    async with fixture([Reply(completed(response([item, message()])))]) as (_, client, _):
        source = DeepSeekResponses(model="offline", capabilities=CAPS, client=client)
        if not isinstance(opaque, str):
            with pytest.raises(ModelProtocolError):
                await collect_response(source.generate(request()))
            return
        result = await collect_response(source.generate(request()))
        history = request(provider_items=result.provider_items).model_copy(
            update={"messages": [ModelMessage(role="assistant", content="hello")]}
        )
        assert source._body(history)["input"][0] == reasoning()
        assert result.message == "hello" and source._body(history)["input"][1] == message()
