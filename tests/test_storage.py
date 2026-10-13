import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from drov.config import Limits
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    InvalidCursor,
    PersistenceUnavailable,
    RecordNotFound,
    SessionBusy,
)
from drov.storage import MemoryStorage, SQLiteStorage
from drov.storage.base import Storage
from drov.storage.records import (
    Checkpoint,
    EventDraft,
    MemoryRecord,
    MemoryScope,
    RunItem,
    RunState,
    SessionScope,
    StartRequest,
)
from drov.types import Cancelled, RunContext, RunRef

NOW = datetime(2026, 10, 13, tzinfo=UTC)


def start_request(
    *,
    tenant: str = "tenant",
    user: str = "alice",
    session: str = "session",
    key: str | None = "request",
    text: str = "hello",
    revision: str = "1",
    agent: str = "assistant",
    requested_limits: Limits | None = None,
) -> StartRequest:
    return StartRequest(
        scope=SessionScope(tenant_id=tenant, user_id=user, session_id=session),
        agent_name=agent,
        revision=revision,
        config_hash="a" * 64,
        input=text,
        request_key=key,
        requested_limits=requested_limits,
    )


@pytest_asyncio.fixture(params=["memory", "sqlite"])
async def storage(tmp_path: Path, request: pytest.FixtureRequest) -> AsyncIterator[Storage]:
    store: Storage = (
        MemoryStorage(clock=lambda: NOW)
        if request.param == "memory"
        else SQLiteStorage(tmp_path / "state.db", clock=lambda: NOW)
    )
    async with store:
        yield store


async def transition(
    store: Storage, scope: RunContext, ref: RunRef, *, status: str = "running"
) -> RunRef:
    version = ref.state_version + 1
    values: dict[str, Any] = dict(run_id=ref.run_id, state_version=version, status=status)
    if status == "cancelled":
        values["outcome"] = Cancelled(run_id=ref.run_id, state_version=version)
    state = RunState.model_validate(values)
    async with store.transaction() as transaction:
        return await transaction.save_step(
            scope,
            state=state,
            expected_version=ref.state_version,
            checkpoint=Checkpoint(run_id=ref.run_id, state_version=version, cursor=status),
            events=[EventDraft.model_validate({"type": "run." + status})],
        )


@pytest.mark.asyncio
async def test_start_deduplicates_before_busy_and_capacity(storage: Storage) -> None:
    request = start_request()
    refs = await asyncio.gather(*(storage.start(request, max_unfinished=1) for _ in range(20)))
    assert all(ref == refs[0] for ref in refs)
    assert len(await storage.events(request.scope, refs[0].run_id)) == 1
    assert (
        await storage.start(start_request(requested_limits=Limits()), max_unfinished=1) == refs[0]
    )
    assert (await storage.get_session(request.scope)).active_run_id == refs[0].run_id
    with pytest.raises(SessionBusy):
        await storage.start(start_request(key="different"), max_unfinished=1)
    with pytest.raises(CapacityExceeded):
        await storage.start(start_request(session="another"), max_unfinished=1)
    with pytest.raises(RecordNotFound):
        await storage.get_session(start_request(session="another").scope)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"text": "changed"},
        {"revision": "2"},
        {"agent": "other"},
        {"requested_limits": Limits(model_retries=0)},
    ],
)
async def test_same_key_different_canonical_request_conflicts(
    storage: Storage, changes: dict[str, Any]
) -> None:
    ref = await storage.start(start_request())
    with pytest.raises(Conflict):
        await storage.start(start_request(**changes))
    assert (await storage.get_run(start_request().scope, ref.run_id)).state.state_version == 1


@pytest.mark.asyncio
async def test_record_queries_isolate_scope_and_dedup(storage: Storage) -> None:
    requests = [
        start_request(),
        start_request(user="bob"),
        start_request(tenant="another"),
        start_request(session="another"),
        start_request(session="agent-session", agent="other"),
    ]
    refs = [await storage.start(request) for request in requests]
    assert len({ref.run_id for ref in refs}) == 5
    for request, ref in zip(requests, refs, strict=True):
        assert (await storage.get_run(request.scope, ref.run_id)).scope == request.scope
    for foreign in (requests[1], requests[2]):
        with pytest.raises(RecordNotFound):
            await storage.get_run(foreign.scope, refs[0].run_id)
        with pytest.raises(RecordNotFound):
            await storage.events(foreign.scope, refs[0].run_id)
        with pytest.raises(RecordNotFound):
            await storage.get_checkpoint(foreign.scope, refs[0].run_id)
    async with storage.transaction() as transaction:
        assert len(await transaction.sessions(requests[0].scope)) == 3
        assert len(await transaction.sessions(requests[1].scope)) == 1
        assert len(await transaction.runs(requests[0].scope)) == 1


