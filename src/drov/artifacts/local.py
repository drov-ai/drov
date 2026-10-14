"""Private byte staging, independent of transactional ready metadata.

The underscore methods are kernel boundaries, not upload/download APIs. A staged
file is never a ready artifact. Run grants and publication must be committed by
the Runtime in the subsequent integration batch.
"""

import asyncio
import hashlib
import hmac
import os
import stat
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, TypeVar
from uuid import uuid4

from pydantic import Field

from drov.artifacts.types import ArtifactInput, ArtifactLimits, ArtifactRef, FileName
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    PersistenceUnavailable,
    RecordNotFound,
    StorageInUse,
)
from drov.storage.records import MemoryScope
from drov.storage.sqlite import SQLiteStorage
from drov.types import Identifier, WireModel

T = TypeVar("T")
# Charge every reservation for its bounded manifest, including empty files.
LEDGER_BYTES = 16 * 1024
MAX_LEDGER_BYTES = LEDGER_BYTES
ID_PATTERN = r"^[0-9a-f]{32}$"
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class _Header(WireModel):
    schema_version: Literal[1] = 1
    format: Literal["drov.artifacts"] = "drov.artifacts"
    store_id: str = Field(pattern=ID_PATTERN)
    storage_binding: str = Field(pattern=r"^[0-9a-f]{64}$")


class _File(WireModel):
    ref: ArtifactRef
    name: FileName


class _Stage(WireModel):
    schema_version: Literal[1] = 1
    stage_id: str = Field(pattern=ID_PATTERN)
    owner: MemoryScope
    run_id: Identifier
    operation_id: Identifier | None
    purpose: Literal["input", "output"]
    state: Literal["reserved", "staged"] = "reserved"
    reserved_bytes: int = Field(ge=0)
    files: list[_File] = Field(default_factory=list, max_length=256)


