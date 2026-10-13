"""Registration and real resource ownership, without an Agent execution substitute."""

import asyncio
import copy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from drov import Agent, Limits, MemoryStorage, ResourceBinding, Runtime, SQLiteStorage
from drov.errors import ConfigurationError, Conflict, ShutdownIncomplete, StorageInUse
from drov.runtime.registration import fingerprint
from drov.storage.records import SessionScope, StartRequest
from drov.tools.schema import compile_tool
from drov.types import RunContext
from tests.fixtures import ScriptedAdapter, close_released_runtime


def definition(**kwargs: Any) -> Agent[str]:
    return Agent(name="assistant", revision="1", model=ScriptedAdapter([]), **kwargs)


class ResourceSpy:
    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self.log = log
        self.fail_close = False
        self.close_entered = asyncio.Event()
        self.close_release: asyncio.Event | None = None

    async def aclose(self) -> None:
        self.log.append(f"close:{self.name}")
        self.close_entered.set()
        if self.close_release is not None:
            await self.close_release.wait()
        if self.fail_close:
            raise RuntimeError("close failed")


class SpyStorage(MemoryStorage):
    def __init__(self, log: list[str], *, fail_open: bool = False) -> None:
        super().__init__()
        self.log = log
        self.fail_open = fail_open

    async def open(self) -> None:
        self.log.append("open:storage")
        await super().open()
        if self.fail_open:
            raise RuntimeError("partially opened")

    async def aclose(self) -> None:
        self.log.append("close:storage")
        await super().aclose()


def test_constructor_is_inert_and_has_no_execution_placeholders(tmp_path: Path) -> None:
    agent = definition()
    runtime = Runtime(agents=[agent], storage=SQLiteStorage(tmp_path / "no-file.db"))
    assert not (tmp_path / "no-file.db").exists()
    assert agent.model.calls == []  # type: ignore[attr-defined]
    for name in ("recover", "verify"):
        assert not hasattr(runtime, name)
    assert callable(agent.run)


def test_duplicate_missing_identity_and_owned_model_validation() -> None:
    agent = definition()
    with pytest.raises(ConfigurationError, match="Duplicate"):
        Runtime(agents=[agent, definition()])
    with pytest.raises(ConfigurationError, match="name and revision"):
        Runtime(agents=[Agent(model=ScriptedAdapter([]))])
    runtime = Runtime(agents=[agent, Agent(name="assistant", revision="2", model=agent.model)])
    assert runtime.registration("assistant", "1") != runtime.registration("assistant", "2")
    with pytest.raises(ConfigurationError, match="owned_models"):
        Runtime(agents=[agent], owned_models=[ScriptedAdapter([])])
    with pytest.raises(ConfigurationError, match="owned_models"):
        Runtime(agents=[agent], owned_models=[agent.model, agent.model])


@pytest.mark.parametrize(
    "changes",
    [
        {"instructions": "new"},
        {"model_config": {"model_id": "other", "temperature": 0.5}},
        {"limits": Limits(model_retries=0)},
        {"required_dependencies": ["items"]},
    ],
)
def test_fingerprint_covers_declared_configuration(changes: dict[str, Any]) -> None:
    assert fingerprint(definition()) != fingerprint(definition(**changes))


def test_fingerprint_is_canonical_and_validates_schemas() -> None:
    a = definition(model_config={"a": 1, "b": [True, None]})
    b = definition(model_config={"b": [True, None], "a": 1})
    assert fingerprint(a) == fingerprint(b)

    def search(query: str, limit: int = 2) -> list[str]:
        raise AssertionError("Schema registration must not execute tools")

    tool = compile_tool(search, name="search", revision="1", effect="read")
    agent = definition(tools=[tool])
    original = fingerprint(agent)
    assert fingerprint(definition(tools=[copy.deepcopy(tool)])) == original
    tool.parameters["additionalProperties"] = True
    with pytest.raises(ConfigurationError, match="schema"):
        fingerprint(agent)


def test_fingerprint_is_stable_in_an_independent_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tests.test_runtime import definition; "
                "from drov.runtime.registration import fingerprint; "
                "print(fingerprint(definition()).config_hash)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert result.stdout.strip() == fingerprint(definition()).config_hash


