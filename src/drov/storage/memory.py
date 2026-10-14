"""Transactional in-memory backend sharing the durable-record operations."""

import secrets
from collections.abc import Callable
from datetime import datetime

from drov.errors import Conflict
from drov.storage._driver import Driver, Key, Query, Row
from drov.storage.base import Storage
from drov.storage.transaction import UNFINISHED


class MemoryDriver:
    def __init__(self, rows: dict[Key, Row]) -> None:
        self.rows = rows

    async def unfinished_count(self) -> int:
        return sum(row.key.kind == "run" and row.state in UNFINISHED for row in self.rows.values())

    async def select(self, query: Query) -> list[Row]:
        rows = [
            row
            for key, row in self.rows.items()
            if (
                key.kind == query.kind
                and (query.tenant_id is None or key.tenant_id == query.tenant_id)
                and (query.user_id is None or key.user_id == query.user_id)
                and (query.namespace is None or key.namespace == query.namespace)
                and (query.key is None or key.key == query.key)
                and (query.state is None or row.state == query.state)
                and (query.states is None or row.state in query.states)
                and (query.after_sequence is None or row.sequence > query.after_sequence)
                and (
                    query.before is None
                    or (
                        (
                            row.created_at < query.before[0]
                            or (row.created_at == query.before[0] and key.key > query.before[1])
                        )
                        if query.order == "memory_recent"
                        else (row.created_at, key.key) < query.before
                    )
                )
            )
        ]
        if query.order == "sequence":
            rows.sort(key=lambda row: row.sequence)
        elif query.order == "newest":
            rows.sort(key=lambda row: (row.created_at, row.key.key), reverse=True)
        elif query.order == "memory_recent":
            rows.sort(key=lambda row: row.key.key)
            rows.sort(key=lambda row: row.created_at, reverse=True)
        else:
            rows.sort(key=lambda row: row.key.key)
        return rows[: query.limit]

    def _unique(self, row: Row) -> None:
        for existing in self.rows.values():
            if existing.key == row.key:
                continue
            key = row.key
            if key.kind == "run" and existing.key.kind == "run":
                if key.key == existing.key.key:
                    raise Conflict("Run ID is already present")
                if (
                    (key.tenant_id, key.user_id, key.namespace)
                    == (existing.key.tenant_id, existing.key.user_id, existing.key.namespace)
                    and row.state in UNFINISHED
                    and existing.state in UNFINISHED
                ):
                    raise Conflict("Session has another unfinished run")
            if key.kind == "interaction" and existing.key.kind == "interaction":
                if (key.tenant_id, key.user_id, key.namespace) == (
                    existing.key.tenant_id,
                    existing.key.user_id,
                    existing.key.namespace,
                ) and row.state == existing.state == "active":
                    raise Conflict("Run already has an active interaction")

    async def insert(self, row: Row) -> None:
        if row.key in self.rows:
            raise Conflict("Record already exists")
        self._unique(row)
        self.rows[row.key] = row

    async def replace(self, row: Row, expected_version: int) -> None:
        previous = self.rows.get(row.key)
        if previous is None or previous.version != expected_version:
            raise Conflict("Record version changed")
        self._unique(row)
        self.rows[row.key] = row


class MemoryStorage(Storage):
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        super().__init__(clock=clock)
        self._rows: dict[Key, Row] = {}
        self._working: dict[Key, Row] | None = None
        self._secret = secrets.token_bytes(32)

    async def open(self) -> None:
        self._check_lifecycle()
        async with self._gate:
            self._check_not_open()
            self._opened = True

    async def aclose(self) -> None:
        self._check_lifecycle()
        async with self._gate:
            self._opened = False

    async def _begin(self) -> Driver:
        self._working = dict(self._rows)
        return MemoryDriver(self._working)

    async def _finish(self, commit: bool) -> None:
        if commit and self._working is not None:
            self._rows = self._working
        self._working = None
