"""An offline snapshot can be read, but never trusted to replay external work."""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from drov import Agent, RunContext, Runtime, SQLiteStorage
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.errors import RecoveryBlocked
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse
from drov.storage.maintenance import SQLiteMaintenance


async def main(stream: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="drov-backup-example-") as directory:
        root = await asyncio.to_thread(Path(directory).resolve)
        path, files = root / "state.db", root / "files"
        actor = RunContext(tenant_id="example", user_id="synthetic")
        model = ScriptedModel([ModelResponse(message="saved", finish_reason="completed")])
        agent: Agent[str] = Agent(name="assistant", revision="1", model=model)
        paired = os.name == "posix"
        inputs = [ArtifactInput(name="numbers.csv", data=b"value\n1\n")] if paired else []
        async with Runtime(
            agents=[agent],
            storage=SQLiteStorage(path),
            artifacts=LocalArtifacts(directory=files) if paired else None,
        ) as runtime:
            if stream:
                async with runtime.stream(
                    agent, "synthetic", session_id="s", context=actor, artifacts=inputs
                ) as events:
                    async for _ in events:
                        pass
                    outcome = await events.result()
            else:
                outcome = await runtime.run(
                    agent, "synthetic", session_id="s", context=actor, artifacts=inputs
                )
            assert outcome.status == "completed"
            refs = (await runtime.storage.get_run(actor, outcome.run_id)).artifact_inputs
        async with SQLiteMaintenance(
            path, artifacts=LocalArtifacts(directory=files) if paired else None
        ) as maintenance:
            manifest = await maintenance.backup(root / "snapshot")
            assert await maintenance.get_recovery_barrier() is None
        assert await SQLiteMaintenance.inspect_backup(root / "snapshot") == manifest
        restored = await SQLiteMaintenance.restore(root / "snapshot", root / "restored")
        async with Runtime(
            agents=[agent],
            storage=SQLiteStorage(restored.database),
            artifacts=LocalArtifacts(directory=restored.artifacts, limits=restored.artifact_limits)
            if restored.artifacts
            else None,
        ) as runtime:
            assert (
                await runtime.inspect(outcome.run_id, context=actor)
            ).snapshot.status == "completed"
            for ref in refs:
                assert await runtime.read_artifact(ref, context=actor) == b"value\n1\n"
            try:
                await runtime.start(
                    agent,
                    "missing after backup",
                    session_id="absent",
                    context=actor,
                    request_key="absent",
                )
            except RecoveryBlocked:
                pass
            else:
                raise AssertionError("Restored storage dispatched new work")
        assert len(model.calls) == 1
        print(
            json.dumps(
                {
                    "real_model": False,
                    "stream": stream,
                    "paired_files": paired,
                    "snapshot_files": len(manifest.files),
                    "restored_bytes_verified": len(refs),
                    "recovery_barrier": True,
                    "model_calls": len(model.calls),
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    asyncio.run(main(parser.parse_args().stream))
