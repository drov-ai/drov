import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from drov.artifacts import ArtifactInfo, ArtifactInput, ArtifactLimits, LocalArtifacts
from drov.artifacts.local import LEDGER_BYTES
from drov.artifacts.types import file_path
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    PersistenceUnavailable,
    RecordNotFound,
    StorageInUse,
)
from drov.storage import MemoryStorage, SQLiteStorage
from drov.storage.records import MemoryScope

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private byte store subrange")
OWNER = MemoryScope(tenant_id="tenant", user_id="alice", agent_name="analysis")


@pytest.mark.parametrize(
    "name", ["", "../x", "/x", "x/y", "x\\y", ".", "..", "x\x00", "x\x7f", "x\x85", "é" * 128]
)
def test_input_rejects_unsafe_name(name: str) -> None:
    with pytest.raises(ValidationError):
        ArtifactInput(name=name, data=b"x")


@pytest.mark.parametrize("path", ["/x", "x/../y", "x//y", "x/./y", "a/" * 8 + "x"])
def test_output_path_rejects_escape_or_unbounded_depth(path: str) -> None:
    with pytest.raises(ValueError):
        file_path(path)


def test_values_normalize_and_keep_bytes_out_of_json() -> None:
    value = ArtifactInput(name="e\u0301.csv", data=b"private", media_type="Text/CSV")
    assert value.name == "é.csv" and value.media_type == "text/csv"
    assert "private" not in repr(value) and "data" not in value.model_dump()
    assert file_path("charts/e\u0301.png") == "charts/é.png"
    with pytest.raises(ValidationError):
        ArtifactInput(name="x", data="x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ArtifactInput(name="x", data=b"x", media_type="text/html\r\nX: injected")
    with pytest.raises(ValidationError):
        ArtifactLimits(max_input_files=True)


def test_metadata_cannot_leak_name_without_content_authorization() -> None:
    data = {
        "artifact_id": "art_" + "a" * 32,
        "sha256": "b" * 64,
        "size_bytes": 2,
        "media_type": "text/csv",
        "run_id": "run",
        "state": "ready",
    }
    info = ArtifactInfo.model_validate(data)
    assert info.name is None and info.content_visible is False
    with pytest.raises(ValidationError):
        ArtifactInfo.model_validate(data | {"name": "secret.csv"})


@pytest.mark.asyncio
async def test_real_staging_survives_reopen_and_pairs_both_directions(tmp_path: Path) -> None:
    directory = tmp_path / "files"
    store = LocalArtifacts(directory=directory)
    assert not directory.exists()  # Construction is inert.
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        await store.open(storage=storage)
        identity = store.store_id
        reserve = await store._reserve(
            owner=OWNER, run_id="run", operation_id=None, purpose="input", size_bytes=100
        )
        staged = await store._stage(
            reserve.stage_id, [ArtifactInput(name="values.csv", data=b"v\n3\n")]
        )
        ref = staged.files[0].ref
        assert ref.sha256 == hashlib.sha256(b"v\n3\n").hexdigest()
        assert staged.state == "staged" and staged.reserved_bytes == 4
        assert await store._read_staged(OWNER, staged.stage_id, ref) == b"v\n3\n"
        with pytest.raises(Conflict):
            await store._stage(staged.stage_id, [ArtifactInput(name="values.csv", data=b"other")])
        await store.aclose()
        other_directory = LocalArtifacts(directory=tmp_path / "other")
        with pytest.raises(Conflict):
            await other_directory.open(storage=storage)
        assert not other_directory.is_open
    async with SQLiteStorage(tmp_path / "state.db") as reopened:
        await store.open(storage=reopened)
        assert store.store_id == identity
        assert await store._read_staged(OWNER, staged.stage_id, ref) == b"v\n3\n"
        assert (directory.stat().st_mode & 0o777) == 0o700
        for child in directory.rglob("*"):
            assert child.stat().st_mode & 0o777 == (0o700 if child.is_dir() else 0o600)
        await store.aclose()
    async with SQLiteStorage(tmp_path / "foreign.db") as foreign:
        with pytest.raises(Conflict):
            await store.open(storage=foreign)
        assert not store.is_open


@pytest.mark.asyncio
async def test_requires_open_sqlite(tmp_path: Path) -> None:
    store = LocalArtifacts(directory=tmp_path / "files")
    with pytest.raises(ConfigurationError):
        await store.open(storage=SQLiteStorage(tmp_path / "state.db"))
    async with MemoryStorage() as memory:
        with pytest.raises(ConfigurationError):
            await store.open(storage=memory)  # type: ignore[arg-type]
    assert not store.directory.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_root", ["public", "symlink", "ancestor", "nonempty"])
async def test_refuses_unsafe_existing_directory(tmp_path: Path, bad_root: str) -> None:
    directory = tmp_path / "files"
    if bad_root == "public":
        directory.mkdir(mode=0o755)
    elif bad_root in ("symlink", "ancestor"):
        actual = tmp_path / "actual"
        actual.mkdir(mode=0o700)
        directory.symlink_to(actual, target_is_directory=True)
        if bad_root == "ancestor":
            directory = directory / "nested"
    else:
        directory.mkdir(mode=0o700)
        (directory / "foreign").write_bytes(b"preserve")
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        store = LocalArtifacts(directory=directory)
        with pytest.raises(ConfigurationError):
            await store.open(storage=storage)
        assert not store.is_open


@pytest.mark.asyncio
async def test_quotas_are_reserved_before_bytes_and_persist_across_reopen(tmp_path: Path) -> None:
    limits = ArtifactLimits(max_owner_bytes=LEDGER_BYTES + 10, max_store_bytes=3 * LEDGER_BYTES)
    store = LocalArtifacts(directory=tmp_path / "files", limits=limits)
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        await store.open(storage=storage)
        stages = await asyncio.gather(
            *[
                store._reserve(
                    owner=OWNER, run_id="run", operation_id="op", purpose="output", size_bytes=10
                )
                for _ in range(2)
            ],
            return_exceptions=True,
        )
        assert sum(isinstance(value, CapacityExceeded) for value in stages) == 1
        stage = (await store._pending())[0]
        assert stage.state == "reserved" and not stage.files
        with pytest.raises(CapacityExceeded):
            await store._stage(stage.stage_id, [ArtifactInput(name="x", data=b"x" * 11)])
        assert not list((store.directory / stage.stage_id).glob("art_*"))
        await store.aclose()
        await store.open(storage=storage)
        assert (await store._pending()) == [stage]
        with pytest.raises(CapacityExceeded):
            await store._reserve(
                owner=OWNER, run_id="new", operation_id=None, purpose="input", size_bytes=0
            )
        await store.aclose()


@pytest.mark.asyncio
async def test_store_quota_covers_multiple_owners_and_empty_stages(tmp_path: Path) -> None:
    limits = ArtifactLimits(max_owner_bytes=LEDGER_BYTES, max_store_bytes=2 * LEDGER_BYTES)
    store = LocalArtifacts(directory=tmp_path / "files", limits=limits)
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        await store.open(storage=storage)
        for name in ("alice", "bob"):
            scope = OWNER.model_copy(update={"user_id": name})
            await store._reserve(
                owner=scope, run_id=name, operation_id=None, purpose="input", size_bytes=0
            )
        with pytest.raises(CapacityExceeded):
            await store._reserve(
                owner=OWNER.model_copy(update={"user_id": "eve"}),
                run_id="run",
                operation_id=None,
                purpose="input",
                size_bytes=0,
            )
        await store.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_bytes", ["missing", "hash", "oversize", "symlink", "hardlink", "fifo", "public"]
)
async def test_read_verifies_real_file_and_never_follows_links(
    tmp_path: Path, bad_bytes: str
) -> None:
    store = LocalArtifacts(directory=tmp_path / "files")
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        await store.open(storage=storage)
        reserve = await store._reserve(
            owner=OWNER, run_id="run", operation_id=None, purpose="input", size_bytes=4
        )
        staged = await store._stage(reserve.stage_id, [ArtifactInput(name="x", data=b"data")])
        ref = staged.files[0].ref
        target = store.directory / staged.stage_id / ref.artifact_id
        if bad_bytes == "hash":
            target.write_bytes(b"evil")
        elif bad_bytes == "oversize":
            target.write_bytes(b"exceeds saved length")
        elif bad_bytes == "hardlink":
            os.link(target, tmp_path / "linked")
        elif bad_bytes == "public":
            target.chmod(0o644)
        else:
            target.unlink()
            if bad_bytes == "symlink":
                (tmp_path / "secret").write_bytes(b"data")
                target.symlink_to(tmp_path / "secret")
            elif bad_bytes == "fifo":
                os.mkfifo(target, 0o600)
        with pytest.raises((PersistenceUnavailable, ConfigurationError)):
            await asyncio.wait_for(store._read_staged(OWNER, staged.stage_id, ref), 2)
        await store.aclose()


