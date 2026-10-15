"""Real official client against loopback HTTP/SSE; no external model or credential."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("openai")
import httpx2
from openai import AsyncOpenAI

from drov import Agent, Limits, ModelCapabilities, RunContext, Runtime, tool
from drov.errors import (
    ConfigurationError,
    ModelProtocolError,
    ModelProviderError,
    ShutdownIncomplete,
    TransientModelError,
    UnsupportedSchema,
)
from drov.models.openai import OpenAIResponses
from drov.models.protocol import (
    ModelComplete,
    ModelDelta,
    ModelEvent,
    ModelMessage,
    ModelRequest,
    ModelTool,
    collect_response,
)
from drov.runtime.registration import fingerprint
from drov.sandbox import PythonSandbox

CAPS = ModelCapabilities(
    text=True, tools=True, structured_output=True, streaming=True, context_window=32768
)
CTX = RunContext(tenant_id="offline", user_id="test")


def request(**kwargs: Any) -> ModelRequest:
    return ModelRequest(
        model_call_id="offline", messages=[ModelMessage(role="user", content="hello")], **kwargs
    )


def message(text: str = "hello", **extra: Any) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
        **extra,
    }


def response(output: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": "resp_offline",
        "object": "response",
        "created_at": 1,
        "model": "offline",
        "status": "completed",
        "output": [message()] if output is None else output,
        "usage": {
            "input_tokens": 11,
            "output_tokens": 3,
            "total_tokens": 14,
            "input_tokens_details": {"cached_tokens": 4},
        },
        **extra,
    }


def event(kind: str, sequence: int, **kwargs: Any) -> dict[str, Any]:
    return {"type": kind, "sequence_number": sequence, **kwargs}


def completed(value: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [event("response.completed", 10, response=value or response())]


def call(name: str, arguments: str = "{}", call_id: str = "call_1") -> dict[str, Any]:
    return {
        "type": "function_call",
        "id": "fc_" + call_id,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


@dataclass
class Reply:
    body: Any = None
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    stall: bool = False
    drop: bool = False


class HTTPFixture:
    def __init__(self, replies: list[Reply]) -> None:
        self.replies = replies
        self.requests: list[dict[str, Any]] = []
        self.tasks: set[asyncio.Task[None]] = set()
        self.received = asyncio.Event()
        self.disconnected = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        try:
            header = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
            length = next(
                int(line.split(":", 1)[1])
                for line in header.split("\r\n")
                if line.lower().startswith("content-length:")
            )
            self.requests.append(json.loads(await reader.readexactly(length)))
            self.received.set()
            reply = self.replies.pop(0)
            if reply.drop:
                return
            if isinstance(reply.body, list):
                body = b"".join(
                    ("event: " + item["type"] + "\ndata: " + json.dumps(item) + "\n\n").encode()
                    for item in reply.body
                )
                content_type = "text/event-stream"
            else:
                body = json.dumps(reply.body).encode()
                content_type = "application/json"
            headers = {
                "Content-Type": content_type,
                "Connection": "close",
                "Content-Length": str(len(body) + (100 if reply.stall else 0)),
                **reply.headers,
            }
            writer.write(
                (
                    f"HTTP/1.1 {reply.status} Fixture\r\n"
                    + "".join(f"{key}: {value}\r\n" for key, value in headers.items())
                    + "\r\n"
                ).encode()
                + body
            )
            await writer.drain()
            if reply.stall:
                await reader.read()
                self.disconnected.set()
        except (ConnectionError, asyncio.IncompleteReadError):
            self.disconnected.set()
        finally:
            writer.close()
            await writer.wait_closed()
            self.tasks.discard(task)


@asynccontextmanager
async def fixture(
    replies: list[Reply],
    *,
    streaming: bool = True,
    request_timeout: float = 5,
) -> AsyncIterator[tuple[HTTPFixture, AsyncOpenAI, OpenAIResponses]]:
    server_state = HTTPFixture(replies)
    server = await asyncio.start_server(server_state.handle, "127.0.0.1", 0)
    assert server.sockets
    port = server.sockets[0].getsockname()[1]
    async with httpx2.AsyncClient(trust_env=False) as http:
        client = AsyncOpenAI(
            api_key="drov-offline-test",
            base_url=f"http://127.0.0.1:{port}/v1",
            http_client=http,
            max_retries=2,
            timeout=request_timeout,
        )
        adapter = OpenAIResponses(
            model="offline-model",
            capabilities=CAPS.model_copy(update={"streaming": streaming}),
            client=client,
        )
        try:
            yield server_state, client, adapter
        finally:
            await adapter.aclose()
            await client.close()
            server.close()
            await server.wait_closed()
            for task in tuple(server_state.tasks):
                task.cancel()
            await asyncio.gather(*server_state.tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [True, False])
async def test_http_text_usage_and_controls(streaming: bool) -> None:
    body = completed() if streaming else response()
    async with fixture([Reply(body)], streaming=streaming) as (server, client, adapter):
        result = await collect_response(adapter.generate(request()))
        assert result.message == "hello" and result.finish_reason == "completed"
        assert result.usage.input_tokens == 11 and result.usage.cached_input_tokens == 4
        assert result.usage.complete and result.usage.cost_amount is None
        sent = server.requests[0]
        assert sent["model"] == "offline-model" and sent["stream"] is streaming
        assert sent["store"] is False and sent["parallel_tool_calls"] is False
        assert sent["truncation"] == "disabled" and sent["max_output_tokens"] == 2048
        assert sent["include"] == ["reasoning.encrypted_content"]
        assert "previous_response_id" not in sent and "conversation" not in sent
        assert client.max_retries == 2  # borrowed source is unchanged
        await adapter.aclose()
        assert not client.is_closed()


@pytest.mark.asyncio
async def test_tool_schema_mapping_and_ordered_protocol_replay() -> None:
    sandbox = PythonSandbox(backend="code")
    tools = [
        ModelTool(name=sandbox.name, description=sandbox.description, parameters=sandbox.parameters)
    ]
    # First capture the actual public name mapping, then return a call through HTTP.
    async with fixture([Reply(completed())]) as (server, _, adapter):
        await collect_response(adapter.generate(request(tools=tools)))
        wire = server.requests[0]["tools"][0]["name"]
    assert len(wire) <= 64 and "." not in wire and wire != "sandbox.run_python"
    opaque = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "synthetic"}
    raw = [opaque, message("plan", phase="commentary"), call(wire, '{"code":"print(1)"}')]
    async with fixture([Reply(completed(response(raw))), Reply(completed())]) as (
        server,
        _,
        adapter,
    ):
        first = await collect_response(adapter.generate(request(tools=tools)))
        assert first.tool_calls[0].name == "sandbox.run_python"
        assert first.tool_calls[0].arguments == {"code": "print(1)"}
        bundle = first.provider_items[0]
        followup = ModelRequest(
            model_call_id="second",
            tools=tools,
            provider_items=first.provider_items,
            messages=[
                ModelMessage(role="system", content="rules"),
                ModelMessage(role="user", content="first"),
                ModelMessage(role="assistant", content=bundle["assistant"]),
                ModelMessage(role="tool", call_id="call_1", content={"result": "1"}),
                ModelMessage(role="user", content="next"),
            ],
        )
        await collect_response(adapter.generate(followup))
        assert server.requests[1]["input"] == [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "first"},
            *raw,
            {"type": "function_call_output", "call_id": "call_1", "output": '{"result":"1"}'},
            {"role": "user", "content": "next"},
        ]
        parameters = server.requests[0]["tools"][0]["parameters"]
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])
        assert server.requests[0]["tools"][0]["strict"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 409, 422, 429, 500, 503])
async def test_http_status_is_sanitized_and_never_retried_by_sdk(status: int) -> None:
    transient = status in (408, 409, 429) or status >= 500
    async with fixture(
        [
            Reply(
                {"error": {"message": "PRIVATE", "type": "invalid"}},
                status,
                {"Retry-After": "0.02"},
            )
        ]
    ) as (server, _, adapter):
        with pytest.raises(TransientModelError if transient else ModelProviderError) as caught:
            await collect_response(adapter.generate(request()))
        assert "PRIVATE" not in str(caught.value) and len(server.requests) == 1
        if transient:
            assert isinstance(caught.value, TransientModelError)
            assert caught.value.retry_after_seconds == 0.02


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', "[]", "{"])
async def test_invalid_arguments_never_commit(arguments: str) -> None:
    tool_def = ModelTool(
        name="query",
        description="",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    async with fixture([Reply(completed()), Reply()]) as (server, _, adapter):
        await collect_response(adapter.generate(request(tools=[tool_def])))
        wire = server.requests[0]["tools"][0]["name"]
        server.replies[0].body = completed(response([call(wire, arguments)]))
        with pytest.raises(ModelProtocolError):
            await collect_response(adapter.generate(request(tools=[tool_def])))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        [],
        [event("response.output_text.delta", 0, delta="partial")],
        completed() + [event("response.output_text.delta", 11, delta="late")],
        completed() + completed(),
        [event("response.future", 0)],
        [event("response.output_item.added", 0, item={"type": "web_search_call"})],
        [
            event("response.output_text.delta", 1, delta="a"),
            event("response.output_text.delta", 0, delta="b"),
        ],
        [event("response.completed", 0, response=response(status="incomplete"))],
        [event("response.failed", 0, response=response(status="failed"))],
        [event("error", 0, error={"message": "PRIVATE"})],
    ],
)
async def test_stream_must_end_cleanly_with_one_supported_complete(
    body: list[dict[str, Any]],
) -> None:
    async with fixture([Reply(body)]) as (_, _, adapter):
        seen: list[ModelEvent] = []
        with pytest.raises((ModelProtocolError, ModelProviderError)) as caught:
            async for item in adapter.generate(request()):
                seen.append(item)
        assert "PRIVATE" not in str(caught.value)
        assert not any(isinstance(item, ModelComplete) for item in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,reason",
    [
        (response(status="incomplete"), "incomplete"),
        (response([message(content=[{"type": "refusal", "refusal": "no"}])]), "refusal"),
        (response([{"type": "web_search_call", "id": "ws"}]), "error"),
        (response([{"type": "reasoning", "id": "rs", "summary": []}]), "error"),
        (response([message(status="in_progress")]), "error"),
        (response([message(), message()]), "error"),
        (response([message(), call("unknown")]), "error"),
        (response([], usage=None), "error"),
        (response(usage={"input_tokens": -1}), "error"),
    ],
)
async def test_terminal_validation(value: dict[str, Any], reason: str) -> None:
    kind = "response.incomplete" if reason == "incomplete" else "response.completed"
    async with fixture([Reply([event(kind, 0, response=value)])]) as (_, _, adapter):
        if reason == "error":
            with pytest.raises((ModelProtocolError, ModelProviderError)):
                await collect_response(adapter.generate(request()))
        else:
            result = await collect_response(adapter.generate(request()))
            assert result.finish_reason == reason and result.tool_calls == []


@pytest.mark.asyncio
async def test_missing_usage_stays_unknown_and_structured_output_is_strict() -> None:
    schema = {
        "type": "object",
        "properties": {"x": {"type": "integer", "default": 1}},
        "required": [],
        "additionalProperties": False,
    }
    async with fixture([Reply(completed(response(usage=None)))]) as (server, _, adapter):
        result = await collect_response(adapter.generate(request(output_schema=schema)))
        assert result.usage.input_tokens is None and not result.usage.complete
        sent = server.requests[0]["text"]["format"]
        assert sent["strict"] and sent["schema"]["required"] == ["x"]
        assert "default" not in sent["schema"]["properties"]["x"]
        assert "Default: 1" in sent["schema"]["properties"]["x"]["description"]


@pytest.mark.asyncio
async def test_final_phase_projects_final_text_but_preserves_all_message_fields() -> None:
    output = [
        message("working", phase="commentary"),
        message('{"total":36}', id="msg_final", phase="final_answer"),
    ]
    async with fixture([Reply(completed(response(output)))]) as (_, _, adapter):
        result = await collect_response(adapter.generate(request()))
        assert result.message == '{"total":36}'
        assert result.provider_items[0]["output"] == output


@pytest.mark.asyncio
async def test_provider_budget_drops_whole_history_group_and_keeps_protocol_private() -> None:
    opaque = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "synthetic-private-protocol" * 4000,
    }
    async with fixture([Reply(completed(response([opaque, message()]))), Reply(completed())]) as (
        server,
        _,
        adapter,
    ):
        agent: Agent[str] = Agent(
            model=adapter, name="a", revision="1", limits=Limits(max_input_tokens=5000)
        )
        async with Runtime(agents=[agent]) as runtime:
            first = await runtime.run(agent, "old", session_id="s", context=CTX)
            assert first.status == "completed"
            second = await runtime.run(agent, "current", session_id="s", context=CTX)
            assert second.status == "completed"
            assert server.requests[1]["input"] == [{"role": "user", "content": "current"}]
            inspection = await runtime.inspect(second.run_id, context=CTX, include_content=True)
            assert "synthetic-private-protocol" not in inspection.model_dump_json()
            async with runtime.storage.transaction() as tx:
                inputs = await tx.items(CTX, second.run_id, "model_input")
                saved = await tx.items(CTX, first.run_id, "model_response")
            input_data: Any = inputs[0].data["request"]
            saved_data: Any = saved[0].data["response"]
            assert input_data["provider_items"] == []
            assert saved_data["provider_items"][0]["output"][0] == opaque


@pytest.mark.asyncio
async def test_runtime_tool_loop_history_and_single_retry_owner() -> None:
    count = 0

    @tool(name="lookup.item", effect="read")
    def lookup() -> str:
        nonlocal count
        count += 1
        return "found"

    replies = [
        Reply({"error": {"message": "PRIVATE"}}, 429),
        Reply(),
        Reply(completed()),
        Reply(completed()),
    ]
    async with fixture(replies) as (server, _, adapter):
        # Capture mapping without depending on the private hash helper.
        wire = adapter._body(
            request(
                tools=[
                    ModelTool(
                        name=lookup.name,
                        description=lookup.description,
                        parameters=lookup.parameters,
                    )
                ]
            )
        )["tools"][0]["name"]
        server.replies[1].body = completed(response([call(wire)]))
        agent: Agent[str] = Agent(
            model=adapter,
            name="test",
            revision="1",
            tools=[lookup],
            limits=Limits(retry_backoff_seconds=0.001),
        )
        async with Runtime(agents=[agent]) as runtime:
            first = await runtime.run(agent, "find", session_id="s", context=CTX)
            assert first.status == "completed" and first.output == "hello"
            assert count == 1 and len(server.requests) == 3
            assert server.requests[0] == server.requests[1]  # exact saved retry input
            assert server.requests[2]["input"][-1]["type"] == "function_call_output"
            second = await runtime.run(agent, "again", session_id="s", context=CTX)
            assert second.status == "completed" and count == 1
            kinds = [item.get("type", item.get("role")) for item in server.requests[3]["input"]]
            assert kinds == ["user", "function_call", "function_call_output", "message", "user"]
            inspection = await runtime.inspect(first.run_id, context=CTX, include_content=True)
            assert "PRIVATE" not in inspection.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "drop", "cancel"])
async def test_network_and_cancel_release_stream_and_no_implicit_retry(mode: str) -> None:
    reply = Reply(
        [event("response.output_text.delta", 0, delta="partial")],
        stall=mode != "drop",
        drop=mode == "drop",
    )
    async with fixture([reply], request_timeout=0.05 if mode == "timeout" else 5) as (
        server,
        _,
        adapter,
    ):
        task = asyncio.create_task(collect_response(adapter.generate(request())))
        await asyncio.wait_for(server.received.wait(), 2)
        if mode == "cancel":
            with pytest.raises(ShutdownIncomplete):
                await adapter.aclose()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(TransientModelError):
                await task
        assert len(server.requests) == 1
        if mode != "drop":
            await asyncio.wait_for(server.disconnected.wait(), 2)


def test_configuration_is_inert_fingerprinted_and_rejects_unsupported_schema() -> None:
    adapter = OpenAIResponses(model="explicit", capabilities=CAPS)
    assert adapter._client is None
    base: Agent[str] = Agent(model=adapter, name="a", revision="1")
    other: Agent[str] = Agent(
        model=OpenAIResponses(model="other", capabilities=CAPS), name="a", revision="1"
    )
    assert fingerprint(base).config_hash != fingerprint(other).config_hash

    with pytest.raises(UnsupportedSchema):
        adapter.validate_configuration(
            tools=[
                ModelTool(
                    name="dynamic",
                    description="",
                    parameters={"type": "object", "additionalProperties": {"type": "string"}},
                )
            ],
            output_schema=None,
        )
    assert adapter._client is None
    with pytest.raises(ConfigurationError):
        OpenAIResponses(model="", capabilities=CAPS)
    with pytest.raises(ConfigurationError):
        Agent(
            model=OpenAIResponses(
                model="x", capabilities=ModelCapabilities(text=True, context_window=32768)
            ),
            tools=[PythonSandbox(backend="code")],
        )


@pytest.mark.asyncio
async def test_protocol_bundle_mismatch_is_rejected_before_http() -> None:
    async with fixture([]) as (server, _, adapter):
        for value in (
            ModelRequest(
                model_call_id="a", messages=[ModelMessage(role="assistant", content="hi")]
            ),
            request(provider_items=[{"type": "unknown"}]),
        ):
            with pytest.raises(ModelProtocolError):
                await collect_response(adapter.generate(value))
        assert not server.requests


@pytest.mark.asyncio
async def test_complete_tool_plan_waits_for_eof_and_keeps_multiple_call_order() -> None:
    tool_def = ModelTool(
        name="search",
        description="",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    )
    async with fixture([Reply()]) as (server, _, adapter):
        wire = adapter._body(request(tools=[tool_def]))["tools"][0]["name"]
        server.replies[0].body = [
            event("response.output_text.delta", 0, delta="checking"),
            event(
                "response.function_call_arguments.delta",
                1,
                delta="{",
                item_id="fc_call_1",
                output_index=0,
            ),
            *completed(response([call(wire), call(wire, call_id="call_2")])),
        ]
        events = [item async for item in adapter.generate(request(tools=[tool_def]))]
        assert isinstance(events[0], ModelDelta)
        assert len(events) == 2 and isinstance(events[-1], ModelComplete)
        assert [c.call_id for c in events[-1].response.tool_calls] == ["call_1", "call_2"]


@pytest.mark.asyncio
async def test_duplicate_calls_and_truncated_tool_plans_do_not_execute() -> None:
    executions = 0

    @tool(name="lookup", effect="read")
    def lookup() -> str:
        nonlocal executions
        executions += 1
        return "found"

    async with fixture([Reply(), Reply()]) as (server, _, adapter):
        wire = adapter._body(
            request(
                tools=[
                    ModelTool(
                        name=lookup.name,
                        description=lookup.description,
                        parameters=lookup.parameters,
                    )
                ]
            )
        )["tools"][0]["name"]
        server.replies[0].body = completed(response([call(wire), call(wire)]))
        server.replies[1].body = [
            event(
                "response.function_call_arguments.delta",
                0,
                delta="{}",
                item_id="fc_1",
                output_index=0,
            )
        ]
        agent: Agent[str] = Agent(model=adapter, name="a", revision="1", tools=[lookup])
        async with Runtime(agents=[agent]) as runtime:
            for session in ("duplicate", "truncated"):
                outcome = await runtime.run(agent, "find", session_id=session, context=CTX)
                assert outcome.status == "failed" and executions == 0
                inspection = await runtime.inspect(
                    outcome.run_id, context=CTX, include_content=True
                )
                assert not any(e.type == "tool.dispatched" for e in inspection.events)
        assert len(server.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,expected",
    [
        ({"Retry-After": "0.03"}, 0.03),
        ({"retry-after-ms": "50"}, 0.05),
        ({"Retry-After": "NaN"}, None),
        ({"Retry-After": "-1"}, None),
        ({"Retry-After": "invalid"}, None),
        ({"Retry-After": format_datetime(datetime.now(UTC) + timedelta(hours=1))}, "date"),
    ],
)
async def test_retry_after_header_mapping(
    header: dict[str, str], expected: float | str | None
) -> None:
    async with fixture([Reply({}, 429, header)]) as (_, _, adapter):
        with pytest.raises(TransientModelError) as caught:
            await collect_response(adapter.generate(request()))
        delay = caught.value.retry_after_seconds
        if expected == "date":
            assert delay is not None and 3500 < delay <= 3600
        else:
            assert delay == expected


@pytest.mark.asyncio
async def test_runtime_respects_retry_after_within_budget() -> None:
    async with fixture([Reply({}, 429, {"Retry-After": "0.03"}), Reply(completed())]) as (
        server,
        _,
        adapter,
    ):
        agent: Agent[str] = Agent(
            model=adapter, name="a", revision="1", limits=Limits(retry_backoff_seconds=0.001)
        )
        sleeps: list[float] = []
        original = asyncio.sleep

        async def observe(seconds: float) -> None:
            sleeps.append(seconds)
            await original(0)

        async with Runtime(agents=[agent]) as runtime:
            with patch("drov.runtime.runs.asyncio.sleep", observe):
                outcome = await runtime.run(agent, "hello", session_id="s", context=CTX)
        assert outcome.status == "completed" and 0.03 in sleeps
        assert len(server.requests) == 2


@pytest.mark.asyncio
async def test_owned_client_is_lazy_and_closed_once() -> None:
    async with fixture([Reply(completed())]) as (server, client, _):
        # The constructor factory uses only the fixture client, never process credentials.
        with patch("openai.AsyncOpenAI", return_value=client) as factory:
            owned = OpenAIResponses(model="owned", capabilities=CAPS)
            assert not factory.called
            await collect_response(owned.generate(request()))
            factory.assert_called_once_with(max_retries=0)
            await owned.aclose()
            await owned.aclose()
            assert client.is_closed() and len(server.requests) == 1
            with pytest.raises(ConfigurationError):
                await collect_response(owned.generate(request()))


@pytest.mark.asyncio
async def test_closing_generator_after_provisional_delta_closes_http() -> None:
    async with fixture(
        [Reply([event("response.output_text.delta", 0, delta="partial")], stall=True)]
    ) as (server, _, adapter):
        iterator = adapter.generate(request())
        assert isinstance(await anext(iterator), ModelDelta)
        await iterator.aclose()
        await asyncio.wait_for(server.disconnected.wait(), 2)
        assert adapter._active == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_offline_adapter_preserves_sandbox_approval_and_artifact_result(
    tmp_path: Path, stream: bool
) -> None:
    import os

    if os.name != "posix":
        pytest.skip("Artifact sandbox integration requires POSIX private files")
    from drov import ApprovalDecision
    from tests.test_sandbox_runtime import BackendFixture, runtime_for

    sandbox = PythonSandbox(backend="code")
    async with fixture([Reply(), Reply(completed())]) as (server, _, adapter):
        wire = adapter._body(
            request(
                tools=[
                    ModelTool(
                        name=sandbox.name,
                        description=sandbox.description,
                        parameters=sandbox.parameters,
                    )
                ]
            )
        )["tools"][0]["name"]
        server.replies[0].body = completed(
            response([call(wire, '{"code":"print(60)","outputs":["result.csv"]}')])
        )
        agent: Agent[str] = Agent(
            model=adapter, name="sandbox-fixture", revision="1", tools=[sandbox]
        )
        backend = BackendFixture()
        from tests.test_sandbox_runtime import CONTEXT

        async with runtime_for(tmp_path, agent, backend) as runtime:
            if stream:
                async with runtime.stream(
                    agent, "code", session_id="s", context=CONTEXT
                ) as subscription:
                    async for _ in subscription:
                        pass
                    first = await subscription.result()
            else:
                first = await runtime.run(agent, "code", session_id="s", context=CONTEXT)
            assert first.status == "suspended" and first.pending is not None
            assert not backend.executions
            await runtime.respond(
                first.run_id,
                request_id=first.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=CONTEXT,
            )
            outcome = await runtime.wait(first.run_id, context=CONTEXT)
            assert outcome.status == "completed" and len(backend.executions) == 1
            assert len(server.requests) == 2 and len(backend.destroyed) == 1
            sent = json.loads(server.requests[1]["input"][-1]["output"])
            result = sent["result"]
            ref = result["artifacts"][0]
            assert (
                await runtime.read_artifact(ref["artifact_id"], context=CONTEXT) == b"value\n60\n"
            )
