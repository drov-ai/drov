"""Local workbench with deterministic examples; no model keys or external services.

Run with the server extra. Optional --docker-config explicitly enables the existing
local Docker profile. All files and application writes are synthetic local data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from drov import (
    Agent,
    DrovApp,
    HumanInput,
    Memory,
    PermissionPolicy,
    ReconciliationDecision,
    ResourceBinding,
    SQLiteStorage,
    ToolContext,
    tool,
)
from drov.artifacts import LocalArtifacts
from drov.config import ModelCapabilities
from drov.models.protocol import (
    ModelComplete,
    ModelDelta,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ToolCall,
)
from drov.sandbox import DockerSandbox, PythonSandbox


class DemoModel:
    capabilities = ModelCapabilities(text=True, tools=True, streaming=True, context_window=32768)

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.calls: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls.append(request)
        start = max(
            i
            for i, message in enumerate(request.messages)
            if message.role == "user"
            and not str(message.content).startswith("Granted artifact descriptors (file data): ")
        )
        current = request.messages[start:]
        results = [message for message in current if message.role == "tool"]
        prompt = str(current[0].content)

        def plan(name: str, args: dict[str, Any]) -> ModelResponse:
            return ModelResponse(
                finish_reason="tool_calls",
                tool_calls=[ToolCall(call_id=name, name=name, arguments=args)],
            )

        if self.kind == "research":
            if prompt == "fail":
                response = ModelResponse(finish_reason="incomplete")
            elif not results:
                response = plan("search", {"query": prompt})
            else:
                response = ModelResponse(message="cache-a vs cache-b", finish_reason="completed")
        elif self.kind == "assistant":
            response = (
                plan(
                    "memory.remember",
                    {"key": request.model_call_id, "content": "concise", "expected_version": 0},
                )
                if not results
                else ModelResponse(message="偏好已保存", finish_reason="completed")
            )
        elif not results:
            response = plan("ask", {"prompt": "请选择要处理的合成事项"})
        elif len(results) == 1:
            response = plan("uncertain" if prompt == "unknown" else "write", {"value": 7})
        else:
            last = results[-1].content
            denied = isinstance(last, dict) and last.get("error_code") == "approval_denied"
            response = ModelResponse(
                message="已拒绝，未执行写入" if denied else "事项处理完成",
                finish_reason="completed",
            )
        if prompt == "slow":
            for _ in range(30):
                await asyncio.sleep(0.1)
                yield ModelDelta(text="·")
        yield ModelComplete(response=response)

    async def aclose(self) -> None:
        pass


class FileDemoModel:
    """Reuse the recorded CSV plan per current turn, never a shared script position."""

    capabilities = ModelCapabilities(text=True, tools=True, streaming=True, context_window=32768)

    def __init__(self) -> None:
        self._factory = runpy.run_path(str(Path(__file__).with_name("sandbox_offline.py")))[
            "FixtureModel"
        ]

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        first = max(
            i
            for i, item in enumerate(request.messages)
            if item.role == "user"
            and not str(item.content).startswith("Granted artifact descriptors (file data): ")
        )
        previous = [item for item in request.messages[first:] if item.role == "tool"]
        if previous and (
            not isinstance(previous[-1].content, dict)
            or previous[-1].content.get("status") != "succeeded"
        ):
            yield ModelComplete(
                response=ModelResponse(message="代码计划未执行成功", finish_reason="completed")
            )
            return
        current = request.model_copy(update={"messages": request.messages[first:]})
        model = self._factory(chart=True)
        if previous:
            model.calls.append(current)
        async for event in model.generate(current):
            yield event

    async def aclose(self) -> None:
        pass


def demo_agents() -> tuple[dict[str, Agent[Any]], list[int]]:
    writes: list[int] = []

    @tool(name="search", effect="read")
    async def search(query: str) -> list[str]:
        return ["cache-a: single process", "cache-b: shared service", query]

    @tool(name="write", effect="write")
    async def write(value: int) -> str:
        writes.append(value)
        return "written"

    async def verify(ctx: ToolContext) -> ReconciliationDecision:
        return ReconciliationDecision(resolution="applied", evidence="synthetic local write log")

    @tool(name="uncertain", effect="write", verifier=verify)
    async def uncertain(value: int) -> str:
        writes.append(value)
        raise TimeoutError("synthetic lost reply")

    return {
        "research": Agent(
            name="research", revision="1", model=DemoModel("research"), tools=[search]
        ),
        "assistant": Agent(
            name="assistant", revision="1", model=DemoModel("assistant"), memory=Memory()
        ),
        "operations": Agent(
            name="operations",
            revision="1",
            model=DemoModel("operations"),
            tools=[HumanInput(name="ask", input_type=str), write, uncertain],
        ),
    }, writes


def make_app(port: int, directory: Path, docker_config: Path | None = None) -> DrovApp:
    agents, _ = demo_agents()
    dependencies: dict[str, object] = {}
    files = LocalArtifacts(directory=directory / "bytes") if os.name == "posix" else None
    if docker_config is not None:
        # This explicit, non-secret checkout profile contains no API credentials.
        config = json.loads(docker_config.read_text())
        agents["csv-sandbox"] = Agent(
            name="csv-sandbox",
            revision="1",
            model=FileDemoModel(),
            tools=[PythonSandbox(backend="code")],
        )
        dependencies["code"] = ResourceBinding.owned(
            DockerSandbox(
                image=config["analysis_preparation"]["image_id"],
                docker=config["docker_cli"],
                host=config["docker_host"],
            )
        )
    return DrovApp(
        agents=agents,
        local=True,
        port=port,
        storage=SQLiteStorage(directory / "state.db"),
        artifacts=files,
        dependencies=dependencies,
        policy=PermissionPolicy(allow={"memory.remember"}),
    )


async def serve(options: argparse.Namespace) -> None:
    app = make_app(options.port, Path(options.directory), options.docker_config)

    async def connection() -> None:
        async with asyncio.timeout(10):
            while app.local_token is None:  # noqa: ASYNC110 -- bounded local startup
                await asyncio.sleep(0.01)
        # Explicit ephemeral local UI credential, not a provider key. Never put it in the URL.
        print(f"Workbench: http://127.0.0.1:{options.port}/workbench/", flush=True)
        print("Local startup token (paste into the login form): " + app.local_token, flush=True)
        print("Deterministic offline examples; no real model inference.", flush=True)

    display = asyncio.create_task(connection())
    try:
        await app.serve()
    finally:
        display.cancel()
        await asyncio.gather(display, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--directory", type=Path, default=Path(".drov/workbench-demo"))
    parser.add_argument("--docker-config", type=Path)
    asyncio.run(serve(parser.parse_args()))
