"""Synthetic Session → offline preview/deletion → durable Gone; no external calls."""

import argparse
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from drov import Agent, RunContext, Runtime, SQLiteStorage
from drov.errors import ContentGone
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import SessionScope


async def check(directory: Path, *, stream: bool) -> None:
    database = directory / "synthetic.db"
    context = RunContext(tenant_id="fixture", user_id="alice")
    model = ScriptedModel([ModelResponse(message="synthetic answer", finish_reason="completed")])
    agent: Agent[str] = Agent(name="assistant", revision="1", model=model)
    async with Runtime(agents=[agent], storage=SQLiteStorage(database)) as runtime:
        if stream:
            async with runtime.stream(
                agent, "synthetic input", session_id="old", context=context, request_key="once"
            ) as subscription:
                async for _ in subscription:
                    pass
                outcome = await subscription.result()
        else:
            outcome = await runtime.run(
                agent, "synthetic input", session_id="old", context=context, request_key="once"
            )
        assert outcome.status == "completed"
    async with SQLiteMaintenance(database) as maintenance:
        plan = await maintenance.preview_session(
            SessionScope(**context.model_dump(), session_id="old")
        )
        assert not plan.blockers
        await maintenance.delete_session(plan)
    async with Runtime(agents=[agent], storage=SQLiteStorage(database)) as runtime:
        try:
            await runtime.start(
                agent, "synthetic input", session_id="old", context=context, request_key="once"
            )
        except ContentGone:
            pass
        else:
            raise AssertionError("A deleted Session was recreated")
    assert len(model.calls) == 1
    print(
        json.dumps(
            {
                "checks": "passed",
                "stream": stream,
                "database_rows_deleted": plan.counts,
                "old_request": "content_gone",
                "bytes_reclaimed": plan.bytes_reclaimed,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    # All destructive work is confined to an automatically created synthetic DB.
    with TemporaryDirectory(prefix="drov-maintenance-example-") as temporary:
        asyncio.run(check(Path(temporary).resolve(), stream=args.stream))


if __name__ == "__main__":
    main()
