"""Check the real private staging layer after clean package installation (POSIX only)."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from drov.artifacts import ArtifactInput, LocalArtifacts
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


if __name__ == "__main__":
    if os.name != "posix":
        raise SystemExit("Private artifact staging not yet validated on this platform")
    with tempfile.TemporaryDirectory(prefix="drov-artifact-install-") as temporary:
        asyncio.run(check(Path(temporary).resolve()))
    print(
        json.dumps({"scope": "private byte staging, not a ready Artifact API", "checks": "passed"})
    )
