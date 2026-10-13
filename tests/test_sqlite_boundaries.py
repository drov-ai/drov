import asyncio
import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from drov.errors import (
    ConfigurationError,
    IncompatibleStorage,
    PersistenceUnavailable,
    RecordNotFound,
    ShutdownIncomplete,
    StorageInUse,
)
from drov.storage import SQLiteStorage
from tests.test_storage import NOW, start_request


@contextmanager
def sql_connection(path: Path, **options: Any) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, **options)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_descriptors_do_not_open_and_settings_are_verified(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    lock_path = Path(str(path) + ".drov.lock")
    storage = SQLiteStorage(path)
    assert not path.exists()
    assert not await asyncio.to_thread(lock_path.exists)
    with pytest.raises(ConfigurationError):
        await storage.start(start_request())
    async with storage:
        assert storage.info["schema_version"] == 1
        assert storage.info["record_schema"] == 1
        assert storage.info["journal_mode"] == "delete"
        assert storage.info["synchronous"] == 2
        assert storage.info["foreign_keys"] == 1
        assert storage.info["busy_timeout"] == 5000
        if os.name == "posix":
            assert path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(StorageInUse):
            await storage.open()
    assert await asyncio.to_thread(lock_path.exists)
    async with SQLiteStorage(path):
        pass


@pytest.mark.asyncio
async def test_persistence_and_secret_dedup_across_new_instances(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    request = start_request(key="raw-sensitive-key")
    async with SQLiteStorage(path, clock=lambda: NOW) as storage:
        ref = await storage.start(request)
    with sql_connection(path) as connection:
        key, payload = connection.execute(
            "SELECT key,payload FROM records WHERE kind='start_key'"
        ).fetchone()
        assert len(key) == 64 and "raw-sensitive-key" not in key + payload
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    async with SQLiteStorage(path) as storage:
        assert await storage.start(request) == ref
        assert (await storage.get_checkpoint(request.scope, ref.run_id)).state_version == 1
        assert [event.seq for event in await storage.events(request.scope, ref.run_id)] == [1]


@pytest.mark.asyncio
async def test_same_path_and_symlink_ownership(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    async with SQLiteStorage(path):
        with pytest.raises(StorageInUse):
            await SQLiteStorage(tmp_path / "." / "state.db").open()
        if os.name != "nt":
            alias = tmp_path / "alias.db"
            alias.symlink_to(path)
            with pytest.raises(StorageInUse):
                await SQLiteStorage(alias).open()


@pytest.mark.asyncio
async def test_hardlinks_and_non_files_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    async with SQLiteStorage(path):
        pass
    alias = tmp_path / "hard.db"
    os.link(path, alias)
    for candidate in (path, alias, tmp_path):
        with pytest.raises(ConfigurationError):
            await SQLiteStorage(candidate).open()


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [0, 2, 999])
async def test_unsupported_database_is_not_modified(tmp_path: Path, schema: int) -> None:
    path = tmp_path / "foreign.db"
    with sql_connection(path) as connection:
        connection.execute("CREATE TABLE foreign_table (value TEXT)")
        connection.execute(f"PRAGMA user_version={schema}")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    storage = SQLiteStorage(path)
    with pytest.raises(IncompatibleStorage):
        await storage.open()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    # Ownership must have been released even though this database cannot be opened.
    with pytest.raises(IncompatibleStorage):
        await SQLiteStorage(path).open()


@pytest.mark.asyncio
async def test_schema_one_metadata_is_checked_before_writes(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    async with SQLiteStorage(path):
        pass
    with sql_connection(path) as connection:
        connection.execute("UPDATE metadata SET value='2' WHERE key='record_schema'")
    before = path.read_bytes()
    with pytest.raises(IncompatibleStorage):
        await SQLiteStorage(path).open()
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_statement_failure_after_partial_writes_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = SQLiteStorage(tmp_path / "state.db")
    async with storage:
        original = storage._raw

        async def fault(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            if sql.startswith("INSERT INTO records") and parameters[0] == "checkpoint":
                raise sqlite3.OperationalError("injected I/O failure")
            return await original(sql, parameters)

        monkeypatch.setattr(storage, "_raw", fault)
        with pytest.raises(PersistenceUnavailable):
            await storage.start(start_request())
        monkeypatch.setattr(storage, "_raw", original)
        with pytest.raises(RecordNotFound):
            await storage.get_session(start_request().scope)
        with sql_connection(storage.path) as connection:
            assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before", "after"])
async def test_commit_error_never_returns_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    path = tmp_path / "state.db"
    storage = SQLiteStorage(path)
    await storage.open()
    original = storage._raw

    async def fault(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
        if sql == "COMMIT":
            if phase == "after":
                await original(sql, parameters)
            raise sqlite3.OperationalError("injected commit failure")
        return await original(sql, parameters)

    monkeypatch.setattr(storage, "_raw", fault)
    try:
        with pytest.raises(PersistenceUnavailable):
            await storage.start(start_request())
        with pytest.raises(PersistenceUnavailable):
            await storage.get_session(start_request().scope)
    finally:
        monkeypatch.setattr(storage, "_raw", original)
        await storage.aclose()
    async with SQLiteStorage(path) as reopened:
        if phase == "before":
            with pytest.raises(RecordNotFound):
                await reopened.get_session(start_request().scope)
        else:
            ref = await reopened.start(start_request())
            assert (await reopened.get_session(start_request().scope)).active_run_id == ref.run_id
            assert (
                await reopened.get_checkpoint(start_request().scope, ref.run_id)
            ).state_version == 1
            assert len(await reopened.events(start_request().scope, ref.run_id)) == 1


@pytest.mark.asyncio
async def test_uncertain_rollback_keeps_ownership_until_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    storage = SQLiteStorage(path)
    await storage.open()
    original = storage._raw

    async def fault(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
        if sql in ("COMMIT", "ROLLBACK"):
            raise sqlite3.OperationalError("injected uncertain transaction")
        return await original(sql, parameters)

    monkeypatch.setattr(storage, "_raw", fault)
    try:
        with pytest.raises(PersistenceUnavailable):
            await storage.start(start_request())
        with pytest.raises(ShutdownIncomplete):
            await storage.aclose()
        with pytest.raises(StorageInUse):
            await SQLiteStorage(path).open()
        with pytest.raises(PersistenceUnavailable):
            await storage.start(start_request(session="another"))
    finally:
        monkeypatch.setattr(storage, "_raw", original)
        await storage.aclose()
    async with SQLiteStorage(path) as reopened:
        with pytest.raises(RecordNotFound):
            await reopened.get_session(start_request().scope)


@pytest.mark.asyncio
async def test_cancel_during_commit_waits_and_keeps_atomic_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    storage = SQLiteStorage(path)
    async with storage:
        entered = asyncio.Event()
        release = asyncio.Event()
        original = storage._raw

        async def gated(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            if sql == "COMMIT":
                entered.set()
                await release.wait()
            return await original(sql, parameters)

        monkeypatch.setattr(storage, "_raw", gated)
        task = asyncio.create_task(storage.start(start_request()))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        reader = asyncio.create_task(storage.get_session(start_request().scope))
        await asyncio.sleep(0)
        assert not reader.done()
        with pytest.raises(StorageInUse):
            await SQLiteStorage(path).open()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        session = await reader
        assert session.active_run_id is not None
        ref = await storage.start(start_request())
        assert ref.run_id == session.active_run_id
        assert (await storage.get_run(start_request().scope, ref.run_id)).last_seq == 1
        assert (await storage.get_checkpoint(start_request().scope, ref.run_id)).state_version == 1
        assert len(await storage.events(start_request().scope, ref.run_id)) == 1


@pytest.mark.asyncio
async def test_cancel_while_real_sqlite_begin_waits_for_external_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    async with SQLiteStorage(path) as storage:
        entered = asyncio.Event()
        original = storage._raw

        async def observed(sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
            if sql == "BEGIN IMMEDIATE":
                entered.set()
            return await original(sql, parameters)

        with sql_connection(path, isolation_level=None) as outside:
            outside.execute("BEGIN IMMEDIATE")
            monkeypatch.setattr(storage, "_raw", observed)
            task = asyncio.create_task(storage.start(start_request()))
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            outside.execute("ROLLBACK")
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(RecordNotFound):
            await storage.get_session(start_request().scope)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="Windows disallows replacing an open SQLite file")
async def test_database_replacement_stops_transactions(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    async with SQLiteStorage(path) as storage:
        ref = await storage.start(start_request())
        path.rename(tmp_path / "original.db")
        path.write_bytes(b"replacement")
        with pytest.raises(PersistenceUnavailable):
            await storage.get_run(start_request().scope, ref.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["owner", "checkpoint"])
async def test_payload_index_mismatch_is_not_returned(tmp_path: Path, corruption: str) -> None:
    path = tmp_path / "state.db"
    async with SQLiteStorage(path) as storage:
        ref = await storage.start(start_request())
    kind = "run" if corruption == "owner" else "checkpoint"
    with sql_connection(path) as connection:
        payload = connection.execute(
            "SELECT payload FROM records WHERE kind=?", (kind,)
        ).fetchone()[0]
        value = json.loads(payload)
        if corruption == "owner":
            value["scope"]["user_id"] = "bob"
        else:
            value["state_version"] = 2
        connection.execute("UPDATE records SET payload=? WHERE kind=?", (json.dumps(value), kind))
    async with SQLiteStorage(path) as storage:
        with pytest.raises(PersistenceUnavailable):
            if corruption == "owner":
                await storage.get_run(start_request().scope, ref.run_id)
            else:
                await storage.get_checkpoint(start_request().scope, ref.run_id)
