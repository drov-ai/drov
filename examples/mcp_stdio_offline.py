"""Real stdio discovery → durable approval → single call → process cleanup.

The model is a deterministic local fixture. This runs no paid service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from drov import Agent, ApprovalDecision, RunContext, Runtime, SQLiteStorage
from drov.integrations.mcp import MCPStdio
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse, ToolCall
from drov.runtime import ResourceBinding
from drov.types import ApprovalRequest, Completed, Suspended


async def main(stream: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="drov-mcp-example-") as directory:
        root = Path(directory)
        server = MCPStdio(
            namespace="local",
            revision="1",
            command=sys.executable,
            args=[
                "-I",
                "-B",
                str(Path(__file__).with_name("mcp_stdio_fixture.py")),
                "--ledger",
                str(root / "calls.jsonl"),
            ],
        )
        tools = await server.discover()
        write = next(tool for tool in tools if tool.remote_name == "write")
        model = ScriptedModel(
            [
                ModelResponse(
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCall(
                            call_id="original",
                            name=write.name,
                            arguments={"value": 7},
                        )
                    ],
                ),
                ModelResponse(finish_reason="completed", message="Synthetic MCP call completed"),
            ]
        )
        agent: Agent[str] = Agent(name="mcp-example", revision="1", model=model, tools=[write])
        actor = RunContext(tenant_id="synthetic", user_id="alice")
        async with Runtime(
            agents=[agent],
            storage=SQLiteStorage(root / "run.db"),
            dependencies={server.dependency: ResourceBinding.owned(server)},
        ) as runtime:
            if stream:
                async with runtime.stream(
                    agent, "synthetic", session_id="s", context=actor
                ) as events:
                    async for _ in events:
                        pass
                    paused = await events.result()
            else:
                paused = await runtime.run(agent, "synthetic", session_id="s", context=actor)
            assert isinstance(paused, Suspended) and isinstance(paused.pending, ApprovalRequest)
            rows = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
            assert not any(row["event"] == "call" for row in rows)
            result = await runtime.resume(
                paused.run_id,
                request_id=paused.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=actor,
            )
            assert isinstance(result, Completed)
        rows = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
        assert len([row for row in rows if row["event"] == "call"]) == 1
        assert len([row for row in rows if row["event"] == "exit"]) == 2
        print(
            json.dumps(
                {
                    "status": "completed",
                    "stream": stream,
                    "pages": 2,
                    "calls_before_approval": 0,
                    "calls_after_approval": 1,
                    "processes_closed": 2,
                    "protocol": server.protocol_version,
                    "real_model": False,
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    asyncio.run(main(parser.parse_args().stream))
