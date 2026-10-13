"""One owner and one serialized transaction at a time, including ordinary reads."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Self

from drov.errors import ConfigurationError, PersistenceUnavailable, StorageInUse
from drov.storage._driver import Driver
from drov.storage.records import (
    Checkpoint,
    EventRecord,
    RunRecord,
    SessionRecord,
    SessionScope,
    StartRequest,
)
from drov.storage.transaction import Transaction
from drov.types import RunContext, RunRef


class Storage(ABC):
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._gate = asyncio.Lock()
        self._owner: asyncio.Task[Any] | None = None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._opened = False
        self._poisoned = False
        self._secret = b""

    @abstractmethod
    async def open(self) -> None: ...

    @property
    def is_open(self) -> bool:
        """Whether this instance is open; uncertain transactions can still block use."""
        return self._opened

    @abstractmethod
    async def aclose(self) -> None: ...

    @abstractmethod
    async def _begin(self) -> Driver: ...

    @abstractmethod
    async def _finish(self, commit: bool) -> None: ...

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    def _require_open(self) -> None:
        if not self._opened:
            raise ConfigurationError("Storage is not open")
        if self._poisoned:
            raise PersistenceUnavailable("Storage stopped after an uncertain transaction")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        self._require_open()
        task = asyncio.current_task()
        if task is None:
            raise ConfigurationError("Storage requires an asyncio task")
        if self._owner is task:
            raise ConfigurationError("Nested storage transactions are not supported")
        async with self._gate:
            self._require_open()
            self._owner = task
            transaction: Transaction | None = None
            try:
                try:
                    driver = await self._begin()
                except BaseException:
                    await self._finish(False)
                    raise
                transaction = Transaction(driver, self._secret, self._clock, task)
                try:
                    yield transaction
                except BaseException:
                    await self._finish(False)
                    raise
                else:
                    if transaction._failed:
                        await self._finish(False)
                        raise PersistenceUnavailable(
                            "Transaction was aborted after a failed mutation"
                        )
                    await self._finish(True)
            finally:
                if transaction is not None:
                    transaction._active = False
                self._owner = None

    def _check_lifecycle(self) -> None:
        if self._owner is asyncio.current_task():
            raise ConfigurationError("Storage lifecycle cannot change inside a transaction")

    async def start(self, request: StartRequest, *, max_unfinished: int = 1000) -> RunRef:
        async with self.transaction() as transaction:
            return await transaction.start(request, max_unfinished=max_unfinished)

    async def get_run(self, scope: RunContext, run_id: str) -> RunRecord:
        async with self.transaction() as transaction:
            return await transaction.get_run(scope, run_id)

    async def get_session(self, scope: SessionScope) -> SessionRecord:
        async with self.transaction() as transaction:
            return await transaction.get_session(scope)

    async def get_checkpoint(self, scope: RunContext, run_id: str) -> Checkpoint:
        async with self.transaction() as transaction:
            return await transaction.get_checkpoint(scope, run_id)

    async def events(
        self, scope: RunContext, run_id: str, *, after: int = 0, limit: int = 100
    ) -> list[EventRecord]:
        async with self.transaction() as transaction:
            return await transaction.events(scope, run_id, after=after, limit=limit)

    def _check_not_open(self) -> None:
        if self._opened:
            raise StorageInUse("This storage instance is already open")
