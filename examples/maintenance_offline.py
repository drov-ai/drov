"""Synthetic Session → offline preview/deletion → durable Gone; no external calls."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from drov import Agent, RunContext, Runtime, SQLiteStorage
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.errors import ContentGone, IncompatibleStorage
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import MemoryScope, SessionScope


async def check(directory: Path, *, stream: bool) -> None:
    database = directory / "synthetic.db"
    context = RunContext(tenant_id="fixture", user_id="alice")
    model = ScriptedModel([ModelResponse(message="synthetic answer", finish_reason="completed")])
    agent: Agent[str] = Agent(name="assistant", revision="1", model=model)

    def store() -> LocalArtifacts | None:
        return LocalArtifacts(directory=directory / "files") if os.name == "posix" else None

    inputs = [ArtifactInput(name="synthetic.csv", data=b"value\n7\n")] if os.name == "posix" else []
    async with Runtime(
        agents=[agent], storage=SQLiteStorage(database), artifacts=store()
    ) as runtime:
        if stream:
            async with runtime.stream(
                agent,
                "synthetic input",
                session_id="old",
                context=context,
                request_key="once",
                artifacts=inputs,
            ) as subscription:
                async for _ in subscription:
                    pass
                outcome = await subscription.result()
        else:
            outcome = await runtime.run(
                agent,
                "synthetic input",
                session_id="old",
                context=context,
                request_key="once",
                artifacts=inputs,
            )
        assert outcome.status == "completed"
    reclaimed = 0
    async with SQLiteMaintenance(database, artifacts=store()) as maintenance:
        plan = await maintenance.preview_session(
            SessionScope(**context.model_dump(), session_id="old")
        )
        assert not plan.blockers
        await maintenance.delete_session(plan)
        if os.name == "posix":
            preview = await maintenance.preview_reclamation(
                MemoryScope(**context.model_dump(), agent_name="assistant")
            )
            assert preview.stages == 1
            reclaimed = (await maintenance.reclaim(preview)).bytes_reclaimed
            assert reclaimed == len(inputs[0].data)
            assert (await maintenance.resume_reclamation()).completed_stages == 0
    async with Runtime(
        agents=[agent], storage=SQLiteStorage(database), artifacts=store()
    ) as runtime:
        try:
            await runtime.start(
                agent, "synthetic input", session_id="old", context=context, request_key="once"
            )
        except ContentGone:
            pass
        else:
            raise AssertionError("A deleted Session was recreated")
    assert len(model.calls) == 1
    empty = directory / "empty.db"
    empty.touch()
    try:
        async with SQLiteMaintenance(empty):
            pass
    except IncompatibleStorage:
        pass
    else:
        raise AssertionError("Maintenance initialized an existing empty file")
    assert empty.read_bytes() == b""
    print(
        json.dumps(
            {
                "checks": "passed",
                "stream": stream,
                "database_rows_deleted": plan.counts,
                "old_request": "content_gone",
                "bytes_reclaimed": reclaimed,
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
