"""Consistent offline snapshot bundles; publish only after durable quarantine.

Bundles are host administrator capabilities, not authorized content exports.
No process/container/VM state is captured and no external service is contacted.
"""

import asyncio
import hashlib
import os
import re
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from functools import partial as bind
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from drov._json import canonical_json
from drov.artifacts.local import NOFOLLOW, LocalArtifacts, _io
from drov.artifacts.records import ArtifactRecord
from drov.artifacts.transaction import ArtifactTransaction
from drov.artifacts.types import ArtifactLimits, Sha256
from drov.artifacts.uploads import UploadRecord
from drov.errors import ConfigurationError, PersistenceUnavailable
from drov.sandbox._leases import LeaseTransaction, SandboxLease
from drov.storage._barrier import RecoveryBarrier, get, install
from drov.storage._driver import Key, Row
from drov.storage._ownership import FileOwnership
from drov.storage._retention import Tombstone, retained_cost
from drov.storage.records import (
    Checkpoint,
    EventRecord,
    MemoryRecord,
    RunItem,
    RunRecord,
    SessionRecord,
    StartKeyRecord,
)
from drov.storage.sqlite import DDL, SQLiteStorage, _settle
from drov.storage.transaction import _decode
from drov.tools.operations import ToolOperation
from drov.types import UtcTime, WireModel

if TYPE_CHECKING:
    from drov.storage.maintenance import SQLiteMaintenance

MAX_MANIFEST_BYTES = 16 * 1024**2
FILE_PATTERN = (
    r"^(state\.sqlite|artifacts/store\.json|"
    r"artifacts/[0-9a-f]{32}/(stage\.json|art_[0-9a-f]{32}))$"
)


class BackupFile(WireModel):
    path: str = Field(pattern=FILE_PATTERN)
    size_bytes: int = Field(ge=0)
    sha256: Sha256


class BackupManifest(WireModel):
    schema_version: Literal[1] = 1
    format: Literal["drov.backup"] = "drov.backup"
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: UtcTime
    versions: dict[str, str]
    artifact_store_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    artifact_limits: ArtifactLimits | None = None
    files: list[BackupFile] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        names = [item.path for item in self.files]
        if names != sorted(set(names)) or names.count("state.sqlite") != 1:
            raise ValueError("Backup inventory is invalid")
        if (self.artifact_store_id is None) != (self.artifact_limits is None):
            raise ValueError("Backup artifact binding is incomplete")
        if ("artifacts/store.json" in names) != (self.artifact_store_id is not None):
            raise ValueError("Backup artifact header is missing or unexpected")
        if self.artifact_store_id is None and any(name.startswith("artifacts/") for name in names):
            raise ValueError("Unpaired backup contains artifact files")
        return self


class RestoreResult(WireModel):
    """Host-only locations; never expose this administrator result via HTTP."""

    database: str
    artifacts: str | None = None
    artifact_limits: ArtifactLimits | None = None
    barrier: RecoveryBarrier


def _private(info: os.stat_result, *, directory: bool = False) -> None:
    if (
        not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        or (not directory and info.st_nlink != 1)
        or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
        or (
            os.name == "posix"
            and (
                info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            )
        )
    ):
        raise ConfigurationError("Backup requires private regular files and directories")