@pytest.mark.asyncio
async def test_owner_id_hash_and_reserved_state_do_not_grant_bytes(tmp_path: Path) -> None:
    store = LocalArtifacts(directory=tmp_path / "files")
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        await store.open(storage=storage)
        reserve = await store._reserve(
            owner=OWNER, run_id="run", operation_id=None, purpose="input", size_bytes=1
        )
        staged = await store._stage(reserve.stage_id, [ArtifactInput(name="x", data=b"x")])
        ref = staged.files[0].ref
        for changed in ({"tenant_id": "other"}, {"user_id": "other"}, {"agent_name": "other"}):
            with pytest.raises(RecordNotFound):
                await store._read_staged(OWNER.model_copy(update=changed), staged.stage_id, ref)
        with pytest.raises(RecordNotFound):
            await store._read_staged(
                OWNER, staged.stage_id, ref.model_copy(update={"sha256": "f" * 64})
            )
        reserved = await store._reserve(
            owner=OWNER, run_id="run", operation_id=None, purpose="input", size_bytes=1
        )
        with pytest.raises(RecordNotFound):
            await store._read_staged(OWNER, reserved.stage_id, ref)
        await store.aclose()


@pytest.mark.asyncio
async def test_partial_disk_failure_retains_charge_and_never_becomes_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalArtifacts(directory=tmp_path / "files")
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        await store.open(storage=storage)
        reserve = await store._reserve(
            owner=OWNER, run_id="run", operation_id="op", purpose="output", size_bytes=100
        )
        original = store._json

        def fail_publication(fd: int, name: str, value: Any) -> None:
            if getattr(value, "state", None) == "staged":
                raise OSError("synthetic disk failure at staged ledger")
            original(fd, name, value)

        with monkeypatch.context() as patch:
            patch.setattr(store, "_json", fail_publication)
            with pytest.raises(OSError):
                await store._stage(reserve.stage_id, [ArtifactInput(name="x", data=b"kept")])
        await store.aclose()
        await store.open(storage=storage)
        pending = (await store._pending())[0]
        assert pending.state == "reserved" and pending.reserved_bytes == 100
        ref = pending.files[0].ref
        assert (store.directory / pending.stage_id / ref.artifact_id).read_bytes() == b"kept"
        with pytest.raises(RecordNotFound):
            await store._read_staged(OWNER, pending.stage_id, ref)
        with pytest.raises(Conflict):
            await store._stage(pending.stage_id, [ArtifactInput(name="x", data=b"redo")])
        await store.aclose()


