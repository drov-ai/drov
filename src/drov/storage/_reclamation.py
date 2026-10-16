"""Durable byte reclamation facts and anchored, resumable POSIX removal."""

import os
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, model_validator

from drov.artifacts.local import LEDGER_BYTES, _io, _Stage
from drov.artifacts.types import Sha256
from drov.errors import PersistenceUnavailable
from drov.storage.records import MemoryScope
from drov.types import Identifier, UtcTime, WireModel

if TYPE_CHECKING:
    from drov.artifacts.local import LocalArtifacts
    from drov.storage.sqlite import SQLiteDriver

RECLAMATION_DDL = (
    """CREATE TABLE artifact_reclamation (
        stage_id TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','done'))
    )""",
)


class ReclamationPlan(WireModel):
    """Host-admin preview; no content, logical names or filesystem paths."""

    scope: MemoryScope
    expired_uploads: bool = False
    as_of: UtcTime
    fingerprint: Sha256
    stages: int = Field(ge=0)
    bytes_eligible: int = Field(ge=0)
    capacity_eligible: int = Field(ge=0)
    uploads: int = Field(ge=0)
    protected: dict[str, int]


class ReclamationResult(WireModel):
    completed_stages: int = Field(default=0, ge=0)
    bytes_reclaimed: int = Field(default=0, ge=0)
    capacity_released: int = Field(default=0, ge=0)


class _EntryFile(WireModel):
    name: str = Field(pattern=r"^(stage\.json|art_[0-9a-f]{32})$")
    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    size: int = Field(ge=0)
    modified_ns: int

    @classmethod
    def capture(cls, name: str, info: os.stat_result) -> Self:
        return cls(
            name=name,
            device=info.st_dev,
            inode=info.st_ino,
            size=info.st_size,
            modified_ns=info.st_mtime_ns,
        )


class ReclamationEntry(WireModel):
    schema_version: Literal[1] = 1
    stage_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    run_id: Identifier
    store_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    scope_digest: Sha256
    stage_digest: Sha256
    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    files: list[_EntryFile] = Field(max_length=257)
    reserved_bytes: int = Field(ge=0)
    created_at: UtcTime
    state: Literal["pending", "done"] = "pending"
    completed_at: UtcTime | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        names = [item.name for item in self.files]
        if len(set(names)) != len(names) or names.count("stage.json") != 1:
            raise ValueError("Reclamation inventory is invalid")
        if (self.state == "done") != (self.completed_at is not None):
            raise ValueError("Reclamation completion is inconsistent")
        if self.bytes_reclaimed > self.reserved_bytes:
            raise ValueError("Reclamation exceeds the original reservation")
        return self

    @property
    def bytes_reclaimed(self) -> int:
        return sum(item.size for item in self.files if item.name != "stage.json")

    @property
    def capacity_released(self) -> int:
        return self.reserved_bytes + LEDGER_BYTES


async def entries(driver: "SQLiteDriver") -> list[ReclamationEntry]:
    enabled, _ = await driver.execute("SELECT value FROM metadata WHERE key='reclamation_schema'")
    if not enabled:
        return []
    rows, _ = await driver.execute("SELECT stage_id,payload,state FROM artifact_reclamation")
    try:
        result = [ReclamationEntry.from_json(row[1]) for row in rows]
        if any(
            (item.stage_id, item.state) != (row[0], row[2])
            for item, row in zip(result, rows, strict=True)
        ):
            raise ValueError("Reclamation index differs")
        return result
    except (ValueError, TypeError) as exc:
        raise PersistenceUnavailable("Reclamation ledger is invalid") from exc


async def capture(
    store: "LocalArtifacts", stage: _Stage, *, scope_digest: str, stage_digest: str, now: UtcTime
) -> ReclamationEntry:
    store_id = store.store_id

    def inspect() -> ReclamationEntry:
        if store._load(stage.stage_id) != stage:
            raise PersistenceUnavailable("Stage changed during reclamation preview")
        fd = store._child(stage.stage_id)
        try:
            info = os.fstat(fd)
            files = []
            for name in sorted(os.listdir(fd)):
                current = os.stat(name, dir_fd=fd, follow_symlinks=False)
                store._private(current)
                files.append(_EntryFile.capture(name, current))
            return ReclamationEntry(
                stage_id=stage.stage_id,
                run_id=stage.run_id,
                store_id=store_id,
                scope_digest=scope_digest,
                stage_digest=stage_digest,
                device=info.st_dev,
                inode=info.st_ino,
                files=files,
                reserved_bytes=stage.reserved_bytes,
                created_at=now,
            )
        finally:
            os.close(fd)

    async with store._gate:
        return await _io(inspect)


async def erase(store: "LocalArtifacts", entry: ReclamationEntry) -> None:
    """Only a committed intent may call this. Missing files mean prior progress."""
    if store.store_id != entry.store_id:
        raise PersistenceUnavailable("Reclamation store pairing changed")

    def remove() -> None:
        root = store._check()
        try:
            current = os.stat(entry.stage_id, dir_fd=root, follow_symlinks=False)
        except FileNotFoundError:
            # Repeat the durability fence even if an earlier receipt was lost.
            os.fsync(root)
            return
        store._private(current, directory=True)
        if (current.st_dev, current.st_ino) != (entry.device, entry.inode):
            raise PersistenceUnavailable("Reclamation stage was replaced")
        fd = store._child(entry.stage_id)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (entry.device, entry.inode):
                raise PersistenceUnavailable("Reclamation stage changed while opening")
            planned = {item.name: item for item in entry.files}
            present = set(os.listdir(fd))
            if present - planned.keys():
                raise PersistenceUnavailable("Unexpected reclamation directory entry")

            def check(name: str) -> None:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                store._private(info)
                if _EntryFile.capture(name, info) != planned[name]:
                    raise PersistenceUnavailable("Reclamation file was replaced or changed")

            # Validate the entire remaining inventory before the first mutation.
            for name in present:
                check(name)
            for name in sorted(present - {"stage.json"}):
                check(name)
                os.unlink(name, dir_fd=fd)
            os.fsync(fd)
            if "stage.json" in present:
                check("stage.json")
                os.unlink("stage.json", dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(fd)
        current = os.stat(entry.stage_id, dir_fd=root, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (entry.device, entry.inode):
            raise PersistenceUnavailable("Reclamation stage changed before removal")
        os.rmdir(entry.stage_id, dir_fd=root)
        os.fsync(root)

    async with store._gate:
        try:
            await _io(remove)
        except OSError as exc:
            raise PersistenceUnavailable(
                "Reclamation I/O did not complete; resume offline"
            ) from exc
