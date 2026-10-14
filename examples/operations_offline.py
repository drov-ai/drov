"""Five S-03 offline paths with fresh SDK processes and an independent counted service."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
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
    ReconciliationDecision,
    RunContext,
    Runtime,
    SQLiteStorage,
    ToolContext,
    tool,
)
from drov.models import ScriptedModel, ScriptedStep
from drov.models.protocol import ModelComplete, ModelRequest, ModelResponse, ToolCall
from drov.types import ApprovalRequest, InputRequest, ReconciliationRequest, Suspended

CALLER = RunContext(tenant_id="synthetic", user_id="alice")
SCRIPT = Path(__file__).resolve()
FIXTURE = SCRIPT.with_name("item_service_fixture.py")


@dataclass
class Choice:
    item_id: str


def plan(name: str, arguments: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[ToolCall(call_id="call-0", name=name, arguments=arguments)],
    )


def service_client(directory: Path) -> Any:
    return runpy.run_path(str(FIXTURE))["ItemService"](directory)


async def child(options: argparse.Namespace) -> dict[str, Any]:
    directory = Path(options.directory)
    items = service_client(directory)
    items.port, items.mode = options.port, options.mode

    async def verifier(ctx: ToolContext) -> ReconciliationDecision:
        service: Any = ctx.deps["items"]
        return ReconciliationDecision.model_validate(
            await service.query(ctx.operation_id, ctx.tenant_id, ctx.user_id)
        )

    @tool(name="update_item", effect="write", approval="required", verifier=verifier)
    async def update(item_id: str, status: str, ctx: ToolContext) -> dict[str, str]:
        service: Any = ctx.deps["items"]
        value: dict[str, str] = await service.apply(
            ctx.operation_id, ctx.tenant_id, ctx.user_id, item_id, status
        )
        return value

    def checked(request: ModelRequest) -> None:
        content = request.messages[-1].content
        assert isinstance(content, dict)
        if options.case == "deny":
            assert content["error_code"] == "approval_denied"
        else:
            assert content["status"] == "succeeded" and content["result"] == {
                "item_id": "item-01",
                "status": "processing",
            }

    steps: list[ModelResponse | ScriptedStep] = []
    update_plan = plan("update_item", {"item_id": "item-01", "status": "processing"})
    if options.phase == "propose":
        steps = [
            plan("ask_item", {"prompt": "Which synthetic item?"})
            if options.case == "input"
            else update_plan
        ]
    elif options.phase == "answer":
        steps = [update_plan]
    elif (options.phase == "decide" and options.case in ("approve", "deny", "input")) or (
        options.phase == "reconcile" and options.case == "lost"
    ):
        steps = [
            ScriptedStep(
                events=[
                    ModelComplete(
                        response=ModelResponse(
                            message="denied" if options.case == "deny" else "verified",
                            finish_reason="completed",
                        )
                    )
                ],
                check=checked,
            )
        ]
    model = ScriptedModel(steps)
    agent: Agent[str] = Agent(
        name="operator",
        revision="1",
        model=model,
        tools=[HumanInput(name="ask_item", input_type=Choice), update],
        required_dependencies=["items"],
    )
    async with Runtime(
        agents=[agent], storage=SQLiteStorage(directory / "runs.db"), dependencies={"items": items}
    ) as runtime:
        receipt = None
        if options.phase == "propose":
            if options.stream:
                async with runtime.stream(
                    agent, "Update synthetic item", session_id="operations", context=CALLER
                ) as stream:
                    events = [event.type async for event in stream]
                    assert events[-1] == "run.suspended"
                    result = await stream.result()
            else:
                result = await runtime.run(
                    agent, "Update synthetic item", session_id="operations", context=CALLER
                )
            assert isinstance(result, Suspended)
            assert isinstance(
                result.pending, InputRequest if options.case == "input" else ApprovalRequest
            )
        elif options.phase == "answer":
            result = await runtime.resume(
                options.run_id,
                request_id=options.request_id,
                response=InputResponse(value={"item_id": "item-01"}),
                context=CALLER,
            )
            assert isinstance(result, Suspended) and isinstance(result.pending, ApprovalRequest)
        elif options.phase in ("decide", "duplicate"):
            receipt = await runtime.respond(
                options.run_id,
                request_id=options.request_id,
                response=ApprovalDecision(approved=options.case != "deny"),
                context=CALLER,
            )
            result = await runtime.wait(options.run_id, context=CALLER)
            if options.phase == "duplicate":
                assert not model.calls
            elif options.case in ("lost", "cancel"):
                assert isinstance(result, Suspended) and isinstance(
                    result.pending, ReconciliationRequest
                )
            else:
                assert result.status == "completed"
        else:
            if options.case == "cancel":
                await runtime.cancel(options.run_id, context=CALLER)
            decision = await runtime.verify(
                options.run_id, request_id=options.request_id, context=CALLER
            )
            assert decision.resolution == "applied"
            if options.case == "cancel":
                decision = ReconciliationDecision(resolution="applied", evidence=decision.evidence)
            result = await runtime.resume(
                options.run_id, request_id=options.request_id, response=decision, context=CALLER
            )
            assert result.status == ("cancelled" if options.case == "cancel" else "completed")
        if options.stream:
            async with runtime.events(
                result.run_id, context=CALLER, include_content=True
            ) as subscription:
                collected = [event.type async for event in subscription]
                assert collected[-1] == "run." + result.status
        output: dict[str, Any] = {
            "pid": os.getpid(),
            "run_id": result.run_id,
            "status": result.status,
            "model_calls": len(model.calls),
        }
        if isinstance(result, Suspended):
            assert result.pending is not None
            output.update(
                request_id=result.pending.request_id,
                operation_id=result.pending.operation_id,
                kind=result.pending.kind,
            )
        if receipt is not None:
            output["receipt"] = receipt.model_dump(mode="json")
        return output


async def parent(stream: bool) -> None:
    for case in ("approve", "deny", "input", "lost", "cancel"):
        with tempfile.TemporaryDirectory(prefix="drov-operations-") as directory:
            service = service_client(Path(directory))
            await service.open()
            try:
                result: dict[str, Any] = {}
                approval_id = None
                original_receipt = None
                pids = set()
                phases = (
                    ["propose"]
                    + (["answer"] if case == "input" else [])
                    + ["decide"]
                    + (["reconcile"] if case in ("lost", "cancel") else [])
                    + ["duplicate"]
                )
                for phase in phases:
                    command = [
                        sys.executable,
                        "-I",
                        "-B",
                        "-X",
                        "utf8",
                        str(SCRIPT),
                        "--phase",
                        phase,
                        "--directory",
                        directory,
                        "--port",
                        str(service.port),
                        "--case",
                        case,
                        "--mode",
                        "reply_lost" if case in ("lost", "cancel") else "normal",
                    ]
                    if result:
                        request_id = approval_id if phase == "duplicate" else result["request_id"]
                        assert isinstance(request_id, str)
                        command += ["--run-id", result["run_id"], "--request-id", request_id]
                    if stream:
                        command.append("--stream")
                    # Blocking child wait is isolated from the owned service/process lifecycle loop.
                    completed = await asyncio.to_thread(
                        subprocess.run,
                        command,
                        check=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=30,
                    )
                    result = json.loads(completed.stdout)
                    assert result["pid"] not in pids
                    pids.add(result["pid"])
                    if result.get("kind") == "approval":
                        approval_id = result["request_id"]
                        assert (await service.stats())["writes"] == 0
                    if phase == "decide":
                        original_receipt = result["receipt"]
                    if phase == "duplicate":
                        assert result["receipt"] == original_receipt and result["model_calls"] == 0
                stats = await service.stats()
                assert stats["writes"] == (0 if case == "deny" else 1)
                print(
                    json.dumps(
                        {
                            "case": case,
                            "sdk_processes": len(pids),
                            "independent_writes": stats["writes"],
                            "status": result["status"],
                        }
                    )
                )
            finally:
                await service.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument(
        "--phase", choices=("propose", "answer", "decide", "reconcile", "duplicate")
    )
    parser.add_argument("--directory")
    parser.add_argument("--port", type=int)
    parser.add_argument("--case", default="approve")
    parser.add_argument("--mode", default="normal")
    parser.add_argument("--run-id")
    parser.add_argument("--request-id")
    options = parser.parse_args()
    if options.phase:
        print(json.dumps(asyncio.run(child(options))))
    else:
        asyncio.run(parent(options.stream))


if __name__ == "__main__":
    main()
