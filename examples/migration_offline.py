"""Explicit compatible-field backfill with an actual pre-migration backup."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from drov import Agent, RunContext, Runtime, SQLiteStorage
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse
from drov.storage.maintenance import SQLiteMaintenance


async def main(stream: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="drov-migration-example-") as directory:
        root = await asyncio.to_thread(Path(directory).resolve)
        path = root / "state.db"
        actor = RunContext(tenant_id="example", user_id="synthetic")
        model = ScriptedModel([ModelResponse(message="saved", finish_reason="completed")])
        a: Agent[str] = Agent(name="assistant", revision="1", model=model)
        async with Runtime(agents=[a], storage=SQLiteStorage(path)) as runtime:
            if stream:
                async with runtime.stream(
                    a, "synthetic", session_id="s", context=actor, request_key="once"
                ) as events:
                    async for _ in events:
                        pass
                    done = await events.result()
            else:
                done = await runtime.run(
                    a, "synthetic", session_id="s", context=actor, request_key="once"
                )
        # Only this newly-created synthetic DB is edited to an older compatible
        # field envelope. Real historical writer fixtures are tested separately.
        async with SQLiteStorage(path) as storage:
            rows, _ = await storage._sql("SELECT payload FROM records WHERE kind='run'")
            payload = json.loads(rows[0][0])
            payload.pop("artifact_inputs")
            payload.pop("artifact_binding")
            await storage._sql(
                "UPDATE records SET payload=? WHERE kind='run'", (json.dumps(payload),)
            )
        async with SQLiteMaintenance(path) as maintenance:
            plan = await maintenance.preview_migration()
            assert plan.changes == {"run": 1}
            receipt = await maintenance.migrate(plan, backup_directory=root / "backup")
            assert await maintenance.migrate(plan, backup_directory=root / "unused") == receipt
            assert (await maintenance.preview_migration()).changes == {}
        assert (
            await SQLiteMaintenance.inspect_backup(root / "backup")
        ).snapshot_id == receipt.backup_snapshot_id
        async with Runtime(agents=[a], storage=SQLiteStorage(path)) as runtime:
            again = await runtime.start(
                a, "synthetic", session_id="s", context=actor, request_key="once"
            )
            assert again.run_id == done.run_id and len(model.calls) == 1
        print(
            json.dumps(
                {
                    "real_model": False,
                    "stream": stream,
                    "migration": receipt.profile,
                    "changed": receipt.changes,
                    "backup_verified": True,
                    "same_run": True,
                    "model_calls": len(model.calls),
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", action="store_true")
    asyncio.run(main(parser.parse_args().stream))
