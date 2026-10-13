"""Approval/input in four fresh processes per case, using synthetic read-only items."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from drov import (
    Agent,
    ApprovalDecision,
    HumanInput,
    InputResponse,
    RunContext,
    Runtime,
    SQLiteStorage,
    ToolContext,
    tool,
)
from drov.models import ScriptedModel, ScriptedStep
from drov.models.protocol import ModelComplete, ModelRequest, ModelResponse, ToolCall
from drov.types import ApprovalRequest, InputRequest, Suspended

CALLER = RunContext(tenant_id="synthetic", user_id="alice")


@dataclass
class ItemChoice:
    item_id: str


def plan(name: str, arguments: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[ToolCall(call_id="call-0", name=name, arguments=arguments)],
    )


async def child(options: argparse.Namespace) -> dict[str, Any]:
    directory = Path(options.directory)
    counter = directory / "calls.json"

    @tool(name="read_item", effect="read", approval="required")
    def read_item(item_id: str, context: ToolContext) -> str:
        assert context.user_id == "alice" and item_id == "item-01"
        calls = json.loads(counter.read_text()) if counter.exists() else []
        calls.append({"operation_id": context.operation_id, "item_id": item_id})
        # Fixture instrumentation only; the synthetic item data is read-only.
        counter.write_text(json.dumps(calls))
        return "item-01: pending"

    def check_answer(request: ModelRequest) -> None:
        content = request.messages[-1].content
        assert isinstance(content, dict) and content["result"] == {"item_id": "item-01"}

    def check_result(request: ModelRequest) -> None:
        content = request.messages[-1].content
        assert isinstance(content, dict)
        if options.denied:
            assert (
                content["status"] == "not_executed" and content["error_code"] == "approval_denied"
            )
        else:
            assert content["status"] == "succeeded" and content["result"] == "item-01: pending"

    steps: list[ModelResponse | ScriptedStep]
    if options.phase == "propose":
        steps = [plan("ask_item", {"prompt": "Which synthetic item?"})]
    elif options.phase == "answer":
        steps = [
            ScriptedStep(
                events=[ModelComplete(response=plan("read_item", {"item_id": "item-01"}))],
                check=check_answer,
            )
        ]
    elif options.phase == "decide":
        steps = [
            ScriptedStep(
                events=[
                    ModelComplete(
                        response=ModelResponse(
                            message="denied" if options.denied else "read confirmed",
                            finish_reason="completed",
                        )
                    )
                ],
                check=check_result,
            )
        ]
    else:
        steps = []
    model = ScriptedModel(steps)
    agent: Agent[str] = Agent(
        name="items",
        revision="1",
        model=model,
        tools=[HumanInput(name="ask_item", input_type=ItemChoice), read_item],
    )
    async with Runtime(agents=[agent], storage=SQLiteStorage(directory / "runs.db")) as runtime:
        if options.phase == "propose":
            if options.stream:
                async with runtime.stream(
                    agent, "Read a synthetic item", session_id="s", context=CALLER
                ) as stream:
                    events = [event.type async for event in stream]
                    assert events[-1] == "run.suspended"
                    outcome = await stream.result()
            else:
                outcome = await runtime.run(
                    agent, "Read a synthetic item", session_id="s", context=CALLER
                )
            assert isinstance(outcome, Suspended) and isinstance(outcome.pending, InputRequest)
        elif options.phase == "answer":
            outcome = await runtime.resume(
                options.run_id,
                request_id=options.request_id,
                response=InputResponse(value={"item_id": "item-01"}),
                context=CALLER,
            )
            assert isinstance(outcome, Suspended) and isinstance(outcome.pending, ApprovalRequest)
            assert outcome.pending.arguments_summary == {"item_id": "item-01"}
            assert not counter.exists()
        else:
            decision = ApprovalDecision(approved=not options.denied)
            receipt = await runtime.respond(
                options.run_id, request_id=options.request_id, response=decision, context=CALLER
            )
            outcome = await runtime.wait(options.run_id, context=CALLER)
            assert outcome.status == "completed"
            if options.phase == "duplicate":
                assert not model.calls
            if options.stream:
                async with runtime.events(
                    options.run_id, context=CALLER, include_content=True
                ) as sub:
                    events = [event.type async for event in sub]
                    assert events[-1] == "run.completed"
            return {
                "pid": os.getpid(),
                "run_id": outcome.run_id,
                "status": outcome.status,
                "receipt": receipt.model_dump(mode="json"),
                "model_calls": len(model.calls),
            }
        assert outcome.pending is not None
        return {
            "pid": os.getpid(),
            "run_id": outcome.run_id,
            "status": outcome.status,
            "request_id": outcome.pending.request_id,
            "kind": outcome.pending.kind,
            "model_calls": len(model.calls),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--phase", choices=("propose", "answer", "decide", "duplicate"))
    parser.add_argument("--directory")
    parser.add_argument("--run-id")
    parser.add_argument("--request-id")
    parser.add_argument("--denied", action="store_true")
    options = parser.parse_args()
    if options.phase:
        print(json.dumps(asyncio.run(child(options))))
        return
    for denied in (False, True):
        with tempfile.TemporaryDirectory(prefix="drov-interactions-") as directory:
            result: dict[str, Any] = {}
            receipt: dict[str, Any] | None = None
            pids = set()
            approval_id = None
            for phase in ("propose", "answer", "decide", "duplicate"):
                command = [
                    sys.executable,
                    "-I",
                    "-B",
                    "-X",
                    "utf8",
                    str(Path(__file__).resolve()),
                    "--phase",
                    phase,
                    "--directory",
                    directory,
                ]
                if result:
                    request_id = approval_id if phase == "duplicate" else result["request_id"]
                    assert isinstance(request_id, str)
                    command += [
                        "--run-id",
                        result["run_id"],
                        "--request-id",
                        request_id,
                    ]
                if denied:
                    command.append("--denied")
                if options.stream:
                    command.append("--stream")
                output = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
                result = json.loads(output.stdout)
                assert result["pid"] not in pids
                pids.add(result["pid"])
                if phase == "answer":
                    approval_id = result["request_id"]
                if phase == "decide":
                    receipt = result["receipt"]
                if phase == "duplicate":
                    assert result["receipt"] == receipt and result["model_calls"] == 0
            counter = Path(directory) / "calls.json"
            calls = json.loads(counter.read_text()) if counter.exists() else []
            assert len(calls) == (0 if denied else 1)
            print(
                json.dumps(
                    {
                        "case": "deny" if denied else "approve",
                        "processes": len(pids),
                        "tool_calls": len(calls),
                        "status": result["status"],
                    }
                )
            )


if __name__ == "__main__":
    main()