def test_mutated_definition_rejected_before_open(tmp_path: Path) -> None:
    agent = definition()
    runtime = Runtime(agents=[agent], storage=SQLiteStorage(tmp_path / "none.db"))
    agent.instructions = "changed"
    with pytest.raises(Conflict, match="changed"):
        runtime.registration("assistant", "1")
    assert not (tmp_path / "none.db").exists()


@pytest.mark.asyncio
async def test_model_replacement_cannot_bypass_resource_ownership_with_same_fingerprint() -> None:
    agent = definition()
    original = agent.model
    async with Runtime(agents=[agent]) as runtime:
        before = fingerprint(agent)
        agent.model = ScriptedAdapter([])
        assert fingerprint(agent) == before
        with pytest.raises(Conflict, match="changed"):
            runtime.registration("assistant", "1")
    assert not original.closed  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_startup_rechecks_mutation_and_cleans_partial_storage() -> None:
    log: list[str] = []
    agent = definition()
    storage = SpyStorage(log)
    runtime = Runtime(agents=[agent], storage=storage)
    agent.instructions = "changed"
    with pytest.raises(Conflict):
        await runtime.open()
    assert log == []
    await runtime.aclose()
    failing = SpyStorage(log, fail_open=True)
    with pytest.raises(RuntimeError, match="partially"):
        await Runtime(agents=[definition()], storage=failing).open()
    assert log == ["open:storage", "close:storage"]
    assert not failing.is_open


@pytest.mark.asyncio
async def test_factory_assembly_reverse_cleanup_and_aliases() -> None:
    log: list[str] = []
    a, b, borrowed = (ResourceSpy(name, log) for name in ("a", "b", "borrowed"))

    async def create_a() -> ResourceSpy:
        log.append("create:a")
        return a

    async def create_b() -> ResourceSpy:
        log.append("create:b")
        return b

    agent = definition(required_dependencies=["a", "b", "existing"])
    runtime = Runtime(
        agents=[agent],
        storage=SpyStorage(log),
        owned_models=[agent.model],
        dependencies={
            "a": ResourceBinding.factory(create_a),
            "b": ResourceBinding.factory(create_b),
            "alias": ResourceBinding.owned(b),
            "existing": borrowed,
        },
    )
    assert log == []
    async with runtime:
        assert runtime.dependencies["a"] is a
        with pytest.raises(TypeError):
            runtime.dependencies["a"] = borrowed  # type: ignore[index]
    assert log == ["open:storage", "create:a", "create:b", "close:b", "close:a", "close:storage"]
    assert agent.model.closed  # type: ignore[attr-defined]
    assert not borrowed.close_entered.is_set()
    await runtime.aclose()
    with pytest.raises(ConfigurationError):
        await runtime.open()


@pytest.mark.asyncio
async def test_startup_failure_preserves_borrowers_and_cleans_owned() -> None:
    log: list[str] = []
    owned, borrowed = ResourceSpy("owned", log), ResourceSpy("borrowed", log)

    async def fail() -> ResourceSpy:
        log.append("create:failure")
        raise RuntimeError("factory failure")

    agent = definition()
    runtime = Runtime(
        agents=[agent],
        storage=SpyStorage(log),
        dependencies={
            "a": ResourceBinding.owned(owned),
            "b": borrowed,
            "c": ResourceBinding.factory(fail),
        },
    )
    with pytest.raises(RuntimeError, match="factory failure"):
        await runtime.open()
    assert log == ["open:storage", "create:failure", "close:owned", "close:storage"]
    assert not agent.model.closed  # type: ignore[attr-defined]
    assert not borrowed.close_entered.is_set()
    await runtime.aclose()


@pytest.mark.asyncio
async def test_borrowed_open_storage_remains_open_and_exclusive(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "borrowed.db")
    await storage.open()
    runtime = Runtime(agents=[definition()], storage=storage)
    await runtime.open()
    competitor = Runtime(agents=[definition()], storage=ResourceBinding.borrowed(storage))
    with pytest.raises(ConfigurationError, match="another Runtime"):
        await competitor.open()
    await runtime.aclose()
    assert storage.is_open
    with pytest.raises(StorageInUse):
        await SQLiteStorage(storage.path).open()
    await competitor.aclose()
    await storage.aclose()
    with pytest.raises(ConfigurationError, match="already be open"):
        await Runtime(agents=[definition()], storage=ResourceBinding.borrowed(storage)).open()


