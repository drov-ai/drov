"""Shared transaction operations. The owning backend controls commit and rollback."""

import asyncio
import hashlib
import hmac
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast
from uuid import uuid4

from pydantic import TypeAdapter

from drov._json import canonical_json
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    InvalidCursor,
    PersistenceUnavailable,
    RecordNotFound,
    SessionBusy,
)
from drov.storage._driver import Driver, Key, Query, Row
from drov.storage.records import (
    Checkpoint,
    EventDraft,
    EventRecord,
    ItemKind,
    MemoryRecord,
    MemoryScope,
    RunItem,
    RunRecord,
    RunState,
    SessionRecord,
    SessionScope,
    StartKeyRecord,
    StartRequest,
)
from drov.types import (
    Identifier,
    NonNegativeInt,
    PositiveInt,
    RunContext,
    RunRef,
    UtcTime,
    WireModel,
)

M = TypeVar("M", bound=WireModel)
P = ParamSpec("P")
R = TypeVar("R")
UNFINISHED = frozenset(("accepted", "running", "suspended"))
TRANSITIONS = {
    "accepted": {"running", "suspended", "cancelled", "failed"},
    "running": {"running", "suspended", "completed", "failed", "cancelled"},
    "suspended": {"suspended", "running", "failed", "cancelled"},
}


def _identity(scope: RunContext) -> tuple[str, str]:
    valid = RunContext(tenant_id=scope.tenant_id, user_id=scope.user_id)
    return valid.tenant_id, valid.user_id


def _time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _row(
    kind: str,
    scope: RunContext,
    namespace: str,
    key: str,
    value: WireModel,
    *,
    version: int = 1,
    state: str = "",
    sequence: int = 0,
    created_at: str = "",
) -> Row:
    tenant, user = _identity(scope)
    return Row(
        Key(kind, tenant, user, namespace, key),
        value.model_dump_json(),
        version,
        state,
        sequence,
        created_at,
    )


def _decode(model: type[M], row: Row) -> M:
    try:
        record = model.from_json(row.payload)
        key = row.key
        if isinstance(record, (RunRecord, SessionRecord, MemoryRecord)):
            if (record.scope.tenant_id, record.scope.user_id) != (key.tenant_id, key.user_id):
                raise ValueError("Stored payload owner does not match its index")
        if isinstance(record, RunRecord):
            if (
                record.scope.session_id,
                record.state.run_id,
                record.state.state_version,
                record.state.status,
            ) != (key.namespace, key.key, row.version, row.state):
                raise ValueError("Run payload does not match its index")
        elif isinstance(record, SessionRecord):
            if (record.scope.session_id, record.version) != (key.key, row.version):
                raise ValueError("Session payload does not match its index")
        elif isinstance(record, MemoryRecord):
            if (record.scope.agent_name, record.key, record.version) != (
                key.namespace,
                key.key,
                row.version,
            ):
                raise ValueError("Memory payload does not match its index")
        elif isinstance(record, (Checkpoint, EventRecord, RunItem)):
            if record.run_id != key.namespace:
                raise ValueError("Record belongs to a different run")
            if isinstance(record, Checkpoint) and record.state_version != row.version:
                raise ValueError("Checkpoint payload does not match its version")
            if isinstance(record, EventRecord) and record.seq != row.sequence:
                raise ValueError("Event payload does not match its sequence")
            if isinstance(record, RunItem) and (
                record.kind,
                record.item_id,
                record.version,
                record.state,
            ) != (key.kind, key.key, row.version, row.state):
                raise ValueError("Run item payload does not match its index")
        return record
    except (ValueError, TypeError) as exc:
        raise PersistenceUnavailable("Stored record is invalid or incompatible") from exc


