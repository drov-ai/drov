"""Official OpenAI client against a local deterministic HTTP/SSE fixture.

Requires drov[openai]. Never reads credentials or calls an external model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx2
from openai import AsyncOpenAI

from drov import Agent, ModelCapabilities, RunContext, Runtime, tool
from drov.models.deepseek import DeepSeekResponses
from drov.models.openai import OpenAIResponses


@dataclass
class Report:
    total: int


async def run(*, stream: bool, deepseek: bool = False) -> None:
    requests: list[dict[str, Any]] = []
    handlers: set[asyncio.Task[None]] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        handlers.add(task)
        try:
            headers = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
            length = next(
                int(line.split(":", 1)[1])
                for line in headers.split("\r\n")
                if line.lower().startswith("content-length:")
            )
            body = json.loads(await reader.readexactly(length))
            requests.append(body)
            assert body["text"]["format"]["strict"] is True
            if deepseek:
                assert body["reasoning"] == {"effort": "none"} and "include" not in body
            else:
                assert body["store"] is False
            if len(requests) == 1:
                output = [
                    {
                        "type": "function_call",
                        "id": "fc_local",
                        "call_id": "local",
                        "name": body["tools"][0]["name"],
                        "arguments": '{"value":6}',
                        "status": "completed",
                    }
                ]
            else:
                assert len(requests) == 2
                assert json.loads(body["input"][-1]["output"])["result"] == 36
                output = [
                    {
                        "type": "message",
                        "id": "msg_local",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": '{"total":36}', "annotations": []}
                        ],
                    }
                ]
            if deepseek:
                output.insert(
                    0,
                    {
                        "type": "reasoning",
                        "id": f"rs_{len(requests)}",
                        "content": [{"type": "reasoning_text", "text": "synthetic continuation"}],
                    },
                )
            result = {
                "id": "resp_local",
                "object": "response",
                "created_at": 1,
                "model": "offline-fixture",
                "status": "completed",
                "output": output,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "input_tokens_details": {"cached_tokens": 0},
                },
            }
            event = {"type": "response.completed", "sequence_number": 0, "response": result}
            encoded = ("event: response.completed\ndata: " + json.dumps(event) + "\n\n").encode()
            writer.write(
                (
                    f"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n"
                ).encode()
                + encoded
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    assert server.sockets
    port = server.sockets[0].getsockname()[1]
    try:
        async with httpx2.AsyncClient(trust_env=False) as http:
            async with AsyncOpenAI(
                api_key="drov-offline-fixture",
                base_url=f"http://127.0.0.1:{port}/v1",
                http_client=http,
            ) as client:
                adapter_type = DeepSeekResponses if deepseek else OpenAIResponses
                adapter = adapter_type(
                    model="offline-fixture",
                    client=client,
                    capabilities=ModelCapabilities(
                        text=True,
                        tools=True,
                        structured_output=True,
                        streaming=True,
                        context_window=32768,
                    ),
                )

                @tool(name="math.square", effect="read")
                def square(value: int) -> int:
                    return value * value

                agent: Agent[Report] = Agent(
                    model=adapter, name="offline", revision="1", tools=[square], output_type=Report
                )
                context = RunContext(tenant_id="fixture", user_id="local")
                async with Runtime(agents=[agent], owned_models=[adapter]) as runtime:
                    if stream:
                        async with runtime.stream(
                            agent, "square six", session_id="s", context=context
                        ) as subscription:
                            async for _ in subscription:
                                pass
                            outcome = await subscription.result()
                    else:
                        outcome = await runtime.run(
                            agent, "square six", session_id="s", context=context
                        )
                    assert outcome.status == "completed" and outcome.output == Report(total=36)
                    assert len(requests) == 2
                    print(
                        json.dumps(
                            {
                                "mode": "stream" if stream else "sdk",
                                "total": 36,
                                "local_http_requests": len(requests),
                                "real_model": False,
                                "dialect": "deepseek" if deepseek else "openai",
                            }
                        )
                    )
    finally:
        server.close()
        await server.wait_closed()
        await asyncio.gather(*handlers)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--deepseek", action="store_true")
    options = parser.parse_args()
    asyncio.run(run(stream=options.stream, deepseek=options.deepseek))
