"""Single-connection SQLite backend with explicit cancellation settlement."""

import asyncio
import secrets
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, TypeVar, cast

import aiosqlite

from drov.errors import (
    Conflict,
    IncompatibleStorage,
    PersistenceUnavailable,
    ShutdownIncomplete,
    StorageInUse,
)
from drov.storage._driver import Driver, Key, Query, Row
from drov.storage._ownership import FileOwnership
from drov.storage.base import Storage

T = TypeVar("T")
SCHEMA_VERSION = 1
RECORD_VERSION = 1
DDL = (
    "CREATE TABLE metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)",
    """CREATE TABLE records (
        kind TEXT NOT NULL CHECK(kind IN ('session','run','checkpoint','event','start_key',
            'operation','interaction','context','model_input','model_response','receipt','memory')),
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        namespace TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version >= 1),
        state TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence >= 0),
        created_at TEXT NOT NULL,
        PRIMARY KEY(kind,tenant_id,user_id,namespace,key)
    )""",
    "CREATE UNIQUE INDEX run_ids ON records(key) WHERE kind='run'",
    """CREATE UNIQUE INDEX unfinished_sessions ON records(tenant_id,user_id,namespace)
        WHERE kind='run' AND state IN ('accepted','running','suspended')""",
    """CREATE UNIQUE INDEX active_interactions ON records(tenant_id,user_id,namespace)
        WHERE kind='interaction' AND state='active'""",
    "CREATE INDEX record_pages ON records(kind,tenant_id,user_id,namespace,created_at,key)",
    "CREATE INDEX event_pages ON records(kind,tenant_id,user_id,namespace,sequence)",
)


@dataclass(frozen=True)
class Settled(Generic[T]):
    value: T | None
    error: BaseException | None
    cancelled: bool


async def _settle(awaitable: Awaitable[T]) -> Settled[T]:
    async def work() -> T:
        return await awaitable

    task = asyncio.create_task(work())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    try:
        return Settled(task.result(), None, cancelled)
    except BaseException as exc:
        return Settled(None, exc, cancelled)


class SQLiteDriver:
    def __init__(self, execute: Callable[..., Awaitable[tuple[list[Any], int]]]) -> None:
        self.execute = execute

    async def unfinished_count(self) -> int:
        rows, _ = await self.execute(
            "SELECT count(*) FROM records WHERE kind='run' "
            "AND state IN ('accepted','running','suspended')"
        )
        return int(rows[0][0])

    async def select(self, query: Query) -> list[Row]:
        clauses = ["kind=?"]
        parameters: list[Any] = [query.kind]
        if query.tenant_id is not None:
            clauses.append("tenant_id=?")
            parameters.append(query.tenant_id)
        if query.user_id is not None:
            clauses.append("user_id=?")
            parameters.append(query.user_id)
        for column in ("namespace", "key", "state"):
            value = getattr(query, column)
            if value is not None:
                clauses.append(f"{column}=?")
                parameters.append(value)
        if query.states is not None:
            clauses.append("state IN (" + ",".join("?" for _ in query.states) + ")")
            parameters.extend(query.states)
        if query.after_sequence is not None:
            clauses.append("sequence>?")
            parameters.append(query.after_sequence)
        if query.before is not None:
            if query.order == "memory_recent":
                clauses.append("(created_at<? OR (created_at=? AND key>?))")
                parameters.extend((query.before[0], *query.before))
            else:
                clauses.append("(created_at,key)<(?,?)")
                parameters.extend(query.before)
        order = {
            "key": "key ASC",
            "sequence": "sequence ASC",
            "newest": "created_at DESC,key DESC",
            "memory_recent": "created_at DESC,key ASC",
        }[query.order]
        parameters.append(query.limit)
        sql = (
            "SELECT kind,tenant_id,user_id,namespace,key,payload,version,state,sequence,created_at "
            "FROM records WHERE " + " AND ".join(clauses) + f" ORDER BY {order} LIMIT ?"
        )
        rows, _ = await self.execute(sql, tuple(parameters))
        return [Row(Key(*row[:5]), *row[5:]) for row in rows]

    @staticmethod
    def _values(row: Row) -> tuple[Any, ...]:
        key = row.key
        return (
            key.kind,
            key.tenant_id,
            key.user_id,
            key.namespace,
            key.key,
            row.payload,
            row.version,
            row.state,
            row.sequence,
            row.created_at,
        )

    async def insert(self, row: Row) -> None:
        await self.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)", self._values(row))

    async def replace(self, row: Row, expected_version: int) -> None:
        values = self._values(row)
        _, count = await self.execute(
            "UPDATE records SET payload=?,version=?,state=?,sequence=?,created_at=? "
            "WHERE kind=? AND tenant_id=? AND user_id=? AND namespace=? AND key=? AND version=?",
            (*values[5:], *values[:5], expected_version),
        )
        if count != 1:
            raise Conflict("Record version changed")


