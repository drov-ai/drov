"""Synthetic official-SDK stdio server; no model, credentials or network required."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server


def record(path: Path | None, event: str, **values: Any) -> None:
    if path is not None:
        with path.open("a", encoding="utf-8") as output:
            output.write(json.dumps({"event": event, "pid": os.getpid(), **values}) + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--mode", default="normal")
    options = parser.parse_args()
    record(
        options.ledger,
        "start",
        inherited_test_secret="DROV_MCP_TEST_SECRET" in os.environ,
        systemroot_present=bool(os.environ.get("SYSTEMROOT")),
    )

    if options.mode == "stubborn":
        if os.name == "posix":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        child = subprocess.Popen(  # noqa: ASYNC220 - deliberate real process-tree fixture
            [
                sys.executable,
                "-c",
                "import signal,time,os; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN) if os.name == 'posix' else None; "
                "time.sleep(120)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        record(options.ledger, "child", child_pid=child.pid)

    def mode() -> str:
        return (
            options.control.read_text().strip()
            if options.control and options.control.exists()
            else str(options.mode)
        )

    async def listing(context: Any, params: Any) -> types.ListToolsResult:
        cursor = params.cursor if params else None
        record(options.ledger, "list", cursor=cursor)
        current = mode()
        if current == "list-hang":
            await asyncio.Event().wait()
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "value": {"type": "integer", "minimum": 1},
                "label": {"type": "string", "default": "optional"},
            },
            "required": ["value"],
        }
        if current == "schema-change":
            schema["properties"]["value"] = {"type": "string"}
        if current == "unsupported-schema":
            schema["$ref"] = "https://invalid.example/schema"
        tool = types.Tool(
            name="echo/name" if cursor is None else "write",
            description="Synthetic MCP fixture"
            if current != "description-change"
            else "Changed description",
            input_schema=schema,
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
            annotations=types.ToolAnnotations(read_only_hint=True, idempotent_hint=True),
        )
        if current == "task-required":
            tool.execution = types.ToolExecution(task_support="required")
        if current == "duplicate":
            tool.name = "echo/name"
        return types.ListToolsResult(
            tools=[tool],
            next_cursor="page2" if cursor is None or current == "cursor-cycle" else None,
        )

    async def call(context: Any, params: Any) -> types.CallToolResult:
        record(options.ledger, "call", name=params.name, arguments=params.arguments)
        current = mode()
        if current == "hang":
            await asyncio.Event().wait()
        if current == "exit":
            os._exit(19)
        if current == "error":
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="synthetic-private-error")],
                is_error=True,
            )
        if current == "rpc-error":
            raise ValueError("synthetic-private-error")
        if current == "sampling":
            await context.session.create_message(messages=[], max_tokens=1)
        if current == "bad-content":
            return types.CallToolResult(
                content=[types.ImageContent(type="image", data="eA==", mime_type="image/png")],
                structured_content={"value": 7},
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="synthetic value")],
            structured_content={
                "value": "bad" if current == "bad-output" else params.arguments["value"]
            },
        )

    server: Server[Any] = Server(
        "Drov MCPFixture",
        on_list_tools=None if options.mode == "no-tools" else listing,
        on_call_tool=None if options.mode == "no-tools" else call,
    )
    if options.mode == "init-hang":
        await asyncio.Event().wait()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
    if options.mode == "stubborn":
        await asyncio.Event().wait()
    record(options.ledger, "exit")


if __name__ == "__main__":
    asyncio.run(main())