def _directory(path: Path) -> None:
    for ancestor in [*reversed(path.parents), path]:
        if ancestor.is_symlink() or (
            ancestor.exists()
            and getattr(ancestor.lstat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ConfigurationError("Backup paths cannot contain filesystem links")
    _private(path.lstat(), directory=True)


def _sync(path: Path) -> None:
    # Windows has no portable directory fsync; file flushes, SQLite FULL and
    # atomic directory rename still ensure no published unbarred database.
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _transfer(
    source: Path, destination: Path | None, relative: str, *, directory_fd: int | None = None
) -> BackupFile:
    def info() -> os.stat_result:
        return (
            source.lstat()
            if directory_fd is None
            else os.stat(source.name, dir_fd=directory_fd, follow_symlinks=False)
        )

    before = info()
    _private(before)
    fd = os.open(
        source if directory_fd is None else source.name,
        os.O_RDONLY | NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
        dir_fd=directory_fd,
    )
    with os.fdopen(fd, "rb") as incoming:
        opened = os.fstat(incoming.fileno())
        _private(opened)
        # Windows stat and fstat can assign different meanings to ctime. Bind
        # the same inode/size/mtime across APIs, then compare each API's complete
        # snapshot to itself at the end; do not discard either change-time check.
        if _identity(opened)[:-1] != _identity(before)[:-1]:
            raise PersistenceUnavailable("Backup source changed while opening")
        digest, length = hashlib.sha256(), 0
        target = None
        try:
            if destination is not None:
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
                target = os.fdopen(fd, "wb")
            while data := incoming.read(1024**2):
                length += len(data)
                digest.update(data)
                if target is not None:
                    target.write(data)
            if target is not None:
                target.flush()
                os.fsync(target.fileno())
            after_fd, after_path = os.fstat(incoming.fileno()), info()
            _private(after_fd)
            _private(after_path)
            if (
                length != before.st_size
                or _identity(after_fd) != _identity(opened)
                or _identity(after_path) != _identity(before)
            ):
                raise PersistenceUnavailable("Backup source changed during transfer")
        finally:
            if target is not None:
                target.close()
    return BackupFile(path=relative, size_bytes=length, sha256=digest.hexdigest())


class _Bundle:
    def __init__(self, path: Path, *, create: bool) -> None:
        self.path = path.absolute()
        self.owner: FileOwnership | None = None
        if create:
            # Reject an existing directory, including empty targets; never replace.
            if not self.path.parent.is_dir():
                raise ConfigurationError("Backup parent must already exist")
            for ancestor in [*reversed(self.path.parents), self.path]:
                if ancestor.is_symlink():
                    raise ConfigurationError("Backup paths cannot contain links")
            self.path.mkdir(mode=0o700)
            _sync(self.path.parent)
        _directory(self.path)
        self.identity = self.path.stat().st_dev, self.path.stat().st_ino
        if not create and not (self.path / "owner").is_file():
            raise ConfigurationError("Backup bundle ownership is missing")
        owner = FileOwnership(self.path / "owner")
        owner.acquire()
        try:
            _private((self.path / "owner").lstat())
            _private((self.path / "owner.drov.lock").lstat())
            self.owner = owner
        except BaseException:
            owner.release()
            raise

    def check(self) -> None:
        _directory(self.path)
        if (self.path.stat().st_dev, self.path.stat().st_ino) != self.identity:
            raise PersistenceUnavailable("Backup directory was replaced")
        assert self.owner is not None
        self.owner.check()

    def partial(self) -> Path:
        self.check()
        path = self.path / ("partial_" + uuid4().hex)
        path.mkdir(mode=0o700)
        _sync(self.path)
        return path

    def publish(self, partial: Path) -> None:
        self.check()
        if (self.path / "data").exists():
            raise ConfigurationError("Backup destination already contains published data")
        _sync(partial)
        os.rename(partial, self.path / "data")
        _sync(self.path)

    def close(self) -> None:
        if self.owner is not None:
            self.owner.release()
            self.owner = None


def _write(path: Path, value: WireModel) -> None:
    data = value.model_dump_json().encode()
    if len(data) > MAX_MANIFEST_BYTES:
        raise ConfigurationError("Backup manifest exceeds its bound")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def _inventory(root: Path) -> list[BackupFile]:
    """Walk only format-known private directories, refusing links and extras."""
    result = []

    def walk(path: Path) -> None:
        _directory(path)
        for child in sorted(path.iterdir()):
            relative = child.relative_to(root).as_posix()
            if relative in ("manifest.json", "state.sqlite.drov.lock", "artifacts/ownership.lock"):
                _private(child.lstat())
                continue
            if relative == "artifacts" or re.fullmatch(r"artifacts/[0-9a-f]{32}", relative):
                _private(child.lstat(), directory=True)
                required = "store.json" if relative == "artifacts" else "stage.json"
                if not (child / required).is_file():
                    raise PersistenceUnavailable("Backup directory lacks its original manifest")
                walk(child)
            elif re.fullmatch(FILE_PATTERN, relative):
                result.append(_transfer(child, None, relative))
            else:
                raise PersistenceUnavailable("Unexpected backup inventory entry")

    walk(root)
    return sorted(result, key=lambda item: item.path)


def _read(bundle: _Bundle) -> tuple[BackupManifest, bytes]:
    bundle.check()
    if set(path.name for path in bundle.path.iterdir()) != {"owner", "owner.drov.lock", "data"}:
        raise PersistenceUnavailable("Backup is incomplete or contains unexpected entries")
    data = bundle.path / "data"
    _directory(data)
    path = data / "manifest.json"
    _private(path.lstat())
    fd = os.open(path, os.O_RDONLY | NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        raw = source.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise PersistenceUnavailable("Backup manifest exceeds its bound")
    try:
        manifest = BackupManifest.from_json(raw)
    except (ValueError, TypeError) as exc:
        raise PersistenceUnavailable("Backup manifest is invalid or unsupported") from exc
    if _inventory(data) != manifest.files:
        raise PersistenceUnavailable("Backup checksum inventory differs")
    return manifest, raw


async def validate(maintenance: "SQLiteMaintenance") -> dict[str, str]:
    """Full SQLite integrity, versioned records, ready grants and original bytes."""
    async with maintenance._storage.transaction() as tx:
        await maintenance._no_pending_reclamation(tx)
        driver = maintenance._driver(tx)
        integrity, _ = await driver.execute("PRAGMA integrity_check")
        if integrity != [("ok",)]:
            raise PersistenceUnavailable("Backup database integrity check failed")
        extras, _ = await driver.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('view','trigger')"
        )
        if extras:
            raise PersistenceUnavailable("Backup refuses unrecognized database programs")
        for statement in DDL:
            name = re.match(r"CREATE (?:UNIQUE )?(?:TABLE|INDEX) (\w+)", statement)
            assert name is not None
            rows, _ = await driver.execute("SELECT sql FROM sqlite_master WHERE name=?", (name[1],))
            if len(rows) != 1 or " ".join(rows[0][0].split()) != " ".join(statement.split()):
                raise PersistenceUnavailable("Backup core schema differs from its version")
        snapshot = await maintenance._snapshot(tx)
        models: dict[str, type[WireModel]] = {
            "session": SessionRecord,
            "run": RunRecord,
            "checkpoint": Checkpoint,
            "event": EventRecord,
            "memory": MemoryRecord,
            "start_key": StartKeyRecord,
        }
        runs = []
        for raw in snapshot["records"]:
            row = Row(Key(*raw[:5]), *raw[5:])
            model = models.get(row.key.kind, RunItem)
            value = _decode(model, row)
            if isinstance(value, RunRecord):
                runs.append(value)
                await tx.get_checkpoint(value.scope, value.state.run_id)
                await tx.get_session(value.scope)
            elif isinstance(value, RunItem) and value.kind == "operation":
                operation = ToolOperation.from_json(canonical_json(value.data))
                if (
                    operation.run_id,
                    operation.operation_id,
                    operation.version,
                    operation.status,
                ) != (value.run_id, value.item_id, value.version, value.state):
                    raise PersistenceUnavailable("Backup operation index differs")
        store = maintenance._artifacts
        for raw in snapshot.get("artifact_records", []):
            record = ArtifactRecord.from_json(raw[5])
            await ArtifactTransaction(tx).locate(record.owner, record.ref.artifact_id)
            if record.state == "ready":
                if store is None or store.store_id != record.store_id:
                    raise PersistenceUnavailable("Backup artifact pairing is missing")
                await store._read_staged(
                    record.owner, record.stage_id, record.ref, name=record.name
                )
        if store is not None:
            for run in runs:
                await ArtifactTransaction(tx).grants(run.scope, run.state.run_id)
        for raw in snapshot.get("sandbox_leases", []):
            lease = SandboxLease.from_json(raw[8])
            LeaseTransaction._decode(
                (raw[8], raw[0], *raw[1:4], raw[6], raw[7]), raw[4], raw[5], lease.owner
            )
        for raw in snapshot.get("artifact_uploads", []):
            upload = UploadRecord.from_json(raw[3])
            if (upload.upload_id, upload.owner.tenant_id, upload.owner.user_id, upload.version) != (
                raw[0],
                raw[1],
                raw[2],
                raw[4],
            ):
                raise PersistenceUnavailable("Backup upload index differs")
            if store is None or upload.store_id != store.store_id:
                raise PersistenceUnavailable("Backup upload pairing differs")
            stage = await store._snapshot(upload.stage_id)
            if (stage.owner.tenant_id, stage.owner.user_id, stage.owner.agent_name) != (
                upload.owner.tenant_id,
                upload.owner.user_id,
                upload.target.agent_name,
            ):
                raise PersistenceUnavailable("Backup upload ownership differs")
            if upload.ref is not None:
                await store._read_staged(
                    stage.owner, upload.stage_id, upload.ref, name=upload.target.name
                )
        for _, key, payload in snapshot.get("retention_tombstones", []):
            if not re.fullmatch(r"[0-9a-f]{64}", key):
                raise PersistenceUnavailable("Backup tombstone identity is invalid")
            Tombstone.from_json(payload)
        for key, value in snapshot["metadata"]:
            if key.startswith("retained_e2b_cost:"):
                retained_cost(value)
        return {
            "user_version": str(maintenance._storage.info["schema_version"]),
            **{
                key: value
                for key, value in snapshot["metadata"]
                if key
                in {
                    "format",
                    "record_schema",
                    "artifact_store_id",
                    "artifact_schema",
                    "sandbox_schema",
                    "upload_schema",
                    "retention_schema",
                    "reclamation_schema",
                    "abandonment_schema",
                    "recovery_schema",
                }
            },
        }


async def _database(storage: SQLiteStorage, target: Path) -> None:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(target, isolation_level=None, check_same_thread=False)) as dest:
        async with storage._gate:
            storage._require_open()
            assert storage._connection is not None and storage._ownership is not None
            storage._ownership.check()
            settled = await _settle(storage._connection.backup(dest))
            if settled.error is not None:
                raise PersistenceUnavailable("SQLite backup API failed") from settled.error
            if settled.cancelled:
                raise asyncio.CancelledError
    fd = os.open(target, os.O_RDWR | NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


async def _artifacts(store: LocalArtifacts, target: Path) -> None:
    def copy() -> None:
        stages = store._scan()
        root = store._check()
        target.mkdir(mode=0o700)
        _transfer(
            store.directory / "store.json",
            target / "store.json",
            "artifacts/store.json",
            directory_fd=root,
        )
        for stage in stages:
            incoming = store.directory / stage.stage_id
            output = target / stage.stage_id
            output.mkdir(mode=0o700)
            fd = store._child(stage.stage_id)
            try:
                for name in sorted(os.listdir(fd)):
                    _transfer(
                        incoming / name,
                        output / name,
                        f"artifacts/{stage.stage_id}/{name}",
                        directory_fd=fd,
                    )
            finally:
                os.close(fd)
            if store._load(stage.stage_id) != stage:
                raise PersistenceUnavailable("Backup artifact stage changed")
            _sync(output)
        store._check()
        _sync(target)

    async with store._gate:
        await _io(copy)


async def _quarantine(
    root: Path, limits: ArtifactLimits | None, barrier: RecoveryBarrier
) -> dict[str, str]:
    from drov.storage.maintenance import SQLiteMaintenance

    files = LocalArtifacts(directory=root / "artifacts", limits=limits) if limits else None
    async with SQLiteMaintenance(root / "state.sqlite", artifacts=files) as maintenance:
        versions = await validate(maintenance)
        async with maintenance._storage.transaction() as tx:
            driver = maintenance._driver(tx)
            prior = await get(driver)
            if prior is not None:
                barrier = barrier.model_copy(
                    update={
                        "previous_sha256": hashlib.sha256(
                            prior.model_dump_json().encode()
                        ).hexdigest()
                    }
                )
            await install(driver, barrier)
        versions["recovery_schema"] = "1"
        return versions


async def backup(maintenance: "SQLiteMaintenance", destination: Path) -> BackupManifest:
    # Before creating a destination, refuse missing ready bytes or pending GC.
    await validate(maintenance)
    bundle = _Bundle(destination, create=True)
    try:
        partial = bundle.partial()
        snapshot_id = uuid4().hex
        created_at = maintenance._storage._clock()
        await _database(maintenance._storage, partial / "state.sqlite")
        store = maintenance._artifacts
        if store is not None:
            await _artifacts(store, partial / "artifacts")
        limits = store.limits if store else None
        versions = await _quarantine(
            partial,
            limits,
            RecoveryBarrier(snapshot_id=snapshot_id, origin="backup", created_at=created_at),
        )
        manifest = BackupManifest(
            snapshot_id=snapshot_id,
            created_at=created_at,
            versions=versions,
            artifact_store_id=store.store_id if store else None,
            artifact_limits=limits,
            files=await _io(lambda: _inventory(partial)),
        )
        await _io(lambda: _write(partial / "manifest.json", manifest))
        await _io(lambda: bundle.publish(partial))
        return manifest
    finally:
        bundle.close()


async def _verify(bundle: _Bundle, manifest: BackupManifest) -> None:
    from drov.storage.maintenance import SQLiteMaintenance

    data = bundle.path / "data"
    files = (
        LocalArtifacts(directory=data / "artifacts", limits=manifest.artifact_limits)
        if manifest.artifact_limits
        else None
    )
    async with SQLiteMaintenance(data / "state.sqlite", artifacts=files) as maintenance:
        if await validate(maintenance) != manifest.versions:
            raise PersistenceUnavailable("Backup versions differ from their manifest")
        async with maintenance._storage.transaction() as tx:
            barrier = await get(maintenance._driver(tx))
            if (
                barrier is None
                or barrier.origin != "backup"
                or barrier.snapshot_id != manifest.snapshot_id
            ):
                raise PersistenceUnavailable("Backup quarantine does not match its snapshot")
        if (files.store_id if files else None) != manifest.artifact_store_id:
            raise PersistenceUnavailable("Backup artifact identity differs from its manifest")


async def inspect(source: Path) -> BackupManifest:
    bundle = _Bundle(source, create=False)
    try:
        manifest, _ = await _io(lambda: _read(bundle))
        await _verify(bundle, manifest)
        return manifest
    finally:
        bundle.close()


async def restore(source: Path, destination: Path) -> RestoreResult:
    bundle = _Bundle(source, create=False)
    target = None
    source_owner = None
    try:
        manifest, raw = await _io(lambda: _read(bundle))
        await _verify(bundle, manifest)
        owner = FileOwnership(bundle.path / "data/state.sqlite")
        owner.acquire()
        source_owner = owner
        # Rehash under the database's original ownership lock as well.
        if await _io(lambda: _inventory(bundle.path / "data")) != manifest.files:
            raise PersistenceUnavailable("Backup changed before restore")
        target = _Bundle(destination, create=True)
        partial = target.partial()
        if manifest.artifact_store_id is not None:
            (partial / "artifacts").mkdir(mode=0o700)
        for item in manifest.files:
            path = partial / item.path
            if path.parent != partial and not path.parent.exists():
                path.parent.mkdir(mode=0o700)
            copied = await _io(bind(_transfer, bundle.path / "data" / item.path, path, item.path))
            if copied != item:
                raise PersistenceUnavailable("Backup changed during restore")
        barrier = RecoveryBarrier(
            snapshot_id=manifest.snapshot_id,
            origin="restore",
            created_at=datetime.now(UTC),
            source_manifest_sha256=hashlib.sha256(raw).hexdigest(),
        )
        versions = await _quarantine(partial, manifest.artifact_limits, barrier)
        if versions != manifest.versions:
            raise PersistenceUnavailable("Backup versions differ from their manifest")
        if manifest.artifact_store_id != versions.get("artifact_store_id"):
            raise PersistenceUnavailable("Backup artifact identity differs from its manifest")
        # Return the exact persisted barrier, including its predecessor hash.
        from drov.storage.maintenance import SQLiteMaintenance

        files = (
            LocalArtifacts(directory=partial / "artifacts", limits=manifest.artifact_limits)
            if manifest.artifact_limits
            else None
        )
        async with SQLiteMaintenance(partial / "state.sqlite", artifacts=files) as maintenance:
            async with maintenance._storage.transaction() as tx:
                saved = await get(maintenance._driver(tx))
                assert saved is not None
        result = RestoreResult(
            database=str(target.path / "data/state.sqlite"),
            artifacts=str(target.path / "data/artifacts") if files else None,
            artifact_limits=manifest.artifact_limits,
            barrier=saved,
        )
        await _io(lambda: _write(partial / "restored.json", result))
        for path in sorted(partial.rglob("*"), reverse=True):
            if path.is_dir():
                await _io(bind(_sync, path))
        await _io(lambda: target.publish(partial))
        return result
    finally:
        if source_owner is not None:
            source_owner.release()
        if target is not None:
            target.close()
        bundle.close()