class SQLiteStorage(Storage):
    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] | None = None) -> None:
        super().__init__(clock=clock)
        self.path = Path(path)
        self._connection: aiosqlite.Connection | None = None
        self._ownership: FileOwnership | None = None
        self._in_transaction = False
        self.info: dict[str, str | int] = {}

    async def _raw(self, sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
        connection = self._connection
        if connection is None:
            raise PersistenceUnavailable("Database connection is unavailable")
        cursor = await connection.execute(sql, parameters)
        try:
            rows = list(await cursor.fetchall())
            return rows, cursor.rowcount
        finally:
            await cursor.close()

    @staticmethod
    def _error(error: BaseException) -> None:
        if isinstance(error, sqlite3.IntegrityError):
            raise Conflict("Stored uniqueness or record constraint failed") from error
        if isinstance(error, asyncio.CancelledError):
            raise error
        raise PersistenceUnavailable("SQLite operation failed") from error

    async def _sql(self, sql: str, parameters: tuple[Any, ...] = ()) -> tuple[list[Any], int]:
        settled = await _settle(self._raw(sql, parameters))
        if settled.error is not None:
            self._error(settled.error)
        if settled.cancelled:
            raise asyncio.CancelledError
        return cast(tuple[list[Any], int], settled.value)

    async def open(self) -> None:
        self._check_lifecycle()
        async with self._gate:
            self._check_not_open()
            if self._connection is not None or self._ownership is not None:
                raise StorageInUse("Storage still owns resources from its previous lifecycle")
            ownership = FileOwnership(self.path)
            ownership.acquire()
            self._ownership = ownership
            try:
                connection = aiosqlite.connect(ownership.path, isolation_level=None)
                self._connection = connection
                connected = await _settle(connection)
                if connected.error is not None:
                    self._error(connected.error)
                if connected.cancelled:
                    raise asyncio.CancelledError
                version, _ = await self._sql("SELECT sqlite_version()")
                runtime_version = str(version[0][0])
                if tuple(map(int, runtime_version.split("."))) < (3, 35, 0):
                    raise IncompatibleStorage("SQLite 3.35 or later is required")
                schema, _ = await self._sql("PRAGMA user_version")
                schema_version = int(schema[0][0])
                tables, _ = await self._sql("SELECT name FROM sqlite_master WHERE type='table'")
                names = {row[0] for row in tables}
                if schema_version == 0 and names:
                    raise IncompatibleStorage(
                        "Non-empty unversioned database requires explicit migration"
                    )
                if schema_version not in (0, SCHEMA_VERSION):
                    raise IncompatibleStorage(
                        "Database schema is unsupported; no automatic migration"
                    )
                if schema_version == SCHEMA_VERSION:
                    if names != {"metadata", "records"}:
                        raise IncompatibleStorage("Database tables do not match its schema version")
                    metadata, _ = await self._sql("SELECT key,value FROM metadata")
                    values = dict(metadata)
                    if values.get("format") != "drov.sqlite" or values.get("record_schema") != str(
                        RECORD_VERSION
                    ):
                        raise IncompatibleStorage("Record schema is unsupported")
                    secret = values.get("dedup_secret", "")
                    if len(secret) != 64:
                        raise IncompatibleStorage("Database deduplication metadata is invalid")
                    try:
                        self._secret = bytes.fromhex(secret)
                    except ValueError as exc:
                        raise IncompatibleStorage(
                            "Database deduplication metadata is invalid"
                        ) from exc
                    indexes, _ = await self._sql(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                    if not {
                        "run_ids",
                        "unfinished_sessions",
                        "active_interactions",
                        "record_pages",
                        "event_pages",
                    } <= {row[0] for row in indexes}:
                        raise IncompatibleStorage("Database schema indexes are incomplete")
                configured: dict[str, str | int] = {"sqlite_version": runtime_version}
                for name, value, expected in (
                    ("journal_mode", "DELETE", "delete"),
                    ("synchronous", "FULL", 2),
                    ("foreign_keys", "ON", 1),
                    ("busy_timeout", "5000", 5000),
                ):
                    await self._sql(f"PRAGMA {name}={value}")
                    rows, _ = await self._sql(f"PRAGMA {name}")
                    actual = rows[0][0]
                    if actual != expected:
                        raise IncompatibleStorage("Required SQLite setting was not applied")
                    configured[name] = actual
                if schema_version == 0:
                    self._secret = secrets.token_bytes(32)
                    await self._begin()
                    for statement in DDL:
                        await self._sql(statement)
                    for key, value in (
                        ("format", "drov.sqlite"),
                        ("record_schema", str(RECORD_VERSION)),
                        ("dedup_secret", self._secret.hex()),
                    ):
                        await self._sql("INSERT INTO metadata VALUES (?,?)", (key, value))
                    await self._sql(f"PRAGMA user_version={SCHEMA_VERSION}")
                    await self._finish(True)
                configured["schema_version"] = SCHEMA_VERSION
                configured["record_schema"] = RECORD_VERSION
                self.info = configured
                self._poisoned = False
                self._opened = True
            except BaseException:
                await self._close_resources()
                raise

    async def _begin(self) -> Driver:
        if self._ownership is None:
            raise PersistenceUnavailable("Database ownership is missing")
        self._ownership.check()
        settled = await _settle(self._raw("BEGIN IMMEDIATE"))
        if self._connection is not None:
            self._in_transaction = self._connection.in_transaction
        if settled.error is not None:
            self._error(settled.error)
        if settled.cancelled:
            raise asyncio.CancelledError
        return SQLiteDriver(self._sql)

    async def _bind_artifacts(self, store_id: str) -> None:
        """Pair one private byte directory with this database; no record/DDL migration."""
        import re

        if not re.fullmatch(r"[0-9a-f]{32}", store_id):
            raise IncompatibleStorage("Invalid artifact store identity")
        self._require_open()
        self._check_lifecycle()
        async with self._gate:
            self._require_open()
            try:
                await self._begin()
                rows, _ = await self._sql(
                    "SELECT value FROM metadata WHERE key='artifact_store_id'"
                )
                if rows and rows[0][0] != store_id:
                    raise Conflict("SQLite storage is paired to another artifact directory")
                if not rows:
                    await self._sql(
                        "INSERT INTO metadata VALUES ('artifact_store_id',?)", (store_id,)
                    )
                await self._finish(True)
            except BaseException:
                await self._finish(False)
                raise

    async def _finish(self, commit: bool) -> None:
        if not self._in_transaction:
            return
        settled = await _settle(self._raw("COMMIT" if commit else "ROLLBACK"))
        if self._connection is not None:
            self._in_transaction = self._connection.in_transaction
        if settled.error is not None or self._in_transaction:
            self._poisoned = True
            if commit and self._in_transaction:
                rollback = await _settle(self._raw("ROLLBACK"))
                if self._connection is not None:
                    self._in_transaction = self._connection.in_transaction
                if rollback.error is not None or self._in_transaction:
                    raise PersistenceUnavailable(
                        "Transaction outcome is uncertain; ownership retained"
                    ) from settled.error
            raise PersistenceUnavailable(
                "Transaction failed; no durable success receipt"
            ) from settled.error
        if settled.cancelled:
            raise asyncio.CancelledError

    async def _close_resources(self) -> None:
        if self._in_transaction:
            try:
                await self._finish(False)
            except BaseException as exc:
                raise ShutdownIncomplete(
                    "Database transaction could not be settled; ownership retained"
                ) from exc
        cancelled = False
        if self._connection is not None:
            closed = await _settle(self._connection.close())
            if closed.error is not None:
                raise ShutdownIncomplete(
                    "Database connection did not close; ownership retained"
                ) from closed.error
            cancelled = closed.cancelled
            self._connection = None
        if self._ownership is not None:
            try:
                self._ownership.release()
            except Exception as exc:
                raise ShutdownIncomplete("Database ownership could not be released") from exc
            self._ownership = None
        self._opened = False
        if cancelled:
            raise asyncio.CancelledError

    async def aclose(self) -> None:
        self._check_lifecycle()
        async with self._gate:
            await self._close_resources()
