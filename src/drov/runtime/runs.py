"""Agent execution, explicit controls and authorized observation of committed facts."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import uuid4

from drov._json import StrictJson, canonical_json
from drov.agent import Agent
from drov.artifacts.types import ArtifactInput, ArtifactRef
from drov.auth import Action, Resource
from drov.config import Limits, validate_input
from drov.context.assembly import MessageGroup, assemble, digest, project, source
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    DrovError,
    InvalidCursor,
    ModelProtocolError,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
    SessionBusy,
    TransientModelError,
)
from drov.memory.execution import MemoryDispatcher
from drov.memory.retrieval import Retrieval, retrieve
from drov.memory.tools import MemoryTool, memory_message, memory_source
from drov.models.protocol import (
    ModelComplete,
    ModelDelta,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelTool,
)
from drov.sandbox.tools import PythonSandbox
from drov.storage.base import Storage
from drov.storage.records import (
    EventDraft,
    MemoryScope,
    RunItem,
    RunRecord,
    SessionScope,
    StartRequest,
)
from drov.storage.transaction import Transaction
from drov.tools.human import HumanInput
from drov.tools.operations import OperationJournal, ToolOperation
from drov.tools.python import PythonTool
from drov.types import (
    Cancelled,
    CancelReceipt,
    Completed,
    ContextManifest,
    ContextSource,
    DurableEvent,
    Failed,
    InteractionSummary,
    JsonOutcome,
    OperationSummary,
    RunContext,
    RunError,
    RunInspection,
    RunOutcome,
    RunRef,
    RunSnapshot,
    Suspended,
    UnknownDurableEvent,
    Usage,
    parse_event,
)

if TYPE_CHECKING:
    from drov.runtime.core import Runtime, _Work
    from drov.runtime.streams import RunEvents

T = TypeVar("T")
TERMINAL = frozenset(("completed", "failed", "cancelled"))
# Default projection is fail-closed, even for unknown future event types.
METADATA_KEYS = frozenset(("operation_id", "attempt_id", "model_call_id", "error_code", "usage"))


class _RunFailure(Exception):
    def __init__(self, code: str, stage: str) -> None:
        self.code, self.stage = code, stage


def _usage(previous: Usage, current: Usage, *, first: bool) -> Usage:
    if first:
        return current
    data: dict[str, Any] = {"complete": previous.complete and current.complete}
    for name in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"):
        a, b = getattr(previous, name), getattr(current, name)
        data[name] = a + b if a is not None and b is not None else None
    if (
        previous.cost_amount is not None
        and current.cost_amount is not None
        and previous.currency == current.currency
    ):
        data["cost_amount"] = format(
            Decimal(previous.cost_amount) + Decimal(current.cost_amount), "f"
        )
        data["currency"] = current.currency
    return Usage.model_validate(data)


class RunController:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime
        from drov.runtime.interactions import InteractionController

        self.interactions = InteractionController(self)
        from drov.runtime.reconciliation import ReconciliationController
        from drov.runtime.recovery import RecoveryController

        self.reconciliation = ReconciliationController(self)
        self.recovery = RecoveryController(self)
        self.works: dict[str, _Work[None]] = {}
        self.failures: dict[str, BaseException] = {}
        self.subscribers: dict[str, set[RunEvents]] = {}

    @property
    def storage(self) -> Storage:
        # Internal access remains available while shutdown records cancellation.
        assert self.runtime._storage is not None
        return self.runtime._storage

    async def control(self, execute: Callable[[], Awaitable[T]]) -> T:
        from drov.runtime.core import _observe, _owned_task, _settled

        self.runtime._require_open()
        task = _owned_task(execute, name="drov-run-control")
        self.runtime._controls.add(task)
        task.add_done_callback(self.runtime._controls.discard)
        task.add_done_callback(_observe)
        result, cancelled = await _settled(task)
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def authorize(
        self, context: RunContext, resource: Resource, actions: tuple[Action, ...]
    ) -> None:
        for action in actions:
            try:
                async with asyncio.timeout(self.runtime.limits.verify_timeout_seconds):
                    allowed = await self.runtime.authorizer.authorize(
                        RunContext.from_json(context.model_dump_json()),
                        action,
                        Resource.from_json(resource.model_dump_json()),
                    )
            except Exception:
                allowed = False
            if allowed is not True:
                if action == "read":
                    raise RecordNotFound("Run is unavailable")
                raise PermissionDenied("Required permission is unavailable")

    @staticmethod
    def resource(record: RunRecord) -> Resource:
        return Resource(
            owner=RunContext(tenant_id=record.scope.tenant_id, user_id=record.scope.user_id),
            session_id=record.scope.session_id,
            run_id=record.state.run_id,
        )

    async def visible(self, run_id: str, context: RunContext, content: bool) -> RunRecord:
        self.runtime._require_open()
        context = RunContext.model_validate(context)
        async with self.storage.transaction() as tx:
            record = await tx._locate_run(context, run_id)
        await self.authorize(
            context, self.resource(record), ("read", "view_content") if content else ("read",)
        )
        await self.interactions.expire(record)
        current = await self.storage.get_run(record.scope, run_id)
        await self.authorize(
            context, self.resource(current), ("read", "view_content") if content else ("read",)
        )
        if content:
            await self.runtime._files.validate(current, actor=context)
        return current

    def effective_limits(self, agent: Agent[Any], requested: Limits | None) -> Limits:
        data: dict[str, Any] = {}
        for name in Limits.model_fields:
            value, ceiling = getattr(agent.limits, name), getattr(self.runtime.limits, name)
            data[name] = (
                min(value, ceiling)
                if value is not None and ceiling is not None
                else ceiling
                if value is None
                else value
            )
        if requested is not None:
            requested = Limits.model_validate(requested)
            data.update(requested.model_dump(exclude_unset=True))
        try:
            return Limits.model_validate(data).require_within(self.runtime.limits)
        except ValueError as exc:
            raise ConfigurationError("Requested limits exceed Runtime ceilings") from exc

    def executable(self, agent: Agent[Any]) -> None:
        name, revision = agent.require_registration_identity()
        self.runtime.registration(name, revision)
        if self.runtime._agents[(name, revision)] is not agent:
            raise ConfigurationError("Use the Agent object registered in this Runtime")
        if any(
            not isinstance(item, (PythonTool, HumanInput, PythonSandbox))
            or (isinstance(item, PythonTool) and not callable(item.function))
            for item in agent.tools
        ):
            raise ConfigurationError("This development snapshot executes only callable read tools")
        self.runtime._require_dependencies(agent)

    async def start(
        self,
        agent: Agent[Any],
        input: str,
        *,
        session_id: str,
        context: RunContext,
        request_key: str | None = None,
        limits: Limits | None = None,
        content: bool = False,
        artifacts: Sequence[ArtifactInput | ArtifactRef] = (),
        _upload_ids: tuple[str, ...] = (),
    ) -> RunRef:
        self.runtime._require_open()
        self.executable(agent)
        if self.runtime._sandboxes is not None:
            controller = self.runtime._sandboxes
            await self.control(lambda: controller.precheck(agent))
        inputs, declarations = self.runtime._files.normalize(artifacts)
        if len(set(_upload_ids)) != len(_upload_ids):
            raise ConfigurationError("Run inputs repeat an upload ID")
        if (
            _upload_ids
            and len(inputs) + len(_upload_ids) > self.runtime._files.store().limits.max_input_files
        ):
            raise CapacityExceeded("Run input files exceed configured limits")
        declarations.extend({"kind": "upload", "upload_id": item} for item in _upload_ids)
        context = RunContext.model_validate(context)
        selected = self.effective_limits(agent, limits)
        validate_input(input, selected)
        identity = self.runtime.registration(*agent.require_registration_identity())
        request = StartRequest(
            scope=SessionScope(**context.model_dump(), session_id=session_id),
            agent_name=identity.name,
            revision=identity.revision,
            config_hash=identity.config_hash,
            input=input,
            request_key=request_key,
            limits=selected,
            requested_limits=limits,
            canonical_artifacts=declarations,
            sandbox_bindings=self.runtime._sandboxes.bindings(agent)
            if self.runtime._sandboxes
            else {},
        )
        resource = Resource(owner=context, session_id=session_id)
        await self.authorize(
            context, resource, ("start", "read", "view_content") if content else ("start",)
        )

        async def accept() -> RunRef:
            # Serialize dedup and slot admission with the storage receiving transaction.
            slot = None
            try:
                async with self.storage.transaction() as tx:
                    previous = await tx.lookup_start(request)
                    if previous is not None:
                        original = await tx.get_run(context, previous.run_id)
                        self.runtime.validate_record(original)
                        await self.runtime._files.validate(original, actor=context, tx=tx)
                        return previous
                    try:
                        session = await tx.get_session(request.scope)
                    except RecordNotFound:
                        session = None
                    if session is not None:
                        if session.agent_name != request.agent_name:
                            raise Conflict("Session belongs to a different Agent")
                        if session.active_run_id is not None:
                            raise SessionBusy("Session already has an unfinished run")
                    if await tx._driver.unfinished_count() >= self.runtime.limits.unfinished_runs:
                        raise CapacityExceeded("Unfinished run capacity reached")
                    slot = self.runtime._reserve()
                    new_run_id = str(uuid4())
                    file_records = await self.runtime._files.admit(
                        tx,
                        inputs=inputs,
                        actor=context,
                        agent_name=identity.name,
                        session_id=session_id,
                        run_id=new_run_id,
                    )
                    if _upload_ids:
                        from drov.artifacts.uploads import Uploads

                        file_records.extend(
                            await Uploads(self.runtime).consume(
                                tx,
                                context,
                                _upload_ids,
                                agent_name=identity.name,
                                revision=identity.revision,
                                session_id=session_id,
                                run_id=new_run_id,
                            )
                        )
                        names = [item.name for item in file_records]
                        store = self.runtime._files.store()
                        if len(set(names)) != len(names):
                            raise ConfigurationError("Run inputs repeat a normalized name")
                        if (
                            len(file_records) > store.limits.max_input_files
                            or sum(item.ref.size_bytes for item in file_records)
                            > store.limits.max_input_bytes
                        ):
                            raise CapacityExceeded("Run input files exceed configured limits")
                    ref = await tx.start(
                        request,
                        max_unfinished=self.runtime.limits.unfinished_runs,
                        _run_id=new_run_id,
                        _artifact_refs=[item.ref for item in file_records],
                        _artifact_binding=self.runtime._files.binding()
                        if file_records or request.sandbox_bindings
                        else None,
                    )
                    await self.runtime._files.publish_inputs(
                        tx, await tx.get_run(context, ref.run_id), file_records
                    )
                assert slot is not None
                try:
                    work = self.runtime._spawn(
                        slot,
                        lambda work: self.execute(ref.run_id, context, work),
                        check_cancel=False,
                    )
                except Exception:
                    # Accepted is durable; a failed handoff must have a recoverable fact.
                    await self.suspend_recovery(ref.run_id, context)
                    raise
                self.attach(ref.run_id, work)
                return ref
            finally:
                if slot is not None and not slot.claimed:
                    # A failed/uncertain commit has no dispatcher; the storage retains its own lock.
                    slot.release()

        return await self.control(accept)

    def attach(self, run_id: str, work: "_Work[None]") -> None:
        self.failures.pop(run_id, None)
        work.run_id = run_id
        self.works[run_id] = work

        def done(task: asyncio.Task[None]) -> None:
            if self.works.get(run_id) is work:
                self.works.pop(run_id, None)
            if not task.cancelled() and task.exception() is not None:
                self.failures[run_id] = cast(BaseException, task.exception())

        work.task.add_done_callback(done)

    async def suspend_recovery(self, run_id: str, context: RunContext) -> None:
        async with self.storage.transaction() as tx:
            record = await tx.get_run(context, run_id)
            checkpoint = await tx.get_checkpoint(context, run_id)
            version = record.state.state_version + 1
            await tx.save_step(
                context,
                state=record.state.model_copy(
                    update={
                        "status": "suspended",
                        "reason": "recovery",
                        "recoverable": True,
                        "state_version": version,
                    }
                ),
                expected_version=version - 1,
                checkpoint=checkpoint.model_copy(update={"state_version": version}),
                events=[EventDraft(type="recovery.required")],
            )

    async def step(
        self,
        run_id: str,
        context: RunContext,
        events: list[EventDraft],
        *,
        data: dict[str, StrictJson] | None = None,
        items: tuple[RunItem, ...] = (),
        usage: Usage | None = None,
    ) -> None:
        async with self.storage.transaction() as tx:
            record = await tx.get_run(context, run_id)
            if record.state.cancel_requested:
                raise asyncio.CancelledError
            checkpoint = await tx.get_checkpoint(context, run_id)
            data = dict(data or {})
            for event in events:
                if (
                    event.type == "model.attempt_failed"
                    and event.data.get("error_code") != "transient_model_error"
                ):
                    data["stop_error"] = RunError(
                        code=cast(str, event.data["error_code"]),
                        message="Committed model attempt failed",
                        stage="model",
                    ).model_dump(mode="json")
            version = record.state.state_version + 1
            for item in items:
                await tx.put_item(context, item)
            await tx.save_step(
                context,
                state=record.state.model_copy(
                    update={
                        "status": "running",
                        "state_version": version,
                        "usage": usage if usage is not None else record.state.usage,
                    }
                ),
                expected_version=version - 1,
                checkpoint=checkpoint.model_copy(
                    update={
                        "state_version": version,
                        "data": checkpoint.data | (data or {}),
                    }
                ),
                events=events,
            )

    async def finish(
        self,
        run_id: str,
        context: RunContext,
        *,
        output: StrictJson = None,
        error: RunError | None = None,
        cancelled: bool = False,
    ) -> None:
        async with self.storage.transaction() as tx:
            await self.finish_in(
                tx, run_id, context, output=output, error=error, cancelled=cancelled
            )

    async def finish_in(
        self,
        tx: Transaction,
        run_id: str,
        context: RunContext,
        *,
        output: StrictJson = None,
        error: RunError | None = None,
        cancelled: bool = False,
        operations: list[OperationSummary] | None = None,
        extra_events: list[EventDraft] | None = None,
    ) -> None:
        record = await tx.get_run(context, run_id)
        if record.state.status in TERMINAL:
            return
        if operations is not None:
            record = record.model_copy(
                update={"state": record.state.model_copy(update={"operations": operations})}
            )
        from drov.runtime.reconciliation import ambiguous

        if ambiguous(record):
            await self.reconciliation.suspend_in(tx, record, error=error, cancelled=cancelled)
            return
        checkpoint = await tx.get_checkpoint(context, run_id)
        summaries = list(record.state.operations)
        final_events: list[EventDraft] = list(extra_events or [])
        for index, summary in enumerate(summaries):
            if summary.status != "prepared":
                continue
            item = await tx.get_item(context, run_id, "operation", summary.operation_id)
            operation = ToolOperation.from_json(canonical_json(item.data))
            stopped = operation.model_copy(
                update={
                    "version": operation.version + 1,
                    "status": "not_executed",
                    "error_code": "run_ended_before_dispatch",
                }
            )
            await tx.put_item(
                context,
                item.model_copy(
                    update={
                        "version": stopped.version,
                        "state": stopped.status,
                        "data": stopped.model_dump(mode="json"),
                    }
                ),
                expected_version=item.version,
            )
            summaries[index] = stopped.summary()
            final_events.append(
                EventDraft(
                    type="tool.not_executed",
                    data={
                        "operation_id": stopped.operation_id,
                    },
                )
            )
        fields: dict[str, Any] = {
            "run_id": run_id,
            "state_version": record.state.state_version + 1,
            "usage": record.state.usage,
        }
        outcome: JsonOutcome
        if cancelled or record.state.cancel_requested:
            outcome = Cancelled(**fields, operations=summaries)
        elif error is not None:
            outcome = Failed(**fields, error=error, operations=summaries)
        else:
            outcome = Completed[StrictJson](**fields, output=output)
        body: dict[str, StrictJson] = {"usage": record.state.usage.model_dump(mode="json")}
        if outcome.status == "completed":
            body["output"] = output
        elif isinstance(outcome, Failed):
            body["error_code"] = outcome.error.code
        await tx.save_step(
            context,
            state=record.state.model_copy(
                update={
                    "status": outcome.status,
                    "state_version": outcome.state_version,
                    "outcome": outcome,
                    "operations": summaries,
                    "cancel_requested": outcome.status == "cancelled",
                    "pending": None,
                    "reason": None,
                    "recoverable": False,
                }
            ),
            expected_version=record.state.state_version,
            checkpoint=checkpoint.model_copy(update={"state_version": outcome.state_version}),
            events=[
                *final_events,
                EventDraft(type=cast(Any, "run." + outcome.status), data=body),
            ],
        )

    async def history(
        self, record: RunRecord, tx: Transaction
    ) -> tuple[MessageGroup, list[MessageGroup]]:
        runs: list[RunRecord] = []
        before = None
        while True:
            page = await tx.runs(record.scope, limit=100, before=before)
            runs.extend(page)
            if len(page) < 100:
                break
            before = (page[-1].created_at, page[-1].state.run_id)
        ordered = []
        for run in runs:
            checkpoint = await tx.get_checkpoint(record.scope, run.state.run_id)
            sequence = checkpoint.data.get("session_sequence", 0)
            if type(sequence) is not int or sequence < 0:
                raise PersistenceUnavailable("Invalid session sequence")
            ordered.append((sequence, run.created_at, run.state.run_id, run, checkpoint))
        ordered.sort(key=lambda row: row[:3])
        protected, history = MessageGroup(), []
        for _, _, _, run, checkpoint in ordered:
            current = run.state.run_id == record.state.run_id
            group = MessageGroup()
            input_message = ModelMessage(role="user", content=run.input)
            group.messages.append(input_message)
            group.sources.append(
                source(
                    f"{run.state.run_id}:input",
                    "input" if current else "history",
                    input_message.model_dump(mode="json"),
                    version="1",
                )
            )
            response_ids = checkpoint.data.get("response_ids", [])
            if not isinstance(response_ids, list) or any(
                type(id) is not str for id in response_ids
            ):
                raise PersistenceUnavailable("Invalid response index")
            for response_id in cast(list[str], response_ids):
                item = await tx.get_item(
                    record.scope, run.state.run_id, "model_response", response_id
                )
                if (
                    type(item.data.get("schema_version")) is not int
                    or item.data["schema_version"] != 1
                ):
                    raise PersistenceUnavailable("Unsupported model response payload")
                response = ModelResponse.model_validate(item.data["response"])
                message = ModelMessage(
                    role="assistant",
                    content=(
                        {
                            "message": response.message,
                            "tool_calls": [
                                call.model_dump(mode="json") for call in response.tool_calls
                            ],
                        }
                        if response.tool_calls
                        else response.message
                    ),
                )
                group.messages.append(message)
                group.sources.append(
                    source(
                        f"{run.state.run_id}:response:{response_id}",
                        "history",
                        message.model_dump(mode="json"),
                        version=str(item.version),
                    )
                )
                operation_ids = item.data["operation_ids"]
                if not isinstance(operation_ids, list) or len(operation_ids) != len(
                    response.tool_calls
                ):
                    raise PersistenceUnavailable("Invalid operation plan")
                for call, operation_id in zip(response.tool_calls, operation_ids, strict=True):
                    if type(operation_id) is not str:
                        raise PersistenceUnavailable("Invalid operation reference")
                    ref = f"{run.state.run_id}:operation:{operation_id}"
                    try:
                        operation = await tx.get_item(
                            record.scope, run.state.run_id, "operation", operation_id
                        )
                        if operation.state in ("prepared", "dispatched"):
                            raise PersistenceUnavailable("Model plan has an unresolved call")
                        result = {
                            "status": operation.state,
                            "has_result": operation.data["has_result"],
                            "result": operation.data["result"],
                            "error_code": operation.data["error_code"],
                        }
                        if operation.data.get("effect_resolution") is not None:
                            result["effect_resolution"] = operation.data["effect_resolution"]
                        version = str(operation.version)
                        memory_sources = operation.data.get("memory_sources", [])
                        if not isinstance(memory_sources, list):
                            raise PersistenceUnavailable("Invalid explicit Memory sources")
                    except RecordNotFound:
                        if run.state.status not in TERMINAL:
                            raise PersistenceUnavailable(
                                "Model plan has an unresolved call"
                            ) from None
                        result = {
                            "status": "not_executed",
                            "has_result": False,
                            "result": None,
                            "error_code": "run_ended_before_dispatch",
                        }
                        version = f"terminal-{run.state.state_version}"
                        memory_sources = []
                    tool_message = ModelMessage(role="tool", call_id=call.call_id, content=result)
                    group.tool_refs.append((len(group.messages), len(group.sources), ref, version))
                    group.messages.append(tool_message)
                    group.sources.append(
                        source(
                            ref,
                            "tool",
                            tool_message.model_dump(mode="json"),
                            version=version,
                        )
                    )
                    tool_source_index = len(group.sources) - 1
                    for memory_item in memory_sources:
                        group.memory_refs.append((len(group.sources), tool_source_index))
                        group.sources.append(ContextSource.model_validate(memory_item))
                group.provider_items.extend(response.provider_items)
                for index, provider in enumerate(response.provider_items):
                    group.sources.append(
                        source(
                            f"{run.state.run_id}:response:{response_id}:provider:{index}",
                            "provider",
                            provider,
                            version=str(item.version),
                        )
                    )
            if current:
                protected = group
            else:
                history.append(group)
        return protected, history

    async def prepare(
        self,
        record: RunRecord,
        context: RunContext,
        model_call_id: str,
        work: "_Work[None]",
        end: float,
    ) -> ModelRequest:
        agent = self.runtime._agents[(record.agent_name, record.revision)]
        retrieval = (
            await retrieve(self.storage, record, agent.memory, work, end)
            if agent.memory is not None
            else Retrieval([], "disabled")
        )
        memory_failed = (
            agent.memory is not None
            and agent.memory.retrieval == "required"
            and retrieval.status in ("failed", "timeout")
        )
        async with self.storage.transaction() as tx:
            record = await tx.get_run(context, record.state.run_id)
            if record.state.cancel_requested:
                raise asyncio.CancelledError
            checkpoint = await tx.get_checkpoint(context, record.state.run_id)
            count = cast(int, checkpoint.data.get("model_step_count", 0))
            if count >= record.limits.max_model_steps:
                raise _RunFailure("limit_exceeded", "model")
            protected, history = await self.history(record, tx)
            grants = await self.runtime._files.validate(record, tx=tx, check_bytes=True)
            if grants:
                descriptors: list[StrictJson] = [
                    {
                        **grant.ref.model_dump(mode="json"),
                        "name": grant.name,
                        "sandbox_path": f"/work/inputs/{grant.ref.artifact_id}/{grant.name}",
                    }
                    for grant in grants
                ]
                protected.messages.append(
                    ModelMessage(
                        role="user",
                        content="Granted artifact descriptors (file data): "
                        + canonical_json(descriptors),
                    )
                )
                for grant, descriptor in zip(grants, descriptors, strict=True):
                    protected.sources.append(
                        source(
                            "artifact:" + grant.ref.artifact_id,
                            "artifact",
                            descriptor,
                            version=grant.ref.sha256,
                        )
                    )
            if agent.instructions:
                message = ModelMessage(role="system", content=agent.instructions)
                protected.messages.insert(0, message)
                protected.sources.insert(
                    0,
                    source(
                        f"agent:{record.agent_name}:{record.revision}:instructions",
                        "instructions",
                        message.model_dump(mode="json"),
                    ),
                )
                protected.tool_refs = [
                    (message_index + 1, source_index + 1, ref, version)
                    for message_index, source_index, ref, version in protected.tool_refs
                ]
                protected.memory_refs = [(m + 1, t + 1) for m, t in protected.memory_refs]
            template = ModelRequest(
                model_call_id=model_call_id,
                messages=[],
                tools=[
                    ModelTool(name=t.name, description=t.description, parameters=t.parameters)
                    for t in agent._execution_tools
                ],
                output_schema=None if agent.output_type is str else agent.output_codec.schema,
            )
            protected.sources.append(
                source(
                    "request-envelope",
                    "schema",
                    {
                        "request_overhead": 256,
                        "per_protocol_item": 32,
                        "model_call_id": model_call_id,
                    },
                )
            )
            for tool in template.tools:
                protected.sources.append(
                    source(
                        f"tool-schema:{tool.name}",
                        "schema",
                        tool.model_dump(mode="json"),
                    )
                )
            if template.output_schema is not None:
                protected.sources.append(source("output-schema", "schema", template.output_schema))
            window = agent.model.capabilities.context_window
            budget = record.limits.max_input_tokens or min(
                16384, (window or 0) - record.limits.output_reserve_tokens
            )
            if window is not None:
                budget = min(budget, window - record.limits.output_reserve_tokens)
            excluded_memory = []
            if retrieval.records:
                stale = []
                for candidate in retrieval.records:
                    current = await tx.get_memory(candidate.scope, candidate.key)
                    if current is None or current.version != candidate.version or current.deleted:
                        stale.append(
                            memory_source(
                                candidate,
                                "memory_deleted"
                                if current is None or current.deleted
                                else "memory_version_changed",
                            ).model_copy(
                                update={
                                    "included": False,
                                    "transform": "excluded",
                                    "content": None,
                                }
                            )
                        )
                if stale:
                    excluded_memory = stale
                    assert agent.memory is not None
                    retrieval.records = await tx.recall_memory(
                        MemoryScope(
                            tenant_id=context.tenant_id,
                            user_id=context.user_id,
                            agent_name=record.agent_name,
                        ),
                        limit=agent.memory.limit,
                    )
            request, manifest, overflow = assemble(
                template,
                protected,
                history,
                run_id=record.state.run_id,
                agent_name=record.agent_name,
                revision=record.revision,
                budget=budget,
                preview_bytes=record.limits.tool_preview_bytes,
                memories=[
                    (memory_message(item), memory_source(item, "retrieved"))
                    for item in retrieval.records
                ],
                memory_budget=min(
                    int(max(0, budget) * agent.context_policy.memory_fraction),
                    agent.context_policy.memory_max_tokens,
                ),
                retrieval_status=retrieval.status,
                excluded_memory=excluded_memory,
            )
            rejected = overflow or memory_failed
            state = "rejected" if rejected else "prepared"
            for kind, payload in (
                ("model_input", {"schema_version": 1, "request": request.model_dump(mode="json")}),
                ("context", {"schema_version": 1, "manifest": manifest.model_dump(mode="json")}),
            ):
                await tx.put_item(
                    context,
                    RunItem(
                        run_id=record.state.run_id,
                        kind=cast(Any, kind),
                        item_id=model_call_id,
                        state=state,
                        data=cast(dict[str, StrictJson], payload),
                    ),
                )
            event_data: dict[str, StrictJson] = {
                "model_call_id": model_call_id,
                "context_manifest": project(manifest, content=False, summary=True).model_dump(
                    mode="json"
                ),
            }
            if rejected:
                event_data["error_code"] = (
                    "memory_unavailable" if memory_failed else "context_overflow"
                )
            version = record.state.state_version + 1
            await tx.save_step(
                context,
                state=record.state.model_copy(
                    update={"state_version": version, "status": "running"}
                ),
                expected_version=version - 1,
                checkpoint=checkpoint.model_copy(
                    update={
                        "state_version": version,
                        "data": checkpoint.data
                        | {
                            "model_step_count": count + (not rejected),
                            "engine_schema": 1,
                            "pending_model_call": model_call_id if not rejected else None,
                            "pending_model_attempt_count": 0,
                        },
                    }
                ),
                events=[EventDraft(type="context.prepared", data=event_data)],
            )
        if memory_failed:
            raise _RunFailure("memory_unavailable", "memory")
        if overflow:
            raise _RunFailure("context_overflow", "context")
        return request

    def delta(self, run_id: str, text: str) -> None:
        for subscriber in tuple(self.subscribers.get(run_id, ())):
            subscriber.delta(text)

    async def model_call(
        self,
        agent: Agent[Any],
        request: ModelRequest,
        work: "_Work[None]",
        allowed_seconds: float,
        *,
        record: RunRecord,
    ) -> ModelResponse:
        async def collect() -> ModelResponse:
            await self.runtime._files.validate(record, check_bytes=True)
            response = None
            async for event in agent.model.generate(
                ModelRequest.from_json(request.model_dump_json())
            ):
                if response is not None:
                    raise ModelProtocolError("Adapter emitted data after completion")
                if isinstance(event, ModelComplete):
                    response = ModelResponse.from_json(event.response.model_dump_json())
                elif isinstance(event, ModelDelta):
                    self.delta(work_run_id, event.text)
                else:
                    raise ModelProtocolError("Unsupported model event")
            if response is None:
                raise ModelProtocolError("Adapter ended without completion")
            return response

        # The active controller call sets this association; no model field can inject a Run ID.
        work_run_id = next(key for key, value in self.works.items() if value is work)
        from drov.runtime.core import _observe, _owned_task

        work.checkpoint()
        task = _owned_task(collect, name="drov-model-attempt")
        work.track(task)
        task.add_done_callback(_observe)
        try:
            done, _ = await asyncio.wait({task}, timeout=max(0, allowed_seconds))
            if not done:
                task.cancel()
                raise _RunFailure("model_timeout", "model")
            return task.result()
        except BaseException:
            task.cancel()
            raise

    async def execute(self, run_id: str, context: RunContext, work: "_Work[None]") -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        attempt_count = 0
        try:
            record = await self.storage.get_run(context, run_id)
            agent = self.runtime._agents[(record.agent_name, record.revision)]
            checkpoint = await self.storage.get_checkpoint(context, run_id)
            prior_active = cast(Any, checkpoint.data.get("active_seconds", 0))
            attempt_count = cast(Any, checkpoint.data.get("model_attempt_count", 0))
            if (
                type(prior_active) not in (int, float)
                or prior_active < 0
                or type(attempt_count) is not int
            ):
                raise PersistenceUnavailable("Invalid cumulative execution budget")
            started -= prior_active
            end = min(
                started + record.limits.run_active_seconds,
                loop.time() + record.limits.advance_seconds,
            )
            end_code = "limit_exceeded"
            if record.limits.expires_at is not None:
                wall_end = (
                    loop.time() + (record.limits.expires_at - datetime.now(UTC)).total_seconds()
                )
                if wall_end < end:
                    end_code = "deadline_exceeded"
                end = min(end, wall_end)
            work.checkpoint()
            await self.step(run_id, context, [EventDraft(type="run.running")])
            pending_output = checkpoint.data.get("pending_output")
            if pending_output is not None:
                if not isinstance(pending_output, str):
                    raise PersistenceUnavailable("Invalid saved output reference")
                async with self.storage.transaction() as tx:
                    output_item = await tx.get_item(
                        context, run_id, "model_response", pending_output
                    )
                await self.finish_response(
                    record, ModelResponse.model_validate(output_item.data["response"]), work, end
                )
                return
            pending_plan = checkpoint.data.get("pending_plan")
            if pending_plan is not None:
                if not isinstance(pending_plan, str):
                    raise PersistenceUnavailable("Invalid pending model plan")
                async with self.storage.transaction() as tx:
                    saved = await tx.get_item(context, run_id, "model_response", pending_plan)
                saved_response = ModelResponse.model_validate(saved.data["response"])
                operation_ids = cast(list[str], saved.data["operation_ids"])
                if not await self.execute_plan(
                    record, saved_response, operation_ids, work, started, end
                ):
                    return
            while True:
                work.checkpoint()
                if loop.time() >= end:
                    raise _RunFailure(end_code, "runtime")
                self.runtime.validate_record(await self.storage.get_run(context, run_id))
                record = await self.storage.get_run(context, run_id)
                current_checkpoint = await self.storage.get_checkpoint(context, run_id)
                saved_call = current_checkpoint.data.get("pending_model_call")
                if saved_call is not None:
                    if not isinstance(saved_call, str):
                        raise PersistenceUnavailable("Invalid saved model call reference")
                    call_id = saved_call
                    async with self.storage.transaction() as tx:
                        input_item = await tx.get_item(context, run_id, "model_input", call_id)
                    request = ModelRequest.from_json(canonical_json(input_item.data["request"]))
                    call_attempts = current_checkpoint.data.get("pending_model_attempt_count", 0)
                    if type(call_attempts) is not int or call_attempts < 0:
                        raise PersistenceUnavailable("Invalid saved model attempt count")
                else:
                    call_id = str(uuid4())
                    request = await self.prepare(record, context, call_id, work, end)
                    call_attempts = 0
                if call_attempts >= record.limits.model_retries + 1:
                    raise _RunFailure("model_attempt_limit", "model")
                response: ModelResponse | None = None
                for attempt in range(call_attempts, record.limits.model_retries + 1):
                    work.checkpoint()
                    allowance = min(record.limits.model_timeout_seconds, end - loop.time())
                    if allowance <= 0:
                        raise _RunFailure(end_code, "model")
                    attempt_id = str(uuid4())
                    attempt_start = loop.time()
                    prior_usage = (await self.storage.get_run(context, run_id)).state.usage
                    await self.step(
                        run_id,
                        context,
                        [
                            EventDraft(
                                type="model.attempt_started",
                                data={
                                    "model_call_id": call_id,
                                    "attempt_id": attempt_id,
                                },
                            )
                        ],
                        data={
                            "active_seconds": attempt_start - started + allowance,
                            "model_attempt_count": attempt_count + 1,
                            "pending_model_attempt_count": attempt + 1,
                        },
                        usage=_usage(prior_usage, Usage(), first=attempt_count == 0),
                    )
                    work.checkpoint()
                    try:
                        response = await self.model_call(
                            agent, request, work, min(allowance, end - loop.time()), record=record
                        )
                    except TransientModelError as exc:
                        attempt_count += 1
                        await self.step(
                            run_id,
                            context,
                            [
                                EventDraft(
                                    type="model.attempt_failed",
                                    data={
                                        "model_call_id": call_id,
                                        "attempt_id": attempt_id,
                                        "error_code": "transient_model_error",
                                    },
                                )
                            ],
                            data={"active_seconds": loop.time() - started},
                            usage=_usage(prior_usage, Usage(), first=attempt_count == 1),
                        )
                        if attempt >= record.limits.model_retries:
                            raise _RunFailure("transient_model_error", "model") from None
                        await asyncio.sleep(
                            min(
                                max(
                                    record.limits.retry_backoff_seconds,
                                    exc.retry_after_seconds or 0,
                                ),
                                max(0, end - loop.time()),
                            )
                        )
                        continue
                    except PersistenceUnavailable:
                        raise
                    except Exception as exc:
                        code = (
                            exc.code if isinstance(exc, (DrovError, _RunFailure)) else "model_error"
                        )
                        await self.step(
                            run_id,
                            context,
                            [
                                EventDraft(
                                    type="model.attempt_failed",
                                    data={
                                        "model_call_id": call_id,
                                        "attempt_id": attempt_id,
                                        "error_code": code,
                                    },
                                )
                            ],
                        )
                        raise _RunFailure(code, "model") from None
                    attempt_count += 1
                    break
                assert response is not None
                if response.finish_reason in ("refusal", "incomplete"):
                    await self.step(
                        run_id,
                        context,
                        [
                            EventDraft(
                                type="model.attempt_failed",
                                data={
                                    "model_call_id": call_id,
                                    "attempt_id": attempt_id,
                                    "error_code": "model_" + response.finish_reason,
                                },
                            )
                        ],
                        data={"active_seconds": loop.time() - started},
                        usage=_usage(prior_usage, response.usage, first=attempt_count == 1),
                    )
                    raise _RunFailure("model_" + response.finish_reason, "model")
                operation_ids = [str(uuid4()) for _ in response.tool_calls]
                checkpoint = await self.storage.get_checkpoint(context, run_id)
                ids = cast(list[StrictJson], checkpoint.data.get("response_ids", []))
                await self.step(
                    run_id,
                    context,
                    [
                        EventDraft(
                            type="model.attempt_completed",
                            data={
                                "model_call_id": call_id,
                                "attempt_id": attempt_id,
                                "message": response.message,
                                "tool_calls": [
                                    call.model_dump(mode="json") for call in response.tool_calls
                                ],
                                "usage": response.usage.model_dump(mode="json"),
                            },
                        )
                    ],
                    data={
                        "response_ids": [*ids, call_id],
                        "active_seconds": loop.time() - started,
                        "pending_plan": call_id if response.tool_calls else None,
                        "pending_model_call": None,
                        "pending_model_attempt_count": 0,
                        "pending_output": call_id if not response.tool_calls else None,
                    },
                    usage=_usage(prior_usage, response.usage, first=attempt_count == 1),
                    items=(
                        RunItem(
                            run_id=run_id,
                            kind="model_response",
                            item_id=call_id,
                            state="committed",
                            data={
                                "schema_version": 1,
                                "response": response.model_dump(mode="json"),
                                "operation_ids": cast(list[StrictJson], list(operation_ids)),
                            },
                        ),
                    ),
                )
                if not response.tool_calls:
                    await self.finish_response(record, response, work, end)
                    return
                if not await self.execute_plan(record, response, operation_ids, work, started, end):
                    return
        except PersistenceUnavailable:
            raise
        except asyncio.CancelledError:
            await self.finish(run_id, context, cancelled=True)
        except _RunFailure as exc:
            await self.finish(
                run_id,
                context,
                error=RunError(
                    code=exc.code,
                    message="Run could not complete: " + exc.code,
                    stage=exc.stage,
                ),
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, DrovError) else "execution_error"
            if isinstance(exc, CapacityExceeded):
                code = "limit_exceeded"
            await self.finish(
                run_id,
                context,
                error=RunError(
                    code=code,
                    message="Run could not complete: " + code,
                    stage="runtime",
                ),
            )

    async def finish_response(
        self,
        record: RunRecord,
        response: ModelResponse,
        work: "_Work[None]",
        end: float,
    ) -> None:
        run_id, context = record.state.run_id, self.resource(record).owner
        agent = self.runtime._agents[(record.agent_name, record.revision)]
        if response.message is None:
            raise _RunFailure("model_protocol_error", "output")
        try:
            decoded = (
                response.message
                if agent.output_type is str
                else agent.output_codec.parse(
                    response.message, max_bytes=record.limits.max_tool_result_bytes
                )
            )
            output = agent.output_codec.encode(decoded)
            if len(canonical_json(output).encode("utf-8")) > record.limits.max_tool_result_bytes:
                raise ValueError("Output exceeds limit")
        except (TypeError, ValueError):
            raise _RunFailure("invalid_output", "output") from None
        work.checkpoint()
        if asyncio.get_running_loop().time() >= end:
            raise _RunFailure(
                "deadline_exceeded"
                if record.limits.expires_at is not None
                and datetime.now(UTC) >= record.limits.expires_at
                else "limit_exceeded",
                "runtime",
            )
        await self.finish(run_id, context, output=output)

    async def execute_plan(
        self,
        record: RunRecord,
        response: ModelResponse,
        operation_ids: list[str],
        work: "_Work[None]",
        started: float,
        end: float,
    ) -> bool:
        loop = asyncio.get_running_loop()
        run_id, context = record.state.run_id, self.resource(record).owner
        agent = self.runtime._agents[(record.agent_name, record.revision)]
        tools = {tool.name: tool for tool in agent._execution_tools}
        for call, operation_id in zip(response.tool_calls, operation_ids, strict=True):
            work.checkpoint()
            if call.name not in tools:
                raise _RunFailure("unknown_tool", "tool")
            previous = await OperationJournal(self.storage, context).get(run_id, operation_id)
            if previous is not None and previous.status not in (
                "prepared",
                "dispatched",
                "unknown",
            ):
                continue
            selected_tool = tools[call.name]
            retries = record.limits.read_tool_retries if selected_tool.effect == "read" else 0
            allowance = min(
                end - loop.time(),
                record.limits.tool_timeout_seconds * (retries + 1)
                + record.limits.retry_backoff_seconds * retries,
            )
            if allowance <= 0:
                raise _RunFailure("limit_exceeded", "tool")
            await self.step(
                run_id,
                context,
                [
                    EventDraft(
                        type="run.running",
                        data={
                            "operation_id": operation_id,
                        },
                    )
                ],
                data={"active_seconds": loop.time() - started + allowance},
            )
            work.checkpoint()
            dispatcher = (
                MemoryDispatcher(
                    storage=self.storage,
                    policy=self.runtime.policy,
                    authorizer=self.runtime.authorizer,
                    validate=self.runtime.validate_record,
                )
                if isinstance(selected_tool, MemoryTool)
                else self.interactions
                if isinstance(selected_tool, HumanInput)
                else self.runtime._sandboxes
                if isinstance(selected_tool, PythonSandbox)
                else self.runtime._tool_dispatcher
            )
            assert dispatcher is not None
            result = await dispatcher.execute(
                cast(Any, selected_tool),
                canonical_json(call.arguments),
                work=work,
                record=record,
                operation_id=operation_id,
                deadline=min(end, loop.time() + allowance),
                continuation=True,
            )
            if result.permission is not None and result.permission.decision == "ask":
                if not self.runtime._temporary:
                    await self.interactions.suspend(
                        record, result.operation, active_seconds=loop.time() - started
                    )
                    return False
                await OperationJournal(self.storage, context).commit(
                    result.operation.model_copy(
                        update={
                            "version": result.operation.version + 1,
                            "status": "not_executed",
                            "error_code": "interaction_unavailable",
                        }
                    ),
                    EventDraft(type="tool.not_executed", data={"operation_id": operation_id}),
                )
                raise _RunFailure("interaction_unavailable", "tool")
            if isinstance(selected_tool, HumanInput) and result.operation.status == "prepared":
                if self.runtime._temporary:
                    raise _RunFailure("interaction_unavailable", "tool")
                await self.interactions.suspend(
                    record,
                    result.operation,
                    active_seconds=loop.time() - started,
                    input_tool=selected_tool,
                )
                return False
            if result.operation.error_code == "interaction_expired":
                raise _RunFailure("interaction_expired", "tool")
            if result.operation.effect != "read" and result.operation.status in (
                "dispatched",
                "unknown",
            ):
                await self.reconciliation.suspend(
                    run_id, context, active_seconds=loop.time() - started
                )
                return False
            if any(not call.done() for call in work._calls):
                raise _RunFailure(result.operation.error_code or "tool_timeout", "tool")
            # Tool errors become explicit messages; the model may choose another action.
            await self.step(
                run_id,
                context,
                [
                    EventDraft(
                        type="run.running",
                        data={
                            "operation_id": operation_id,
                        },
                    )
                ],
                data={"active_seconds": loop.time() - started},
            )
        await self.step(
            run_id, context, [EventDraft(type="run.running")], data={"pending_plan": None}
        )
        return True

    def typed(self, record: RunRecord) -> RunOutcome[Any]:
        outcome = record.state.outcome
        if outcome is None:
            if record.state.status != "suspended":
                raise PersistenceUnavailable("Run has no outcome")
            return Suspended(
                run_id=record.state.run_id,
                state_version=record.state.state_version,
                usage=record.state.usage,
                reason=cast(Any, record.state.reason),
                pending=record.state.pending,
            )
        if isinstance(outcome, Completed):
            self.runtime.validate_record(record)
            agent = self.runtime._agents[(record.agent_name, record.revision)]
            return outcome.model_copy(update={"output": agent.output_codec.decode(outcome.output)})
        return outcome

    async def wait(self, run_id: str, *, context: RunContext) -> RunOutcome[Any]:
        while True:
            record = await self.visible(run_id, context, True)
            if record.state.status in TERMINAL or record.state.status == "suspended":
                return self.typed(record)
            if run_id in self.failures:
                raise self.failures[run_id]
            work = self.works.get(run_id)
            if work is None:
                raise ConfigurationError("Run has no active owner; use Runtime.recover explicitly")
            # Shield execution from a cancelled waiter. Reauthorize at the return boundary.
            await work.wait()

    async def cancel(self, run_id: str, *, context: RunContext) -> CancelReceipt:
        self.runtime._require_open()
        async with self.storage.transaction() as tx:
            record = await tx._locate_run(context, run_id)
        await self.authorize(context, self.resource(record), ("cancel",))
        owner = self.resource(record).owner

        async def apply() -> CancelReceipt:
            async with self.storage.transaction() as tx:
                record = await tx.get_run(owner, run_id)
                if record.state.status not in TERMINAL and not record.state.cancel_requested:
                    checkpoint = await tx.get_checkpoint(owner, run_id)
                    version = record.state.state_version + 1
                    await tx.save_step(
                        owner,
                        state=record.state.model_copy(
                            update={
                                "cancel_requested": True,
                                "state_version": version,
                            }
                        ),
                        expected_version=version - 1,
                        checkpoint=checkpoint.model_copy(update={"state_version": version}),
                        events=[EventDraft(type="run.cancel_requested")],
                    )
            work = self.works.get(run_id)
            if work is not None:
                work.request_cancel()
            elif record.state.status not in TERMINAL:
                await self.finish(run_id, owner, cancelled=True)
            current = await self.storage.get_run(owner, run_id)
            return CancelReceipt(
                run_id=run_id,
                state_version=current.state.state_version,
                cancel_requested=current.state.cancel_requested,
                status=current.state.status,
            )

        return await self.control(apply)

    @staticmethod
    def cursor(run_id: str, after: str | None) -> int:
        if after is None:
            return 0
        prefix, separator, suffix = after.rpartition(":")
        if not separator or prefix != run_id or not suffix.isascii() or not suffix.isdecimal():
            raise InvalidCursor("Cursor must be a same-run run_id:seq")
        if len(suffix) > 20 or suffix != str(int(suffix)):
            raise InvalidCursor("Invalid event sequence")
        return int(suffix)

    async def context_record(
        self, tx: Transaction, context: RunContext, run_id: str, model_call_id: str
    ) -> ContextManifest:
        item = await tx.get_item(context, run_id, "context", model_call_id)
        saved_input = await tx.get_item(context, run_id, "model_input", model_call_id)
        if (
            type(item.data.get("schema_version")) is not int
            or item.data["schema_version"] != 1
            or type(saved_input.data.get("schema_version")) is not int
            or saved_input.data["schema_version"] != 1
        ):
            raise PersistenceUnavailable("Unsupported context payload")
        manifest = ContextManifest.model_validate(item.data["manifest"])
        if (
            manifest.run_id != run_id
            or manifest.model_call_id != model_call_id
            or manifest.input_ref != model_call_id
            or manifest.input_hash != digest(saved_input.data["request"])
        ):
            raise PersistenceUnavailable("Context does not match its saved input")
        try:
            response = await tx.get_item(context, run_id, "model_response", model_call_id)
        except RecordNotFound:
            return manifest
        if (
            type(response.data.get("schema_version")) is not int
            or response.data["schema_version"] != 1
        ):
            raise PersistenceUnavailable("Unsupported model response payload")
        return manifest.model_copy(
            update={"reported_usage": ModelResponse.model_validate(response.data["response"]).usage}
        )

    async def get_context(
        self, run_id: str, model_call_id: str, *, context: RunContext, include_content: bool = False
    ) -> ContextManifest:
        record = await self.visible(run_id, context, include_content)
        async with self.storage.transaction() as tx:
            manifest = await self.context_record(tx, record.scope, run_id, model_call_id)
        # Permissions may change while storage is being read; saved bodies grant no access.
        await self.authorize(
            context,
            self.resource(record),
            ("read", "view_content") if include_content else ("read",),
        )
        return project(manifest, content=include_content)

    async def inspect(
        self,
        run_id: str,
        *,
        context: RunContext,
        after: str | None = None,
        limit: int = 100,
        include_content: bool = False,
    ) -> RunInspection:
        visible = await self.visible(run_id, context, include_content)
        owner = self.resource(visible).owner
        if type(limit) is not int or not 1 <= limit <= min(500, self.runtime.limits.max_page_size):
            raise ValueError("Invalid page size")
        sequence = self.cursor(run_id, after)
        async with self.storage.transaction() as tx:
            record = await tx.get_run(owner, run_id)
            rows = await tx.events(owner, run_id, after=sequence, limit=limit)
            manifests = []
            for row in rows:
                if row.type != "context.prepared" or "context_manifest" not in row.data:
                    # T-06 records have no manifest; their state/events stay readable.
                    continue
                summary = ContextManifest.model_validate(row.data["context_manifest"])
                if (
                    summary.run_id != run_id
                    or summary.model_call_id != row.data["model_call_id"]
                    or summary.content_visible
                    or summary.sources
                ):
                    raise PersistenceUnavailable("Invalid context index")
                manifests.append(summary)
        await self.authorize(
            context,
            self.resource(record),
            ("read", "view_content") if include_content else ("read",),
        )
        events: list[DurableEvent | UnknownDurableEvent] = []
        for row in rows:
            parsed = parse_event(row.model_dump_json())
            projected = (
                {}
                if isinstance(parsed, UnknownDurableEvent)
                else row.data
                if include_content
                else {key: value for key, value in row.data.items() if key in METADATA_KEYS}
            )
            events.append(
                parsed.model_copy(
                    update={
                        "data": projected,
                        "content_visible": include_content
                        and not isinstance(parsed, UnknownDurableEvent),
                    }
                )
            )
        snapshot = RunSnapshot(
            **record.state.model_dump(exclude={"outcome", "pending"}),
            agent_name=record.agent_name,
            revision=record.revision,
            session_id=record.scope.session_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            outcome=record.state.outcome if include_content else None,
            pending=(
                record.state.pending
                if include_content or record.state.pending is None
                else InteractionSummary.model_validate(
                    record.state.pending.model_dump(
                        include={
                            "schema_version",
                            "request_id",
                            "kind",
                            "run_id",
                            "operation_id",
                            "created_at",
                            "expires_at",
                        }
                    )
                )
            ),
            content_visible=include_content,
        )
        return RunInspection(
            snapshot=snapshot,
            events=events,
            next_cursor=f"{run_id}:{rows[-1].seq}" if rows else None,
            has_more=bool(rows and rows[-1].seq < record.last_seq),
            context_manifests=manifests,
        )
