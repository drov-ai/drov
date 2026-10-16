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
