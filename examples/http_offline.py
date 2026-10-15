"""Real loopback HTTP/SSE for three scripted scenarios; no credentials or paid calls."""

import argparse
import asyncio
import http.client
import json
import socket
from typing import Any

from drov import Agent, DrovApp, HumanInput, Memory, PermissionPolicy, tool
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse, ToolCall


def fixtures() -> tuple[dict[str, Agent[Any]], list[int]]:
    writes: list[int] = []

    @tool(name="search", effect="read")
    async def search(query: str) -> list[str]:
        return ["cache-a: single process", "cache-b: shared service"] if query == "cache" else []

    @tool(name="write", effect="write")
    async def write(value: int) -> str:
        writes.append(value)
        return "written"

    def plan(name: str, args: dict[str, Any]) -> ModelResponse:
        return ModelResponse(
            finish_reason="tool_calls",
            tool_calls=[ToolCall(call_id=name, name=name, arguments=args)],
        )

    def final(text: str) -> ModelResponse:
        return ModelResponse(message=text, finish_reason="completed")

    agents: dict[str, Agent[Any]] = {
        "research": Agent(
            name="research",
            revision="1",
            tools=[search],
            model=ScriptedModel([plan("search", {"query": "cache"}), final("cache-a vs cache-b")]),
        ),
        "assistant": Agent(
            name="assistant",
            revision="1",
            memory=Memory(),
            model=ScriptedModel(
                [
                    plan(
                        "memory.remember",
                        {"key": "style", "content": "concise", "expected_version": 0},
                    ),
                    final("saved"),
                ]
            ),
        ),
        "operations": Agent(
            name="operations",
            revision="1",
            tools=[HumanInput(name="ask", input_type=str), write],
            model=ScriptedModel(
                [plan("ask", {"prompt": "choose item"}), plan("write", {"value": 7}), final("done")]
            ),
        ),
    }
    return agents, writes


def request(port: int, token: str, method: str, path: str, value: object = None) -> tuple[int, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        body = json.dumps(value) if value is not None else None
        connection.request(
            method,
            path,
            body,
            {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = response.read()
        return response.status, payload.decode() if path.endswith("/events") else json.loads(
            payload
        )
    finally:
        connection.close()


async def check(*, streamed: bool) -> dict[str, object]:
    import uvicorn

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    agents, writes = fixtures()
    app = DrovApp(
        agents=agents, local=True, port=port, policy=PermissionPolicy(allow={"memory.remember"})
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            lifespan="on",
            log_config=None,
            access_log=False,
            proxy_headers=False,
            timeout_graceful_shutdown=2,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))

    async def call(method: str, path: str, value: object = None) -> Any:
        assert app.local_token is not None
        status, result = await asyncio.to_thread(
            request, port, app.local_token, method, path, value
        )
        assert 200 <= status < 300, (status, result)
        return result

    try:
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 -- bounded real server observation
                if task.done():
                    await task
                    raise AssertionError("Server startup failed")
                await asyncio.sleep(0.01)
        outcomes = []
        for name in agents:
            ref = await call(
                "POST",
                "/v1/runs",
                {"agent_name": name, "revision": "1", "session_id": name, "input": "synthetic"},
            )
            run_id = ref["run_id"]
            while True:
                if streamed:
                    events = await call("GET", f"/v1/runs/{run_id}/events")
                    assert "id: " in events
                async with asyncio.timeout(5):
                    while True:
                        page = await call("GET", f"/v1/runs/{run_id}?include_content=true")
                        snapshot = page["snapshot"]
                        if snapshot["status"] in ("completed", "failed", "suspended", "cancelled"):
                            break
                        await asyncio.sleep(0.01)
                if snapshot["status"] != "suspended":
                    assert snapshot["status"] == "completed"
                    outcomes.append({"agent": name, "output": snapshot["outcome"]["output"]})
                    break
                pending = snapshot["pending"]
                response = (
                    {"kind": "input", "value": "chosen"}
                    if pending["kind"] == "input"
                    else {"kind": "approval", "approved": True}
                )
                assert writes == []
                receipt = await call(
                    "POST",
                    f"/v1/runs/{run_id}/responses",
                    {"request_id": pending["request_id"], "response": response},
                )
                repeat = await call(
                    "POST",
                    f"/v1/runs/{run_id}/responses",
                    {"request_id": pending["request_id"], "response": response},
                )
                assert repeat == receipt
        assert writes == [7]
        assert len((await call("GET", "/v1/sessions"))["items"]) == 3
        return {
            "real_model": False,
            "transport": "loopback SSE" if streamed else "loopback HTTP",
            "scenarios": outcomes,
            "writes": writes,
        }
    finally:
        server.should_exit = True
        async with asyncio.timeout(10):
            await task
        listener.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check(streamed=args.stream))))
