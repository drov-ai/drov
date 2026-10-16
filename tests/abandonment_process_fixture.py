"""Synthetic data abandonment killed at its actual SQLite commit."""

import asyncio
import sys
from pathlib import Path
from typing import Any

from drov.storage.maintenance import SQLiteMaintenance
from drov.types import RunContext


async def main() -> None:
    path, run_id, marker, boundary = sys.argv[1:]
    async with SQLiteMaintenance(path) as maintenance:
        plan = await maintenance.preview_abandonment(
            RunContext(tenant_id="tenant", user_id="alice"), run_id, reason="source process lost"
        )
        original = maintenance._storage._raw

        async def stop(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            if sql == "COMMIT" and boundary == "before":
                await asyncio.to_thread(Path(marker).write_text, "before")
                await asyncio.Event().wait()
            result = await original(sql, parameters)
            if sql == "COMMIT" and boundary == "after":
                await asyncio.to_thread(Path(marker).write_text, "after")
                await asyncio.Event().wait()
            return result

        maintenance._storage._raw = stop  # type: ignore[method-assign]
        await maintenance.abandon_run(plan)


if __name__ == "__main__":
    asyncio.run(main())
