"""Private row boundary used by the two transaction backends."""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Key:
    kind: str
    tenant_id: str
    user_id: str
    namespace: str
    key: str


@dataclass(frozen=True)
class Row:
    key: Key
    payload: str
    version: int
    state: str = ""
    sequence: int = 0
    created_at: str = ""


@dataclass(frozen=True)
class Query:
    kind: str
    tenant_id: str | None
    user_id: str | None
    namespace: str | None = None
    key: str | None = None
    state: str | None = None
    states: tuple[str, ...] | None = None
    after_sequence: int | None = None
    limit: int = 100
    order: Literal["key", "sequence", "newest", "memory_recent"] = "key"
    before: tuple[str, str] | None = None


class Driver(Protocol):
    async def check_retained(self, query: Query) -> None: ...

    async def unfinished_count(self) -> int: ...

    async def select(self, query: Query) -> list[Row]: ...

    async def insert(self, row: Row) -> None: ...

    async def replace(self, row: Row, expected_version: int) -> None: ...
