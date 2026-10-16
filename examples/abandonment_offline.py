"""Synthetic approval pause -> explicit offline abandonment -> durable failure."""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from drov import Agent, ApprovalDecision, RunContext, Runtime, SQLiteStorage, tool
from drov.errors import Conflict, InteractionConflict
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse, ToolCall
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import SessionScope
from drov.types import Failed


async def check(root: Path, *, stream: bool) -> None:
    calls: list[int] = []

    @tool(name="write", effect="write", approval="required")
    def write(value: int) -> int:
        calls.append(value)
        return value

    model = ScriptedModel(
        [
            ModelResponse(
                finish_reason="tool_calls",
                tool_calls=[ToolCall(call_id="call", name="write", arguments={"value": 7})],
            )
        ]
    )
    agent: Agent[int] = Agent(name="assistant", revision="1", model=model, tools=[write])
    context = RunContext(tenant_id="fixture", user_id="alice")
    database = root / "state.db"
    async with Runtime(agents=[agent], storage=SQLiteStorage(database)) as runtime:
        if stream:
            async with runtime.stream(
                agent,
                "synthetic request",
                session_id="session",
                context=context,
                request_key="once",
            ) as subscription:
                async for _ in subscription:
                    pass
                paused = await subscription.result()
        else:
            paused = await runtime.run(
                agent,
                "synthetic request",
                session_id="session",
                context=context,
                request_key="once",
            )
        assert paused.status == "suspended" and paused.pending is not None
        request = paused.pending.request_id
    async with SQLiteMaintenance(database) as maintenance:
        plan = await maintenance.preview_abandonment(
            context, paused.run_id, reason="synthetic operator cannot continue"
        )
        fact = await maintenance.abandon_run(plan)
        assert await maintenance.abandon_run(plan) == fact
    async with Runtime(agents=[agent], storage=SQLiteStorage(database)) as runtime:
        result = await runtime.wait(paused.run_id, context=context)
        assert isinstance(result, Failed) and result.error.code == "data_abandoned"
        try:
            await runtime.respond(
                paused.run_id,
                request_id=request,
                response=ApprovalDecision(approved=True),
                context=context,
            )
        except InteractionConflict:
            pass
        else:
            raise AssertionError("An abandoned approval was consumed")
        try:
            await runtime.recover(paused.run_id, context=context)
        except Conflict:
            pass
        else:
            raise AssertionError("An abandoned run was recovered")
    async with SQLiteMaintenance(database) as maintenance:
        await maintenance.delete_session(
            await maintenance.preview_session(
                SessionScope(**context.model_dump(), session_id="session")
            )
        )
        assert await maintenance.get_abandonment(context, paused.run_id) == fact
    assert not calls and len(model.calls) == 1
    print(
        json.dumps(
            {
                "checks": "passed",
                "stream": stream,
                "status": "failed/data_abandoned",
                "actual_tool_calls": len(calls),
                "model_fixture_calls": len(model.calls),
                "minimal_fact_survives_cleanup": True,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="drov-abandonment-example-") as temporary:
        asyncio.run(check(Path(temporary).resolve(), stream=args.stream))


if __name__ == "__main__":
    main()