def _mutation(method: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @wraps(method)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        transaction = cast(Transaction, args[0])
        transaction._check()
        try:
            return await method(*args, **kwargs)
        except BaseException:
            transaction._failed = True
            raise

    return guarded


class Transaction:
    def __init__(
        self, driver: Driver, secret: bytes, clock: Callable[[], datetime], owner: asyncio.Task[Any]
    ) -> None:
        self._driver = driver
        self._secret = secret
        self._clock = clock
        self._owner = owner
        self._active = True
        self._failed = False

    def _check(self) -> None:
        if not self._active or asyncio.current_task() is not self._owner:
            raise ConfigurationError("Transaction is inactive or belongs to another task")
        if self._failed:
            raise PersistenceUnavailable("Transaction is already aborted")

    def _now(self) -> datetime:
        return TypeAdapter(UtcTime).validate_python(self._clock(), strict=True)

    async def _select(
        self,
        kind: str,
        scope: RunContext,
        *,
        namespace: str | None = None,
        key: str | None = None,
        **options: Any,
    ) -> list[Row]:
        self._check()
        tenant, user = _identity(scope)
        return await self._driver.select(
            Query(kind, tenant, user, namespace=namespace, key=key, **options)
        )

    async def _get(
        self, model: type[M], kind: str, scope: RunContext, namespace: str | None, key: str
    ) -> M | None:
        rows = await self._select(kind, scope, namespace=namespace, key=key, limit=2)
        if len(rows) > 1:
            raise PersistenceUnavailable("Stored identity is ambiguous")
        return _decode(model, rows[0]) if rows else None

    async def get_run(self, scope: RunContext, run_id: str) -> RunRecord:
        record = await self._get(RunRecord, "run", scope, None, run_id)
        if record is None:
            raise RecordNotFound("Run is unavailable")
        return record

    async def get_session(self, scope: SessionScope) -> SessionRecord:
        scope = SessionScope.model_validate(scope)
        record = await self._get(SessionRecord, "session", scope, "", scope.session_id)
        if record is None:
            raise RecordNotFound("Session is unavailable")
        return record

    async def get_checkpoint(self, scope: RunContext, run_id: str) -> Checkpoint:
        run = await self.get_run(scope, run_id)
        value = await self._get(Checkpoint, "checkpoint", scope, run_id, "current")
        if value is None:
            raise PersistenceUnavailable("Run checkpoint is missing")
        if value.state_version != run.state.state_version:
            raise PersistenceUnavailable("Checkpoint and state versions differ")
        return value

    async def events(
        self, scope: RunContext, run_id: str, *, after: int = 0, limit: int = 100
    ) -> list[EventRecord]:
        self._page_limit(limit)
        TypeAdapter(NonNegativeInt).validate_python(after, strict=True)
        run = await self.get_run(scope, run_id)
        if after > run.last_seq:
            raise InvalidCursor("Event cursor exceeds stored sequence")
        rows = await self._select(
            "event", scope, namespace=run_id, after_sequence=after, limit=limit, order="sequence"
        )
        return [_decode(EventRecord, row) for row in rows]

    @staticmethod
    def _page_limit(value: int) -> None:
        TypeAdapter(PositiveInt).validate_python(value, strict=True)
        if value > 500:
            raise ValueError("Storage page limit exceeds 500")

    async def sessions(
        self, scope: RunContext, *, limit: int = 100, before: tuple[datetime, str] | None = None
    ) -> list[SessionRecord]:
        self._page_limit(limit)
        rows = await self._select(
            "session", scope, namespace="", limit=limit, before=self._before(before), order="newest"
        )
        return [_decode(SessionRecord, row) for row in rows]

    async def runs(
        self, scope: SessionScope, *, limit: int = 100, before: tuple[datetime, str] | None = None
    ) -> list[RunRecord]:
        self._page_limit(limit)
        await self.get_session(scope)
        rows = await self._select(
            "run",
            scope,
            namespace=scope.session_id,
            limit=limit,
            before=self._before(before),
            order="newest",
        )
        return [_decode(RunRecord, row) for row in rows]

    @staticmethod
    def _before(value: tuple[datetime, str] | None) -> tuple[str, str] | None:
        if value is None:
            return None
        created = TypeAdapter(UtcTime).validate_python(value[0], strict=True)
        key = TypeAdapter(Identifier).validate_python(value[1], strict=True)
        return _time(created), key

    def _digest(self, value: object) -> str:
        return hmac.new(
            self._secret, canonical_json(value).encode("utf-8"), hashlib.sha256
        ).hexdigest()

    def _start_identity(self, request: StartRequest) -> tuple[str | None, str]:
        scope = request.scope
        key_hash = (
            self._digest(
                {
                    "kind": "request_key",
                    "tenant_id": scope.tenant_id,
                    "user_id": scope.user_id,
                    "session_id": scope.session_id,
                    "key": request.request_key,
                }
            )
            if request.request_key is not None
            else None
        )
        fingerprint = self._digest(
            {
                "input": request.input,
                "agent_name": request.agent_name,
                "revision": request.revision,
                "limits": (
                    request.requested_limits.model_dump(mode="json", exclude_unset=True)
                    if request.requested_limits is not None
                    else {}
                ),
            }
        )
        return key_hash, fingerprint

    async def lookup_start(self, request: StartRequest) -> RunRef | None:
        """Check dedup before Runtime reserves a slot, within the same transaction."""
        self._check()
        request = StartRequest.model_validate(request)
        key_hash, fingerprint = self._start_identity(request)
        if key_hash is not None:
            previous = await self._get(
                StartKeyRecord, "start_key", request.scope, request.scope.session_id, key_hash
            )
            if previous is not None:
                if not hmac.compare_digest(previous.fingerprint, fingerprint):
                    raise Conflict("Request key was already used with different input")
                return previous.ref
        return None

    @_mutation
    async def start(self, request: StartRequest, *, max_unfinished: int = 1000) -> RunRef:
        self._check()
        request = StartRequest.model_validate(request)
        TypeAdapter(PositiveInt).validate_python(max_unfinished, strict=True)
        scope = request.scope
        key_hash, fingerprint = self._start_identity(request)
        previous = await self.lookup_start(request)
        if previous is not None:
            return previous
        try:
            session = await self.get_session(scope)
        except RecordNotFound:
            session = None
        if session is not None:
            if session.agent_name != request.agent_name:
                raise Conflict("Session belongs to a different Agent")
            if session.active_run_id is not None:
                raise SessionBusy("Session already has an unfinished run")
        if await self._driver.unfinished_count() >= max_unfinished:
            raise CapacityExceeded("Unfinished run capacity reached")
        now = self._now()
        run_id = str(uuid4())
        ref = RunRef(run_id=run_id, state_version=1)
        record = RunRecord(
            scope=scope,
            agent_name=request.agent_name,
            revision=request.revision,
            config_hash=request.config_hash,
            input=request.input,
            limits=request.limits,
            created_at=now,
            updated_at=now,
            state=RunState(**ref.model_dump(), status="accepted"),
            last_seq=1,
        )
        new_session = SessionRecord(
            scope=scope,
            agent_name=request.agent_name,
            active_run_id=run_id,
            created_at=session.created_at if session is not None else now,
            updated_at=now,
            version=session.version + 1 if session else 1,
        )
        session_row = _row(
            "session",
            scope,
            "",
            scope.session_id,
            new_session,
            version=new_session.version,
            created_at=_time(new_session.created_at),
        )
        if session is None:
            await self._driver.insert(session_row)
        else:
            await self._driver.replace(session_row, session.version)
        await self._driver.insert(
            _row(
                "run",
                scope,
                scope.session_id,
                run_id,
                record,
                state="accepted",
                created_at=_time(now),
            )
        )
        checkpoint = Checkpoint(
            **ref.model_dump(),
            cursor="accepted",
            data={"input_ref": f"run:{run_id}:input", "session_sequence": new_session.version},
        )
        await self._driver.insert(_row("checkpoint", scope, run_id, "current", checkpoint))
        event = EventRecord(
            **ref.model_dump(), seq=1, type="run.accepted", occurred_at=now, data={}
        )
        await self._driver.insert(_row("event", scope, run_id, "1", event, sequence=1))
        if key_hash is not None:
            dedup = StartKeyRecord(fingerprint=fingerprint, ref=ref)
            await self._driver.insert(_row("start_key", scope, scope.session_id, key_hash, dedup))
        return ref

    @_mutation
    async def save_step(
        self,
        scope: RunContext,
        *,
        state: RunState,
        expected_version: int,
        checkpoint: Checkpoint,
        events: Sequence[EventDraft],
    ) -> RunRef:
        state = RunState.model_validate(state)
        checkpoint = Checkpoint.model_validate(checkpoint)
        current = await self.get_run(scope, state.run_id)
        TypeAdapter(PositiveInt).validate_python(expected_version, strict=True)
        if current.state.state_version != expected_version:
            raise Conflict("Run state version changed")
        if (
            state.state_version != expected_version + 1
            or checkpoint.state_version != state.state_version
        ):
            raise Conflict("Step must advance exactly one state version")
        if checkpoint.run_id != state.run_id or not events:
            raise ConfigurationError("Step needs a matching checkpoint and at least one event")
        if state.status not in TRANSITIONS.get(current.state.status, set()):
            raise Conflict("Run state transition is not allowed")
        now = self._now()
        updated = RunRecord.model_validate(
            current.model_dump()
            | {
                "state": state,
                "updated_at": now,
                "last_seq": current.last_seq + len(events),
            }
        )
        await self._driver.replace(
            _row(
                "run",
                current.scope,
                current.scope.session_id,
                state.run_id,
                updated,
                version=state.state_version,
                state=state.status,
                created_at=_time(current.created_at),
            ),
            expected_version,
        )
        await self._driver.replace(
            _row(
                "checkpoint",
                scope,
                state.run_id,
                "current",
                checkpoint,
                version=state.state_version,
            ),
            expected_version,
        )
        for index, draft in enumerate(events, current.last_seq + 1):
            draft = EventDraft.model_validate(draft)
            event = EventRecord(
                run_id=state.run_id,
                state_version=state.state_version,
                seq=index,
                type=draft.type,
                occurred_at=now,
                data=draft.data,
            )
            await self._driver.insert(
                _row("event", scope, state.run_id, str(index), event, sequence=index)
            )
        if state.status not in UNFINISHED:
            session = await self.get_session(current.scope)
            if session.active_run_id != state.run_id:
                raise PersistenceUnavailable("Session active run is inconsistent")
            released = SessionRecord.model_validate(
                session.model_dump()
                | {
                    "active_run_id": None,
                    "updated_at": now,
                    "version": session.version + 1,
                }
            )
            await self._driver.replace(
                _row(
                    "session",
                    scope,
                    "",
                    current.scope.session_id,
                    released,
                    version=released.version,
                    created_at=_time(session.created_at),
                ),
                session.version,
            )
        return RunRef(run_id=state.run_id, state_version=state.state_version)

    async def get_item(
        self, scope: RunContext, run_id: str, kind: ItemKind, item_id: str
    ) -> RunItem:
        await self.get_run(scope, run_id)
        item = await self._get(RunItem, kind, scope, run_id, item_id)
        if item is None:
            raise RecordNotFound("Run item is unavailable")
        return item

    @_mutation
    async def put_item(
        self, scope: RunContext, item: RunItem, *, expected_version: int = 0
    ) -> None:
        item = RunItem.model_validate(item)
        TypeAdapter(NonNegativeInt).validate_python(expected_version, strict=True)
        await self.get_run(scope, item.run_id)
        if item.kind == "receipt" and expected_version != 0:
            raise Conflict("Response receipts are immutable")
        if item.version != expected_version + 1:
            raise Conflict("Item must advance exactly one version")
        row = _row(
            item.kind,
            scope,
            item.run_id,
            item.item_id,
            item,
            version=item.version,
            state=item.state,
        )
        if expected_version == 0:
            await self._driver.insert(row)
        else:
            await self._driver.replace(row, expected_version)

    async def items(
        self, scope: RunContext, run_id: str, kind: ItemKind, *, limit: int = 100
    ) -> list[RunItem]:
        self._page_limit(limit)
        await self.get_run(scope, run_id)
        return [
            _decode(RunItem, row)
            for row in await self._select(kind, scope, namespace=run_id, limit=limit)
        ]

    async def get_memory(self, scope: MemoryScope, key: str) -> MemoryRecord | None:
        scope = MemoryScope.model_validate(scope)
        return await self._get(MemoryRecord, "memory", scope, scope.agent_name, key)

    @_mutation
    async def put_memory(self, record: MemoryRecord, *, expected_version: int) -> None:
        record = MemoryRecord.model_validate(record)
        TypeAdapter(NonNegativeInt).validate_python(expected_version, strict=True)
        if record.version != expected_version + 1:
            raise Conflict("Memory must advance exactly one version")
        row = _row(
            "memory",
            record.scope,
            record.scope.agent_name,
            record.key,
            record,
            version=record.version,
            state="deleted" if record.deleted else "live",
            created_at=_time(record.updated_at),
        )
        if expected_version == 0:
            await self._driver.insert(row)
        else:
            await self._driver.replace(row, expected_version)