async def _io(call: Callable[[], T]) -> T:
    """Settle the actual worker before releasing the gate, even after cancellation."""
    task = asyncio.create_task(asyncio.to_thread(call))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class LocalArtifacts:
    def __init__(self, *, directory: str | Path, limits: ArtifactLimits | None = None) -> None:
        self._directory = Path(directory).absolute()
        self._limits = ArtifactLimits.model_validate((limits or ArtifactLimits()).model_dump())
        self._gate = asyncio.Lock()
        self._fd: int | None = None
        self._lock: Any = None
        self._identity: tuple[int, int] | None = None
        self._lock_identity: tuple[int, int] | None = None
        self._header: _Header | None = None
        self._storage: SQLiteStorage | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def is_open(self) -> bool:
        return self._storage is not None

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def limits(self) -> ArtifactLimits:
        return self._limits

    @property
    def store_id(self) -> str:
        self._require_open()
        assert self._header is not None
        return self._header.store_id

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and loop is not self._loop:
            raise ConfigurationError("Artifact store belongs to another event loop")
        self._loop = loop

    async def open(self, *, storage: SQLiteStorage) -> None:
        self._check_loop()
        async with self._gate:
            if self._fd is not None or self.is_open:
                raise StorageInUse("Artifact store is already open")
            if not isinstance(storage, SQLiteStorage) or not storage.is_open:
                raise ConfigurationError("Artifact bytes require an open SQLiteStorage")
            storage._require_open()
            binding = hmac.new(storage._secret, b"drov.artifacts.pair.v1", "sha256").hexdigest()
            try:
                await _io(lambda: self._open(binding))
                assert self._header is not None
                await storage._bind_artifacts(self._header.store_id)
                self._storage = storage
            except BaseException:
                await _io(self._close)
                raise

    def _private(self, info: os.stat_result, *, directory: bool = False) -> None:
        if (
            (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or (not directory and info.st_nlink != 1)
        ):
            raise ConfigurationError("Artifact storage requires private owned directories/files")

    def _open(self, binding: str) -> None:
        if os.name != "posix" or not NOFOLLOW or not hasattr(os, "O_DIRECTORY"):
            raise ConfigurationError("Private artifact storage currently requires POSIX controls")
        # Refuse symlink ancestors; all later access is anchored to open directory FDs.
        for path in [*reversed(self.directory.parents), self.directory]:
            if path.is_symlink():
                raise ConfigurationError("Artifact directory/ancestors cannot be symlinks")
        if not self.directory.parent.is_dir():
            raise ConfigurationError("Artifact parent directory must exist")
        try:
            self.directory.mkdir(mode=0o700)
            parent_fd = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except FileExistsError:
            pass
        root_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW)
        self._fd = root_fd
        info = os.fstat(root_fd)
        self._private(info, directory=True)
        self._identity = (info.st_dev, info.st_ino)
        lock_fd = os.open(
            "ownership.lock", os.O_CREAT | os.O_RDWR | NOFOLLOW, 0o600, dir_fd=root_fd
        )
        lock_file = os.fdopen(lock_fd, "r+b")
        try:
            self._private(os.fstat(lock_fd))
            # Import only on explicit open, preserving SDK import purity.
            import portalocker

            try:
                portalocker.lock(lock_file, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.exceptions.LockException as exc:
                raise StorageInUse("Artifact store is owned by another process") from exc
            self._lock = lock_file
            lock_info = os.fstat(lock_fd)
            self._lock_identity = (lock_info.st_dev, lock_info.st_ino)
        except BaseException:
            lock_file.close()
            raise
        os.fsync(root_fd)
        entries = set(os.listdir(root_fd))
        if "store.json" in entries:
            try:
                self._header = _Header.from_json(self._read_json(root_fd, "store.json"))
            except (ValueError, TypeError) as exc:
                raise PersistenceUnavailable("Artifact store header is invalid") from exc
            if self._header.storage_binding != binding:
                raise Conflict("Artifact directory is paired to another SQLite storage")
        else:
            if entries - {"ownership.lock"}:
                raise ConfigurationError("Unpaired artifact directory is not empty")
            self._header = _Header(store_id=uuid4().hex, storage_binding=binding)
            self._json(root_fd, "store.json", self._header)
        stages = self._scan()
        self._capacity(stages)

    def _check(self) -> int:
        if self._fd is None or self._lock is None:
            raise ConfigurationError("Artifact store is not open")
        try:
            current = self.directory.lstat()
            self._private(current, directory=True)
            if (current.st_dev, current.st_ino) != self._identity:
                raise PersistenceUnavailable("Artifact directory was replaced")
            info = os.stat("ownership.lock", dir_fd=self._fd, follow_symlinks=False)
            self._private(info)
            if (info.st_dev, info.st_ino) != self._lock_identity:
                raise PersistenceUnavailable("Artifact ownership lock was replaced")
        except OSError as exc:
            raise PersistenceUnavailable("Artifact directory is unavailable") from exc
        return self._fd

    def _require_open(self) -> None:
        self._check_loop()
        if self._storage is None:
            raise ConfigurationError("Artifact store is not paired/open")
        self._storage._require_open()

    def _require_pair(self, storage: SQLiteStorage) -> None:
        self._require_open()
        if self._storage is not storage:
            raise ConfigurationError("Artifact store is paired to a different open storage")

    def _child(self, stage_id: str) -> int:
        # Validate before using an identifier as a path component.
        import re

        if not re.fullmatch(ID_PATTERN, stage_id):
            raise RecordNotFound("Artifact stage is unavailable")
        try:
            fd = os.open(stage_id, os.O_RDONLY | os.O_DIRECTORY | NOFOLLOW, dir_fd=self._check())
        except OSError as exc:
            raise PersistenceUnavailable("Artifact stage is unavailable") from exc
        try:
            self._private(os.fstat(fd), directory=True)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _json(self, fd: int, name: str, value: WireModel) -> None:
        data = value.model_dump_json().encode("utf-8")
        if len(data) > MAX_LEDGER_BYTES:
            raise CapacityExceeded("Artifact stage metadata exceeds its reserved capacity")
        temporary = "pending_" + uuid4().hex
        file_fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600, dir_fd=fd
        )
        try:
            with os.fdopen(file_fd, "wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass

    def _read_json(self, fd: int, name: str) -> bytes:
        try:
            file_fd = os.open(name, os.O_RDONLY | NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(file_fd, "rb") as source:
                self._private(os.fstat(source.fileno()))
                data = source.read(MAX_LEDGER_BYTES + 1)
        except OSError as exc:
            raise PersistenceUnavailable("Artifact stage metadata is unavailable") from exc
        if len(data) > MAX_LEDGER_BYTES:
            raise PersistenceUnavailable("Artifact stage metadata exceeds its bound")
        return data

    def _load(self, stage_id: str) -> _Stage:
        fd = self._child(stage_id)
        try:
            stage = _Stage.from_json(self._read_json(fd, "stage.json"))
            if stage.stage_id != stage_id:
                raise PersistenceUnavailable("Artifact stage identity does not match its ledger")
            if len({item.ref.artifact_id for item in stage.files}) != len(stage.files):
                raise PersistenceUnavailable("Artifact ledger repeats a byte identity")
            if sum(item.ref.size_bytes for item in stage.files) > stage.reserved_bytes:
                raise PersistenceUnavailable("Artifact files exceed their reservation")
            allowed = {"stage.json", *(item.ref.artifact_id for item in stage.files)}
            # A torn directory/rename needs explicit recovery; never discard its evidence.
            for name in os.listdir(fd):
                if name not in allowed:
                    raise PersistenceUnavailable("Unexpected artifact staging entry")
            return stage
        except (ValueError, TypeError) as exc:
            raise PersistenceUnavailable("Artifact ledger is invalid or incompatible") from exc
        finally:
            os.close(fd)

    def _scan(self) -> list[_Stage]:
        import re

        stages = []
        for name in os.listdir(self._check()):
            if name in ("store.json", "ownership.lock"):
                continue
            if not re.fullmatch(ID_PATTERN, name):
                raise PersistenceUnavailable("Unexpected artifact store entry")
            stages.append(self._load(name))
        return stages

    def _capacity(self, stages: Sequence[_Stage]) -> None:
        total = sum(stage.reserved_bytes + LEDGER_BYTES for stage in stages)
        if total > self.limits.max_store_bytes:
            raise CapacityExceeded("Artifact store capacity exhausted")
        owners: dict[MemoryScope, int] = {}
        for stage in stages:
            owners[stage.owner] = owners.get(stage.owner, 0) + stage.reserved_bytes + LEDGER_BYTES
        if any(value > self.limits.max_owner_bytes for value in owners.values()):
            raise CapacityExceeded("Artifact owner capacity exhausted")

    async def _reserve(
        self,
        *,
        owner: MemoryScope,
        run_id: str,
        operation_id: str | None,
        purpose: Literal["input", "output"],
        size_bytes: int,
    ) -> _Stage:
        self._require_open()
        stage = _Stage(
            stage_id=uuid4().hex,
            owner=owner,
            run_id=run_id,
            operation_id=operation_id,
            purpose=purpose,
            reserved_bytes=size_bytes,
        )
        bound = self.limits.max_input_bytes if purpose == "input" else self.limits.max_export_bytes
        if size_bytes > bound:
            raise CapacityExceeded("Artifact reservation exceeds the file transfer limit")

        def reserve() -> _Stage:
            stages = self._scan()
            self._capacity([*stages, stage])
            root = self._check()
            os.mkdir(stage.stage_id, 0o700, dir_fd=root)
            # If interrupted before the manifest exists, open fails closed; never reset usage.
            os.fsync(root)
            fd = self._child(stage.stage_id)
            try:
                self._json(fd, "stage.json", stage)
            finally:
                os.close(fd)
            return stage

        async with self._gate:
            return await _io(reserve)

    async def _stage(self, stage_id: str, inputs: Sequence[ArtifactInput]) -> _Stage:
        self._require_open()
        # Copy/validate the sequence now; mutable containers cannot change queued bytes.
        files = [
            ArtifactInput(name=item.name, data=item.data, media_type=item.media_type)
            for item in inputs
        ]
        if len({item.name for item in files}) != len(files):
            raise ConfigurationError("Artifact names repeat after normalization")

        def write() -> _Stage:
            original = self._load(stage_id)
            if original.state != "reserved" or original.files:
                raise Conflict("Artifact stage has already begun writing")
            file_limit = (
                self.limits.max_input_files
                if original.purpose == "input"
                else self.limits.max_output_files
            )
            actual = sum(len(item.data) for item in files)
            if (
                len(files) > file_limit
                or actual > original.reserved_bytes
                or (
                    original.purpose == "output"
                    and any(len(item.data) > self.limits.max_artifact_bytes for item in files)
                )
            ):
                raise CapacityExceeded("Artifact files exceed the reserved limits")
            planned = original.model_copy(
                update={
                    "files": [
                        _File(
                            name=item.name,
                            ref=ArtifactRef(
                                artifact_id="art_" + uuid4().hex,
                                sha256=hashlib.sha256(item.data).hexdigest(),
                                size_bytes=len(item.data),
                                media_type=item.media_type,
                            ),
                        )
                        for item in files
                    ]
                }
            )
            fd = self._child(stage_id)
            try:
                self._json(fd, "stage.json", planned)
                for item, entry in zip(files, planned.files, strict=True):
                    byte_fd = os.open(
                        entry.ref.artifact_id,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW,
                        0o600,
                        dir_fd=fd,
                    )
                    with os.fdopen(byte_fd, "wb") as target:
                        target.write(item.data)
                        target.flush()
                        os.fsync(target.fileno())
                os.fsync(fd)
                staged = planned.model_copy(update={"state": "staged", "reserved_bytes": actual})
                self._json(fd, "stage.json", staged)
                return staged
            finally:
                os.close(fd)

        async with self._gate:
            return await _io(write)

    async def _read_staged(
        self, owner: MemoryScope, stage_id: str, ref: ArtifactRef, *, name: str | None = None
    ) -> bytes:
        self._require_open()

        def read() -> bytes:
            stage = self._load(stage_id)
            if (
                stage.state != "staged"
                or stage.owner != owner
                or not any(
                    item.ref == ref and (name is None or item.name == name) for item in stage.files
                )
            ):
                raise RecordNotFound("Artifact bytes are unavailable")
            bound = (
                self.limits.max_input_bytes
                if stage.purpose == "input"
                else self.limits.max_artifact_bytes
            )
            if ref.size_bytes > bound:
                raise CapacityExceeded("Artifact exceeds the configured read bound")
            fd = self._child(stage_id)
            try:
                byte_fd = os.open(
                    ref.artifact_id, os.O_RDONLY | NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
                )
                with os.fdopen(byte_fd, "rb") as source:
                    info = os.fstat(source.fileno())
                    self._private(info)
                    if info.st_size != ref.size_bytes:
                        raise PersistenceUnavailable(
                            "Artifact size differs from its saved reference"
                        )
                    data = source.read(ref.size_bytes + 1)
                    after = os.fstat(source.fileno())
                    if (
                        len(data) != ref.size_bytes
                        or hashlib.sha256(data).hexdigest() != ref.sha256
                        or (info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
                        != (after.st_ino, after.st_mtime_ns, after.st_ctime_ns, after.st_size)
                    ):
                        raise PersistenceUnavailable(
                            "Artifact bytes changed or hash verification failed"
                        )
                    return data
            except OSError as exc:
                raise PersistenceUnavailable("Artifact bytes are unavailable") from exc
            finally:
                os.close(fd)

        async with self._gate:
            return await _io(read)

    async def _pending(self) -> list[_Stage]:
        self._require_open()
        async with self._gate:
            return await _io(self._scan)

    def _close(self) -> None:
        if self._lock is not None:
            self._lock.close()
            self._lock = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._storage = None
        self._identity = self._lock_identity = None
        self._header = None

    async def aclose(self) -> None:
        self._check_loop()
        async with self._gate:
            await _io(self._close)
