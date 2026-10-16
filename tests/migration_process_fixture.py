"""Kill the actual post-backup SQLite migration, including its receipt commit."""

import asyncio
import sys
from pathlib import Path
from typing import Any

from drov.storage import _backup
from drov.storage.maintenance import SQLiteMaintenance


async def main() -> None:
    database, backup, marker, boundary = sys.argv[1:]

    async def stop() -> None:
        await asyncio.to_thread(Path(marker).write_text, boundary)
        await asyncio.Event().wait()

    original_backup = _backup.backup

    async def copy(maintenance: SQLiteMaintenance, destination: Path) -> _backup.BackupManifest:
        result = await original_backup(maintenance, destination)
        if boundary == "after-backup":
            await stop()
        return result

    _backup.backup = copy
    async with SQLiteMaintenance(database) as maintenance:
        plan = await maintenance.preview_migration()
        original = maintenance._storage._raw
        receipt = False

        async def intercept(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            nonlocal receipt
            if sql == "COMMIT" and receipt and boundary == "before-commit":
                await stop()
            result = await original(sql, parameters)
            if sql.startswith("UPDATE records SET payload=") and boundary == "after-first-update":
                await stop()
            if sql.startswith("INSERT INTO migration_receipts"):
                receipt = True
            if sql == "COMMIT" and receipt and boundary == "after-commit":
                await stop()
            return result

        maintenance._storage._raw = intercept  # type: ignore[method-assign]
        await maintenance.migrate(plan, backup_directory=backup)


if __name__ == "__main__":
    asyncio.run(main())
