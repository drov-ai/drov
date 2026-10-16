"""Synthetic maintenance worker stopped at real SQLite commit boundaries."""

import asyncio
import sys
from pathlib import Path
from typing import Any

from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import SessionScope


async def main() -> None:
    database, marker, boundary = sys.argv[1:]
    async with SQLiteMaintenance(database) as maintenance:
        scope = SessionScope(tenant_id="tenant", user_id="alice", session_id="session")
        plan = await maintenance.preview_session(scope)
        original = maintenance._storage._raw

        async def stop(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            if sql == "COMMIT" and boundary == "before":
                await asyncio.to_thread(Path(marker).write_text, "before", encoding="utf-8")
                await asyncio.Event().wait()
            result = await original(sql, parameters)
            if sql == "COMMIT" and boundary == "after":
                await asyncio.to_thread(Path(marker).write_text, "after", encoding="utf-8")
                await asyncio.Event().wait()
            return result

        maintenance._storage._raw = stop  # type: ignore[method-assign]
        await maintenance.delete_session(plan)


if __name__ == "__main__":
    asyncio.run(main())