@pytest.mark.asyncio
async def test_owned_model_cannot_be_transferred_while_borrowed() -> None:
    agent = definition()
    async with Runtime(agents=[agent]):
        other = Runtime(agents=[agent], owned_models=[agent.model])
        with pytest.raises(ConfigurationError, match="another Runtime"):
            await other.open()
        assert not agent.model.closed  # type: ignore[attr-defined]
    async with Runtime(agents=[agent], owned_models=[agent.model]):
        pass
    assert agent.model.closed  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_required_dependency_failure_and_conflicting_alias_cleanup() -> None:
    log: list[str] = []
    storage = SpyStorage(log)
    with pytest.raises(ConfigurationError, match="dependencies"):
        await Runtime(agents=[definition(required_dependencies=["items"])], storage=storage).open()
    assert not storage.is_open
    resource = ResourceSpy("alias", log)
    runtime = Runtime(
        agents=[definition()], dependencies={"a": resource, "b": ResourceBinding.owned(resource)}
    )
    with pytest.raises(ConfigurationError, match="Conflicting ownership"):
        await runtime.open()
    assert not resource.close_entered.is_set()


@pytest.mark.asyncio
async def test_cancelled_startup_waits_for_factory_and_closes_result() -> None:
    log: list[str] = []
    entered, release = asyncio.Event(), asyncio.Event()
    resource = ResourceSpy("created", log)

    async def create() -> ResourceSpy:
        entered.set()
        await release.wait()
        return resource

    runtime = Runtime(
        agents=[definition()],
        storage=SpyStorage(log),
        dependencies={"a": ResourceBinding.factory(create)},
    )
    opening = asyncio.create_task(runtime.open())
    await entered.wait()
    opening.cancel()
    await asyncio.sleep(0)
    opening.cancel()
    assert not opening.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert log == ["open:storage", "close:created", "close:storage"]


@pytest.mark.asyncio
async def test_failed_or_slow_close_retains_lock_and_can_retry(tmp_path: Path) -> None:
    log: list[str] = []
    resource = ResourceSpy("blocked", log)
    resource.fail_close = True
    storage = SQLiteStorage(tmp_path / "retained.db")
    runtime = Runtime(
        agents=[definition()],
        storage=storage,
        dependencies={"a": ResourceBinding.owned(resource)},
        limits=Limits(shutdown_grace_seconds=0.02),
    )
    await runtime.open()
    with pytest.raises(ShutdownIncomplete, match="failed"):
        await runtime.aclose()
    assert storage.is_open
    with pytest.raises(StorageInUse):
        await SQLiteStorage(storage.path).open()
    resource.fail_close = False
    resource.close_release = asyncio.Event()
    with pytest.raises(ShutdownIncomplete, match="still active"):
        await runtime.aclose()
    assert storage.is_open
    resource.close_release.set()
    await close_released_runtime(runtime)
    assert not storage.is_open
    assert log == ["close:blocked", "close:blocked"]
    async with SQLiteStorage(storage.path):
        pass


@pytest.mark.asyncio
async def test_startup_cleanup_failure_retains_resources(tmp_path: Path) -> None:
    resource = ResourceSpy("failed", [])
    resource.fail_close = True
    storage = SQLiteStorage(tmp_path / "startup.db")
    runtime = Runtime(
        agents=[definition(required_dependencies=["missing"])],
        storage=storage,
        dependencies={"owned": ResourceBinding.owned(resource)},
    )
    with pytest.raises(ShutdownIncomplete, match="Startup failed"):
        await runtime.open()
    assert storage.is_open
    with pytest.raises(StorageInUse):
        await SQLiteStorage(storage.path).open()
    resource.fail_close = False
    await runtime.aclose()


