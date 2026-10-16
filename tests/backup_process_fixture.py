"""Stop a real backup/restore worker at capture and publication boundaries."""

import asyncio
import sys
import threading
from pathlib import Path

from drov.storage import _backup
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.sqlite import SQLiteStorage


async def main() -> None:
    mode, source, target, marker, boundary = sys.argv[1:]

    def stop() -> None:
        Path(marker).write_text(boundary)
        threading.Event().wait()

    original_publish = _backup._Bundle.publish

    def publish(self: _backup._Bundle, partial: Path) -> None:
        if boundary == "before-publish":
            stop()
        original_publish(self, partial)
        if boundary == "after-publish":
            stop()

    original_copy = _backup._database

    async def copy(storage: SQLiteStorage, target: Path) -> None:
        if boundary == "before-copy":
            await asyncio.to_thread(stop)
        await original_copy(storage, target)
        if boundary == "after-copy":
            await asyncio.to_thread(stop)

    _backup._Bundle.publish = publish  # type: ignore[method-assign]
    _backup._database = copy
    if mode == "backup":
        async with SQLiteMaintenance(source) as maintenance:
            await maintenance.backup(target)
    else:
        await SQLiteMaintenance.restore(source, target)


if __name__ == "__main__":
    asyncio.run(main())
