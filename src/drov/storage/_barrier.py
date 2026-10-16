"""A restored snapshot cannot establish what happened after its capture."""

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from drov.artifacts.types import Sha256
from drov.errors import IncompatibleStorage, PersistenceUnavailable, RecoveryBlocked
from drov.types import UtcTime, WireModel

if TYPE_CHECKING:
    from drov.storage.sqlite import SQLiteDriver

BARRIER_DDL = (
    """CREATE TABLE recovery_barrier (
        id TEXT PRIMARY KEY NOT NULL CHECK(id='storage'), payload TEXT NOT NULL
    )""",
)


class RecoveryBarrier(WireModel):
    schema_version: Literal[1] = 1
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    origin: Literal["backup", "restore"]
    created_at: UtcTime
    source_manifest_sha256: Sha256 | None = None
    previous_sha256: Sha256 | None = None


async def get(driver: "SQLiteDriver") -> RecoveryBarrier | None:
    enabled, _ = await driver.execute("SELECT value FROM metadata WHERE key='recovery_schema'")
    if not enabled:
        return None
    if enabled != [("1",)]:
        raise IncompatibleStorage("Recovery extension is unsupported")
    rows, _ = await driver.execute("SELECT id,payload FROM recovery_barrier")
    if len(rows) != 1 or rows[0][0] != "storage":
        raise PersistenceUnavailable("Recovery barrier is missing or invalid")
    try:
        return RecoveryBarrier.from_json(rows[0][1])
    except (ValueError, TypeError) as exc:
        raise PersistenceUnavailable("Recovery barrier is invalid") from exc


async def check(driver: "SQLiteDriver") -> None:
    if await get(driver) is not None:
        raise RecoveryBlocked("Restored storage requires independent offline reconciliation")


async def install(driver: "SQLiteDriver", barrier: RecoveryBarrier) -> None:
    previous = await get(driver)
    if previous is None:
        for statement in BARRIER_DDL:
            await driver.execute(statement)
        await driver.execute("INSERT INTO metadata VALUES ('recovery_schema','1')")
    await driver.execute(
        "INSERT OR REPLACE INTO recovery_barrier VALUES ('storage',?)",
        (barrier.model_dump_json(),),
    )
