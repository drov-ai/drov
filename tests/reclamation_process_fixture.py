"""Killed only against synthetic byte stores at actual durable boundaries."""

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import Any

from drov.artifacts import LocalArtifacts
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import MemoryScope

OWNER = MemoryScope(tenant_id="tenant", user_id="alice", agent_name="assistant")


async def main() -> None:
    root, marker, boundary = sys.argv[1:]
    marker_path = Path(marker)

    def stop() -> None:
        marker_path.write_text(boundary)
        threading.Event().wait()

    async with SQLiteMaintenance(
        Path(root) / "state.db", artifacts=LocalArtifacts(directory=Path(root) / "files")
    ) as maintenance:
        plan = await maintenance.preview_reclamation(OWNER)
        original_sql, unlink, rmdir = maintenance._storage._raw, os.unlink, os.rmdir
        commits = 0

        async def intercept(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            nonlocal commits
            if sql == "COMMIT":
                commits += 1
                if (boundary == "before_intent" and commits == 1) or (
                    boundary == "before_complete" and commits == 3
                ):
                    await asyncio.to_thread(stop)
            result = await original_sql(sql, parameters)
            if sql == "COMMIT" and (
                (boundary == "after_intent" and commits == 1)
                or (boundary == "after_complete" and commits == 3)
            ):
                await asyncio.to_thread(stop)
            return result

        def intercepted_unlink(name: str, **kwargs: Any) -> None:
            unlink(name, **kwargs)
            if (boundary == "after_byte" and name.startswith("art_")) or (
                boundary == "after_manifest" and name == "stage.json"
            ):
                stop()

        def intercepted_rmdir(name: str, **kwargs: Any) -> None:
            rmdir(name, **kwargs)
            if boundary == "after_rmdir":
                stop()

        maintenance._storage._raw = intercept  # type: ignore[method-assign]
        os.unlink = intercepted_unlink  # type: ignore[assignment]
        os.rmdir = intercepted_rmdir  # type: ignore[assignment]
        await maintenance.reclaim(plan)


if __name__ == "__main__":
    asyncio.run(main())