@pytest.mark.asyncio
async def test_checkpoint_event_and_cas_advance_together(storage: Storage) -> None:
    request = start_request()
    ref = await storage.start(request)
    results = await asyncio.gather(
        *(transition(storage, request.scope, ref) for _ in range(20)), return_exceptions=True
    )
    assert sum(isinstance(result, RunRef) for result in results) == 1
    assert sum(isinstance(result, Conflict) for result in results) == 19
    current = await storage.get_run(request.scope, ref.run_id)
    checkpoint = await storage.get_checkpoint(request.scope, ref.run_id)
    events = await storage.events(request.scope, ref.run_id)
    assert current.state.state_version == checkpoint.state_version == events[-1].state_version == 2
    assert [event.seq for event in events] == [1, 2]
    assert await storage.start(request) == ref
    with pytest.raises(InvalidCursor):
        await storage.events(request.scope, ref.run_id, after=3)
    assert await storage.events(request.scope, ref.run_id, after=2) == []


@pytest.mark.asyncio
async def test_terminal_run_releases_session_but_old_key_does_not_restart(storage: Storage) -> None:
    request = start_request()
    ref = await storage.start(request)
    terminal = await transition(storage, request.scope, ref, status="cancelled")
    assert (await storage.get_session(request.scope)).active_run_id is None
    assert await storage.start(request) == ref
    with pytest.raises(Conflict):
        await transition(storage, request.scope, terminal)
    with pytest.raises(Conflict):
        await storage.start(start_request(key="next", agent="other"))
    next_ref = await storage.start(start_request(key="next", revision="2"))
    assert next_ref.run_id != ref.run_id
    async with storage.transaction() as transaction:
        assert len(await transaction.runs(request.scope)) == 2


@pytest.mark.asyncio
async def test_body_failure_rolls_back_every_record(storage: Storage) -> None:
    request = start_request()
    with pytest.raises(RuntimeError):
        async with storage.transaction() as transaction:
            await transaction.start(request)
            raise RuntimeError("injected before commit")
    with pytest.raises(RecordNotFound):
        await storage.get_session(request.scope)
    async with storage.transaction() as transaction:
        assert await transaction.sessions(request.scope) == []
    ref = await storage.start(request)
    assert len(await storage.events(request.scope, ref.run_id)) == 1


@pytest.mark.asyncio
async def test_caught_mutation_error_cannot_commit_partial_changes(storage: Storage) -> None:
    request = start_request()
    with pytest.raises(PersistenceUnavailable):
        async with storage.transaction() as transaction:
            await transaction.start(request)
            try:
                await transaction.start(start_request(key="second"))
            except SessionBusy:
                pass
    with pytest.raises(RecordNotFound):
        await storage.get_session(request.scope)


@pytest.mark.asyncio
async def test_memory_cas_and_tombstone_are_in_same_transaction_as_results(
    storage: Storage,
) -> None:
    request = start_request()
    ref = await storage.start(request)
    scope = MemoryScope(tenant_id="tenant", user_id="alice", agent_name="assistant")
    memory = MemoryRecord(
        scope=scope, key="preference", version=1, content="quiet", source=ref.run_id, updated_at=NOW
    )
    item = RunItem(
        run_id=ref.run_id,
        kind="operation",
        item_id="operation",
        state="succeeded",
        data={"result": {"version": 1}},
    )
    with pytest.raises(RuntimeError):
        async with storage.transaction() as transaction:
            await transaction.put_memory(memory, expected_version=0)
            await transaction.put_item(request.scope, item)
            raise RuntimeError("injected result commit failure")
    async with storage.transaction() as transaction:
        assert await transaction.get_memory(scope, "preference") is None
        with pytest.raises(RecordNotFound):
            await transaction.get_item(request.scope, ref.run_id, "operation", "operation")
    async with storage.transaction() as transaction:
        await transaction.put_memory(memory, expected_version=0)
        await transaction.put_item(request.scope, item)
    for foreign in (
        scope.model_copy(update={"user_id": "bob"}),
        scope.model_copy(update={"tenant_id": "other"}),
        scope.model_copy(update={"agent_name": "other"}),
    ):
        async with storage.transaction() as transaction:
            assert await transaction.get_memory(foreign, "preference") is None
    deleted = MemoryRecord.model_validate(
        memory.model_dump() | {"version": 2, "content": None, "deleted": True}
    )
    async with storage.transaction() as transaction:
        await transaction.put_memory(deleted, expected_version=1)
    with pytest.raises(Conflict):
        async with storage.transaction() as transaction:
            await transaction.put_memory(memory, expected_version=0)
    recreated = MemoryRecord.model_validate(memory.model_dump() | {"version": 3, "content": "new"})
    async with storage.transaction() as transaction:
        await transaction.put_memory(recreated, expected_version=2)
        assert (await transaction.get_memory(scope, "preference")) == recreated


