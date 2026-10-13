"""Resource lifecycle and private scheduling primitives; no Agent execution loop."""

import asyncio
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import Future as ThreadFuture
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Generic, Self, TypeVar, cast

from pydantic import TypeAdapter

from drov.agent import Agent
from drov.config import Limits
from drov.errors import CapacityExceeded, ConfigurationError, Conflict, ShutdownIncomplete
from drov.models.protocol import ModelAdapter
from drov.runtime.registration import AgentRegistration, fingerprint
from drov.runtime.resources import ResourceBinding
from drov.storage import MemoryStorage
from drov.storage.base import Storage
from drov.storage.records import RunRecord
from drov.types import ScopedIdentifier

T = TypeVar("T")


@dataclass
class _Lease:
    value: object
    users: set["Runtime"] = field(default_factory=set)
    owned: bool = False
    exclusive: bool = False


# Strong references are intentional until successful close, including failed shutdown.
_LEASES: dict[int, _Lease] = {}
_LEASE_GATE = threading.Lock()


@dataclass
class _Resource:
    value: object
    owned: bool
    close: Callable[[], Awaitable[None]] | None
    closing: asyncio.Task[None] | None = None


async def _settled(task: asyncio.Task[T]) -> tuple[T, bool]:
    """Keep ownership until a lifecycle operation actually settles after caller cancellation."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    return task.result(), cancelled


def _owned_task(execute: Callable[[], Awaitable[T]], *, name: str) -> asyncio.Task[T]:
    """Do not enter user code before the owner stores the task, even with eager factories."""
    ready = asyncio.Event()

    async def run() -> T:
        await ready.wait()
        return await execute()

    coroutine = run()
    try:
        task = asyncio.create_task(coroutine, name=name)
    except BaseException:
        coroutine.close()
        raise
    ready.set()
    return task


def _observe(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


class _Slot:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime
        self.claimed = False
        self.released = False

    def release(self) -> None:
        if self.claimed:
            raise ConfigurationError("Scheduled work owns this slot")
        self._release()

    def _release(self) -> None:
        if not self.released:
            self.released = True
            self.runtime._slots.discard(self)
            self.runtime._changed.set()


class _Work(Generic[T]):
    """Kernel-owned task plus actual underlying calls that may outlive that task."""

    def __init__(self, runtime: "Runtime", slot: _Slot) -> None:
        self.runtime = runtime
        self.slot = slot
        self.cancel_requested = False
        self.task: asyncio.Task[T]
        self._calls: set[asyncio.Future[Any] | ThreadFuture[Any]] = set()

    def checkpoint(self) -> None:
        if self.cancel_requested or self.runtime._closing_requested:
            raise asyncio.CancelledError

    def request_cancel(self) -> None:
        if not self.cancel_requested:
            self.cancel_requested = True
            self.task.cancel()

    async def wait(self) -> T:
        return await asyncio.shield(self.task)

    def track(self, call: asyncio.Future[Any] | ThreadFuture[Any]) -> None:
        """Retain an already dispatched call, including one racing with cancellation.

        Dispatchers must checkpoint before dispatch and register without an intervening await.
        Pass the real future, not a cancellable wrapper of an unstoppable thread.
        """
        if self.task is not asyncio.current_task():
            raise ConfigurationError("Only the owning work can track a dispatched call")
        self._calls.add(call)
        loop = self.runtime._require_loop()

        def done(future: asyncio.Future[Any] | ThreadFuture[Any]) -> None:
            # ThreadFuture callbacks can run in the worker thread.
            loop.call_soon_threadsafe(self._call_done, future)

        call.add_done_callback(done)

    def _call_done(self, call: asyncio.Future[Any] | ThreadFuture[Any]) -> None:
        if not call.cancelled():
            call.exception()  # Observe failure even when the original waiter disconnected.
        self._calls.discard(call)
        self._try_release()

    def _task_done(self, task: asyncio.Task[T]) -> None:
        if not task.cancelled():
            task.exception()
        self._try_release()

    def _try_release(self) -> None:
        if self.task.done() and not self._calls:
            self.slot._release()
            self.runtime._works.discard(self)


class Runtime:
    """Register definitions and manage resources. Run/start/control APIs arrive in T-06+."""

    def __init__(
        self,
        *,
        agents: Sequence[Agent[Any]],
        storage: Storage | ResourceBinding[Storage] | None = None,
        dependencies: Mapping[str, object] | None = None,
        owned_models: Sequence[ModelAdapter] = (),
        limits: Limits | None = None,
    ) -> None:
        self.limits = Limits.from_json((limits or Limits()).model_dump_json())
        self._agents: dict[tuple[str, str], Agent[Any]] = {}
        self._registered_models: dict[tuple[str, str], ModelAdapter] = {}
        self._registrations: dict[tuple[str, str], AgentRegistration] = {}
        for agent in agents:
            registration = fingerprint(agent)
            key = (registration.name, registration.revision)
            if key in self._agents:
                raise ConfigurationError("Duplicate Agent name/revision registration")
            self._agents[key] = agent
            self._registered_models[key] = agent.model
            self._registrations[key] = registration
        self._storage_binding = (
            storage
            if isinstance(storage, ResourceBinding)
            else (
                ResourceBinding.borrowed(storage)
                if storage is not None and storage.is_open
                else ResourceBinding.owned(storage if storage is not None else MemoryStorage())
            )
        )
        self._dependency_bindings = dict(dependencies or {})
        for dependency_name in self._dependency_bindings:
            TypeAdapter(ScopedIdentifier).validate_python(dependency_name, strict=True)
        models = {id(agent.model): agent.model for agent in self._agents.values()}
        owned = {id(model) for model in owned_models}
        if len(owned) != len(owned_models) or owned - models.keys():
            raise ConfigurationError("owned_models must uniquely reference registered models")
        self._model_bindings = [
            ResourceBinding.owned(model) if key in owned else ResourceBinding.borrowed(model)
            for key, model in models.items()
        ]
        self._resources: list[_Resource] = []
        self._storage: Storage | None = None
        self._dependencies: Mapping[str, object] = MappingProxyType({})
        self._state = "new"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._opening: asyncio.Task[None] | None = None
        self._closing: asyncio.Task[None] | None = None
        self._closing_requested = False
        self._slots: set[_Slot] = set()
        self._works: set[_Work[Any]] = set()
        self._changed = asyncio.Event()

    def _require_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ConfigurationError("Runtime belongs to a different event loop")
        self._loop = loop
        return loop

    def _require_open(self) -> None:
        self._require_loop()
        if self._state != "open" or self._closing_requested:
            raise ConfigurationError("Runtime is not accepting work")

    @property
    def storage(self) -> Storage:
        self._require_open()
        assert self._storage is not None
        return self._storage

    @property
    def dependencies(self) -> Mapping[str, object]:
        self._require_open()
        return self._dependencies

    @property
    def active_slots(self) -> int:
        return len(self._slots)

    def registration(self, name: str, revision: str) -> AgentRegistration:
        key = (name, revision)
        if key not in self._registrations:
            raise ConfigurationError("Agent name/revision is not registered")
        expected = self._registrations[key]
        if (
            self._agents[key].model is not self._registered_models[key]
            or fingerprint(self._agents[key]) != expected
        ):
            raise Conflict("Registered Agent definition changed; use a new revision")
        return expected

    def validate_record(self, record: RunRecord) -> AgentRegistration:
        """Validate a storage-loaded record before later recovery can dispatch anything."""
        self._require_open()
        valid = RunRecord.from_json(record.model_dump_json())
        key = (valid.agent_name, valid.revision)
        if key not in self._agents:
            raise Conflict("Original Agent revision is not registered")
        registration = self.registration(*key)
        if registration.config_hash != valid.config_hash:
            raise Conflict("Saved Agent configuration/schema fingerprint differs")
        self._require_dependencies(self._agents[key])
        return registration

    def _require_dependencies(self, agent: Agent[Any]) -> None:
        if any(self._dependencies.get(name) is None for name in agent.required_dependencies):
            raise ConfigurationError("Required Agent dependencies are not configured")

    def _install(
        self, value: object, binding: ResourceBinding[Any], *, exclusive: bool = False
    ) -> _Resource:
        owned = binding.mode != "borrowed"
        for resource in self._resources:
            if resource.value is value:
                if resource.owned != owned or binding.close is not None:
                    raise ConfigurationError("Conflicting ownership or closer for resource alias")
                return resource
        closer: Callable[[], Awaitable[None]] | None = None
        if owned:
            if binding.close is not None:

                async def closer() -> None:
                    assert binding.close is not None
                    await binding.close(value)
            else:
                method = getattr(value, "aclose", None)
                if not callable(method):
                    raise ConfigurationError("Owned resources need aclose or an explicit closer")
                closer = cast(Callable[[], Awaitable[None]], method)
        with _LEASE_GATE:
            lease = _LEASES.get(id(value))
            if lease is not None and (owned or lease.owned or exclusive or lease.exclusive):
                raise ConfigurationError("Resource already belongs to another Runtime")
            if lease is None:
                lease = _Lease(value, owned=owned, exclusive=exclusive)
                _LEASES[id(value)] = lease
            lease.users.add(self)
        resource = _Resource(value, owned, closer)
        self._resources.append(resource)
        return resource

    async def _acquire(self, binding: ResourceBinding[T], *, storage: bool = False) -> T:
        if binding.create is not None:
            value = await binding.create()
        else:
            value = cast(T, binding.value)
        resource = self._install(value, binding, exclusive=storage)
        if storage:
            if not isinstance(value, Storage):
                raise ConfigurationError("Runtime storage must implement the internal Storage type")
            if binding.mode == "borrowed":
                if not value.is_open:
                    raise ConfigurationError("Borrowed storage must already be open")
                value._require_open()
            elif not value.is_open:
                # Install first: a partially failed open still needs cleanup.
                await value.open()
        return cast(T, resource.value)

    async def _open(self) -> None:
        try:
            for agent_key in self._agents:
                self.registration(*agent_key)
            self._storage = await self._acquire(self._storage_binding, storage=True)
            for model_binding in self._model_bindings:
                await self._acquire(model_binding)
            dependencies: dict[str, object] = {}
            for key, value in self._dependency_bindings.items():
                binding = (
                    value if isinstance(value, ResourceBinding) else ResourceBinding.borrowed(value)
                )
                dependencies[key] = await self._acquire(binding)
            self._dependencies = MappingProxyType(dependencies)
            for agent in self._agents.values():
                self._require_dependencies(agent)
            self._state = "open"
        except BaseException:
            self._state = "failed"
            await self._close_resources(
                self._require_loop().time() + self.limits.shutdown_grace_seconds
            )
            raise

    async def open(self) -> None:
        self._require_loop()
        if self._state != "new":
            raise ConfigurationError("Runtime is single-use and cannot be opened twice")
        self._state = "opening"
        self._opening = _owned_task(self._open, name="drov-runtime-open")
        try:
            _, cancelled = await _settled(self._opening)
        except BaseException:
            if self._resources:
                raise ShutdownIncomplete("Startup failed; resource ownership retained") from None
            raise
        if cancelled:
            await self.aclose()
            raise asyncio.CancelledError
        if self._closing_requested:
            raise ConfigurationError("Runtime was closed during startup")

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    def _reserve(self) -> _Slot:
        self._require_open()
        if self.active_slots >= self.limits.active_slots:
            raise CapacityExceeded("Runtime active slot capacity reached")
        slot = _Slot(self)
        self._slots.add(slot)
        return slot

    def _spawn(self, slot: _Slot, execute: Callable[[_Work[T]], Awaitable[T]]) -> _Work[T]:
        self._require_open()
        if slot.runtime is not self or slot.released or slot.claimed:
            raise ConfigurationError("Invalid or already scheduled slot")
        work: _Work[T] = _Work(self, slot)

        async def run() -> T:
            work.checkpoint()
            return await execute(work)

        try:
            work.task = _owned_task(run, name="drov-kernel-work")
        except BaseException:
            slot.release()
            raise
        slot.claimed = True
        self._works.add(work)
        work.task.add_done_callback(work._task_done)
        return work

    async def _close_resources(self, deadline: float) -> None:
        loop = self._require_loop()
        while self._resources:
            resource = self._resources[-1]
            if resource.close is not None:
                if resource.closing is None:
                    resource.closing = _owned_task(resource.close, name="drov-resource-close")
                    resource.closing.add_done_callback(_observe)
                task = resource.closing
                if not task.done():
                    await asyncio.wait({task}, timeout=max(0, deadline - loop.time()))
                if not task.done():
                    raise ShutdownIncomplete("Resource close is still active; ownership retained")
                try:
                    task.result()
                except BaseException as exc:
                    resource.closing = None
                    raise ShutdownIncomplete("Resource close failed; ownership retained") from exc
            with _LEASE_GATE:
                lease = _LEASES[id(resource.value)]
                lease.users.remove(self)
                if not lease.users:
                    del _LEASES[id(resource.value)]
            self._resources.pop()

    async def _close(self) -> None:
        if self._opening is not None:
            try:
                await asyncio.shield(self._opening)
            except BaseException:
                pass  # Startup already attempted cleanup; retry remaining resources below.
        self._state = "closing"
        loop = self._require_loop()
        deadline = loop.time() + self.limits.shutdown_grace_seconds
        for work in tuple(self._works):
            work.request_cancel()
        while self._slots:
            self._changed.clear()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ShutdownIncomplete("Active work/calls retain slots and resource ownership")
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except TimeoutError as exc:
                raise ShutdownIncomplete(
                    "Active work/calls retain slots and resource ownership"
                ) from exc
        await self._close_resources(deadline)
        self._state = "closed"

    async def aclose(self) -> None:
        self._require_loop()
        current = asyncio.current_task()
        if current in (self._opening, self._closing) or any(
            resource.closing is current for resource in self._resources
        ):
            raise ConfigurationError("Runtime cannot be closed from its own lifecycle task")
        if any(work.task is asyncio.current_task() for work in self._works):
            raise ConfigurationError("Runtime cannot be closed from its own execution task")
        self._closing_requested = True
        if self._state == "closed":
            return
        if self._closing is None or self._closing.done():
            self._closing = _owned_task(self._close, name="drov-runtime-close")
        _, cancelled = await _settled(self._closing)
        if cancelled:
            raise asyncio.CancelledError