@pytest.mark.asyncio
async def test_cancellation_settles_worker_before_gate_or_lock_is_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalArtifacts(directory=tmp_path / "files")
    entered, release = threading.Event(), threading.Event()
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        await store.open(storage=storage)
        original = store._scan

        def wait_in_worker() -> Any:
            entered.set()
            assert release.wait(5)
            return original()

        monkeypatch.setattr(store, "_scan", wait_in_worker)
        worker = asyncio.create_task(store._pending())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            worker.cancel()
            close = asyncio.create_task(store.aclose())
            await asyncio.sleep(0.01)
            assert not worker.done() and not close.done() and store.is_open
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await worker
        await close
        assert not store.is_open


@pytest.mark.asyncio
async def test_directory_and_lock_replacement_detected(tmp_path: Path) -> None:
    store = LocalArtifacts(directory=tmp_path / "files")
    async with SQLiteStorage(tmp_path / "state.db") as storage:
        await store.open(storage=storage)
        (store.directory / "ownership.lock").unlink()
        (store.directory / "ownership.lock").touch(mode=0o600)
        with pytest.raises(PersistenceUnavailable):
            await store._pending()
        await store.aclose()


OWNER_PROCESS = """
import asyncio,json,sys
from drov.artifacts import ArtifactInput,LocalArtifacts
from drov.storage import SQLiteStorage
from drov.storage.records import MemoryScope
async def main():
    async with SQLiteStorage(sys.argv[1]) as storage:
        files=LocalArtifacts(directory=sys.argv[2])
        await files.open(storage=storage)
        reservation=await files._reserve(owner=MemoryScope(tenant_id='tenant',user_id='alice',
            agent_name='analysis'),run_id='run',operation_id=None,purpose='input',size_bytes=4)
        stage=await files._stage(reservation.stage_id,[ArtifactInput(name='x',data=b'data')])
        print(stage.model_dump_json(),flush=True)
        await asyncio.to_thread(sys.stdin.readline)
        await files.aclose()
asyncio.run(main())
"""


@pytest.mark.asyncio
async def test_independent_process_crash_preserves_ledger_and_bytes(tmp_path: Path) -> None:
    database, directory = tmp_path / "state.db", tmp_path / "files"
    process = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, "-c", OWNER_PROCESS, str(database), str(directory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 10)
        assert line
        stage = json.loads(line)
        # Different DB avoids testing only the pre-existing SQLite ownership lock.
        async with SQLiteStorage(tmp_path / "contender.db") as storage:
            contender = LocalArtifacts(directory=directory)
            with pytest.raises(StorageInUse):
                await contender.open(storage=storage)
        process.kill()
        await asyncio.to_thread(process.wait, 10)
        async with SQLiteStorage(database) as storage:
            recovered = LocalArtifacts(directory=directory)
            await recovered.open(storage=storage)
            manifest = (await recovered._pending())[0]
            assert manifest.stage_id == stage["stage_id"]
            assert (
                await recovered._read_staged(OWNER, manifest.stage_id, manifest.files[0].ref)
                == b"data"
            )
            await recovered.aclose()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
