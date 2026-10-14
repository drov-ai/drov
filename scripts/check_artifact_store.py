"""Check the real private staging layer after clean package installation (POSIX only)."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from drov import Agent, RunContext, Runtime
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse
from drov.storage import SQLiteStorage
from drov.storage.records import MemoryScope


async def check(path: Path) -> None:
    owner = MemoryScope(tenant_id="fixture", user_id="fixture", agent_name="artifact-check")
    files = LocalArtifacts(directory=path / "private")
    async with SQLiteStorage(path / "state.db") as storage:
        await files.open(storage=storage)
        try:
            pending = await files._reserve(
                owner=owner,
                run_id="fixture",
                operation_id=None,
                purpose="input",
                size_bytes=4,
            )
            stage = await files._stage(
                pending.stage_id, [ArtifactInput(name="x.csv", data=b"v\n1\n")]
            )
        finally:
            await files.aclose()
    async with SQLiteStorage(path / "state.db") as storage:
        await files.open(storage=storage)
        try:
            assert await files._read_staged(owner, stage.stage_id, stage.files[0].ref) == b"v\n1\n"
        finally:
            await files.aclose()

    actor = RunContext(tenant_id=owner.tenant_id, user_id=owner.user_id)
    model = ScriptedModel([ModelResponse(message="metadata", finish_reason="completed")])
    agent: Agent[str] = Agent(name=owner.agent_name, revision="1", model=model)
    async with Runtime(
        agents=[agent],
        storage=SQLiteStorage(path / "state.db"),
        artifacts=LocalArtifacts(directory=path / "private"),
    ) as runtime:
        result = await runtime.run(
            agent,
            "describe file",
            session_id="s",
            context=actor,
            artifacts=[ArtifactInput(name="numbers.csv", data=b"v\n1\n")],
        )
        assert result.status == "completed"
        record = await runtime.storage.get_run(actor, result.run_id)
        assert await runtime.read_artifact(record.artifact_inputs[0], context=actor) == b"v\n1\n"


if __name__ == "__main__":
    if os.name != "posix":
        raise SystemExit("Private artifact staging not yet validated on this platform")
    with tempfile.TemporaryDirectory(prefix="drov-artifact-install-") as temporary:
        asyncio.run(check(Path(temporary).resolve()))
    print(
        json.dumps(
            {
                "scope": "private staging and Runtime input/read, no sandbox execution",
                "checks": "passed",
            }
        )
    )