@pytest.mark.asyncio
async def test_stored_revision_fingerprint_and_schema_recovery_gate(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "recovery.db")
    agent = definition(model_config={"model_id": "scripted"})
    scope = SessionScope(tenant_id="test", user_id="alice", session_id="s")
    caller = RunContext(tenant_id="test", user_id="alice")
    async with Runtime(agents=[agent], storage=storage) as runtime:
        ref = await runtime.storage.start(
            StartRequest(
                scope=scope,
                agent_name="assistant",
                revision="1",
                config_hash=runtime.registration("assistant", "1").config_hash,
                input="hello",
            )
        )
        record = await runtime.storage.get_run(caller, ref.run_id)
        assert runtime.validate_record(record).revision == "1"
    async with Runtime(
        agents=[definition(model_config={"model_id": "scripted"})],
        storage=SQLiteStorage(storage.path),
    ) as runtime:
        record = await runtime.storage.get_run(caller, ref.run_id)
        assert runtime.validate_record(record).config_hash == record.config_hash
    async with Runtime(
        agents=[definition(instructions="changed")], storage=SQLiteStorage(storage.path)
    ) as runtime:
        with pytest.raises(Conflict, match="fingerprint"):
            runtime.validate_record(record)
    async with Runtime(agents=[], storage=SQLiteStorage(storage.path)) as runtime:
        with pytest.raises(Conflict, match="revision"):
            runtime.validate_record(record)
    async with Runtime(agents=[agent], storage=SQLiteStorage(storage.path)) as runtime:
        with pytest.raises(ValueError):
            runtime.validate_record(record.model_copy(update={"schema_version": 2}))
        agent.instructions = "changed-after-open"
        with pytest.raises(Conflict, match="changed"):
            runtime.validate_record(record)


@pytest.mark.asyncio
async def test_explicit_closer_and_storage_factory() -> None:
    closed: list[object] = []
    value = object()

    async def close(instance: object) -> None:
        closed.append(instance)

    async def storage_factory() -> MemoryStorage:
        return MemoryStorage()

    async with Runtime(
        agents=[definition()],
        storage=ResourceBinding.factory(storage_factory),
        dependencies={"plain": ResourceBinding.owned(value, close=close)},
    ) as runtime:
        assert runtime.storage.is_open
    assert closed == [value]


@pytest.mark.asyncio
async def test_concurrent_close_and_cancelled_close_keep_single_owner() -> None:
    log: list[str] = []
    resource = ResourceSpy("a", log)
    resource.close_release = asyncio.Event()
    runtime = Runtime(agents=[definition()], dependencies={"a": ResourceBinding.owned(resource)})
    await runtime.open()
    first = asyncio.create_task(runtime.aclose())
    await resource.close_entered.wait()
    second = asyncio.create_task(runtime.aclose())
    first.cancel()
    await asyncio.sleep(0)
    assert not first.done()
    resource.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert log == ["close:a"]


@pytest.mark.asyncio
async def test_lifecycle_callbacks_cannot_deadlock_by_closing_their_own_runtime() -> None:
    async def create() -> ResourceSpy:
        with pytest.raises(ConfigurationError, match="own lifecycle"):
            await runtime.aclose()
        return ResourceSpy("created", [])

    async def close(value: ResourceSpy) -> None:
        with pytest.raises(ConfigurationError, match="own lifecycle"):
            await runtime.aclose()
        await value.aclose()

    runtime = Runtime(
        agents=[definition()], dependencies={"a": ResourceBinding.factory(create, close=close)}
    )
    async with runtime:
        pass


@pytest.mark.asyncio
async def test_close_during_startup_waits_for_creation_then_releases_resources() -> None:
    entered, finish = asyncio.Event(), asyncio.Event()
    resource = ResourceSpy("created", [])

    async def create() -> ResourceSpy:
        entered.set()
        await finish.wait()
        return resource

    runtime = Runtime(agents=[definition()], dependencies={"a": ResourceBinding.factory(create)})
    opening = asyncio.create_task(runtime.open())
    await entered.wait()
    closing = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    finish.set()
    with pytest.raises(ConfigurationError, match="closed during startup"):
        await opening
    await closing
    assert resource.close_entered.is_set()