@pytest.mark.asyncio
async def test_item_cas_active_interaction_and_receipt_immutability(storage: Storage) -> None:
    request = start_request()
    ref = await storage.start(request)
    first = RunItem(run_id=ref.run_id, kind="interaction", item_id="first", state="active", data={})
    async with storage.transaction() as transaction:
        await transaction.put_item(request.scope, first)
    second = first.model_copy(update={"item_id": "second"})
    with pytest.raises(Conflict):
        async with storage.transaction() as transaction:
            await transaction.put_item(request.scope, second)
    async with storage.transaction() as transaction:
        consumed = first.model_copy(update={"version": 2, "state": "consumed"})
        await transaction.put_item(request.scope, consumed, expected_version=1)
        await transaction.put_item(request.scope, second)
        receipt = RunItem(
            run_id=ref.run_id,
            kind="receipt",
            item_id="receipt",
            state="accepted",
            data={"response": "yes"},
        )
        await transaction.put_item(request.scope, receipt)
    with pytest.raises(Conflict):
        async with storage.transaction() as transaction:
            await transaction.put_item(
                request.scope, receipt.model_copy(update={"version": 2}), expected_version=1
            )
    async with storage.transaction() as transaction:
        assert len(await transaction.items(request.scope, ref.run_id, "interaction")) == 2
        assert (
            await transaction.get_item(request.scope, ref.run_id, "receipt", "receipt")
        ) == receipt


@pytest.mark.asyncio
async def test_transactions_are_owned_not_nested_and_do_not_leak_references(
    storage: Storage,
) -> None:
    request = start_request()
    ref = await storage.start(request)
    async with storage.transaction() as transaction:
        with pytest.raises(ConfigurationError):
            await storage.get_run(request.scope, ref.run_id)
        with pytest.raises(ConfigurationError):
            await asyncio.create_task(transaction.get_run(request.scope, ref.run_id))
        with pytest.raises(ConfigurationError):
            await storage.aclose()
    with pytest.raises(ConfigurationError):
        await transaction.get_run(request.scope, ref.run_id)
    record = await storage.get_run(request.scope, ref.run_id)
    record.state.operations.clear()
    checkpoint = await storage.get_checkpoint(request.scope, ref.run_id)
    checkpoint.data["injected"] = True
    assert "injected" not in (await storage.get_checkpoint(request.scope, ref.run_id)).data


@pytest.mark.asyncio
async def test_reads_wait_for_whole_transaction(storage: Storage) -> None:
    request = start_request()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def write() -> RunRef:
        async with storage.transaction() as transaction:
            ref = await transaction.start(request)
            entered.set()
            await release.wait()
            return ref

    writer = asyncio.create_task(write())
    await entered.wait()
    reader = asyncio.create_task(storage.get_session(request.scope))
    await asyncio.sleep(0)
    assert not reader.done()
    release.set()
    ref = await writer
    assert (await reader).active_run_id == ref.run_id


@pytest.mark.asyncio
async def test_cancelled_transaction_body_rolls_back(storage: Storage) -> None:
    request = start_request()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def write() -> None:
        async with storage.transaction() as transaction:
            await transaction.start(request)
            entered.set()
            await release.wait()

    task = asyncio.create_task(write())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(RecordNotFound):
        await storage.get_session(request.scope)


@pytest.mark.asyncio
async def test_keyset_pages_with_equal_creation_times_have_no_missing_rows(
    storage: Storage,
) -> None:
    request = start_request()
    ref = await storage.start(request)
    await transition(storage, request.scope, ref, status="cancelled")
    await storage.start(start_request(key="next"))
    await storage.start(start_request(session="z-session"))
    async with storage.transaction() as transaction:
        first = await transaction.sessions(request.scope, limit=1)
        second = await transaction.sessions(
            request.scope, limit=1, before=(first[0].created_at, first[0].scope.session_id)
        )
        assert [first[0].scope.session_id, second[0].scope.session_id] == ["z-session", "session"]
        runs = await transaction.runs(request.scope, limit=1)
        next_runs = await transaction.runs(
            request.scope, limit=1, before=(runs[0].created_at, runs[0].state.run_id)
        )
        assert len(next_runs) == 1
        assert runs[0].state.run_id > next_runs[0].state.run_id
