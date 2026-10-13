"""Private read-tool dispatcher, with durable barriers and real call ownership."""

import asyncio
import hashlib
import inspect
from collections.abc import Callable, Mapping
from concurrent.futures import Future as ThreadFuture
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from drov._json import StrictJson, canonical_json
from drov.auth import Authorizer, ToolResource
from drov.errors import CapacityExceeded, ConfigurationError, Conflict, TransientToolError
from drov.storage.base import Storage
from drov.storage.records import EventDraft, RunRecord
from drov.tools._pool import Admission, ToolPool
from drov.tools.operations import OperationJournal, ToolOperation
from drov.tools.permissions import PermissionDecision, PermissionPolicy
from drov.tools.python import PythonTool
from drov.tools.schema import ToolContext
from drov.types import DurableEventType, RunContext

if TYPE_CHECKING:
    from drov.runtime.core import _Work


async def _invoke(tool: PythonTool, bound: dict[str, object], ready: asyncio.Event) -> Any:
    await ready.wait()
    return await tool.function(**bound)


def _observe(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()


@dataclass(frozen=True)
class ToolExecution:
    operation: ToolOperation
    permission: PermissionDecision | None = None


class ToolDispatcher:
    def __init__(
        self,
        *,
        storage: Storage,
        dependencies: Mapping[str, object],
        policy: PermissionPolicy,
        authorizer: Authorizer,
        pool: ToolPool,
        validate: Callable[[RunRecord, PythonTool], None],
    ) -> None:
        self.storage, self.dependencies = storage, dependencies
        self.policy, self.authorizer, self.pool, self.validate = policy, authorizer, pool, validate
        self._inflight: set[tuple[str, str, str, str]] = set()

    async def execute(
        self,
        tool: PythonTool,
        arguments: str | bytes,
        *,
        work: "_Work[Any]",
        record: RunRecord,
        operation_id: str,
        deadline: float | None = None,
    ) -> ToolExecution:
        if work.task is not asyncio.current_task():
            raise ConfigurationError("Tool dispatch belongs to the kernel execution task")
        scope = RunContext(tenant_id=record.scope.tenant_id, user_id=record.scope.user_id)
        key = (scope.tenant_id, scope.user_id, record.state.run_id, operation_id)
        if key in self._inflight:
            raise Conflict("This operation already has an active dispatcher")
        self._inflight.add(key)
        try:
            return await self._execute(tool, arguments, work, record, scope, operation_id, deadline)
        finally:
            self._inflight.remove(key)

    async def _execute(
        self,
        tool: PythonTool,
        arguments: str | bytes,
        work: "_Work[Any]",
        record: RunRecord,
        scope: RunContext,
        operation_id: str,
        deadline: float | None,
    ) -> ToolExecution:
        work.checkpoint()
        run = await self.storage.get_run(scope, record.state.run_id)
        self.validate(run, tool)
        if run.state.status != "running" or run.state.cancel_requested:
            raise Conflict("Run is not accepting tool dispatch")
        context = ToolContext(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            run_id=run.state.run_id,
            operation_id=operation_id,
            deps=self.dependencies,
        )
        invalid = False
        bound: dict[str, object] = {}
        normalized: dict[str, StrictJson] = {}
        try:
            bound = tool.bind_arguments(
                arguments, context=context, max_bytes=run.limits.max_arguments_bytes
            )
            normalized = {
                name: codec.encode(bound[name]) for name, codec in tool.argument_codecs.items()
            }
            request: object = normalized
        except (ValueError, TypeError):
            invalid = True
            request = {
                "invalid_input_hash": hashlib.sha256(
                    arguments.encode("utf-8") if isinstance(arguments, str) else arguments
                ).hexdigest()
            }
        request_hash = hashlib.sha256(
            canonical_json(
                {
                    "tool": tool.name,
                    "revision": tool.revision,
                    "tenant_id": scope.tenant_id,
                    "user_id": scope.user_id,
                    "run_id": run.state.run_id,
                    "operation_id": operation_id,
                    "arguments": request,
                }
            ).encode("utf-8")
        ).hexdigest()
        journal = OperationJournal(self.storage, scope)
        previous = await journal.get(run.state.run_id, operation_id)
        if previous is not None:
            if previous.request_hash != request_hash:
                raise Conflict("Operation ID was used for a different tool call")
            # Stored operations are facts, never implicit approval/retry permission.
            return ToolExecution(previous)
        operation = ToolOperation(
            run_id=run.state.run_id,
            operation_id=operation_id,
            tool_name=tool.name,
            tool_revision=tool.revision,
            effect=tool.effect,
            request_hash=request_hash,
            arguments=normalized,
            status="prepared",
        )
        await journal.commit(
            operation,
            EventDraft(
                type="tool.prepared",
                data={
                    "operation_id": operation_id,
                    "tool_name": tool.name,
                    "arguments": normalized,
                },
            ),
        )

        async def save(
            status: str, code: str | None, *, result: StrictJson = None, has_result: bool = False
        ) -> ToolOperation:
            nonlocal operation
            updated = ToolOperation.model_validate(
                operation.model_dump()
                | {
                    "version": operation.version + 1,
                    "status": status,
                    "error_code": code,
                    "result": result,
                    "has_result": has_result,
                }
            )
            await journal.commit(
                updated,
                EventDraft(
                    type=cast(
                        DurableEventType,
                        "tool.denied" if code == "permission_denied" else "tool." + status,
                    ),
                    data={
                        "operation_id": operation_id,
                        "error_code": code,
                        **({"result": result} if has_result else {}),
                    },
                ),
            )
            operation = updated
            return updated

        if invalid:
            return ToolExecution(await save("not_executed", "invalid_arguments"))
        resource = ToolResource(
            owner=scope,
            run_id=run.state.run_id,
            session_id=run.scope.session_id,
            tool_name=tool.name,
            operation_id=operation_id,
            arguments=normalized,
        )

        resource_json = resource.model_dump_json()

        async def permission() -> PermissionDecision:
            work.checkpoint()
            current = await self.storage.get_run(scope, run.state.run_id)
            self.validate(current, tool)
            if current.state.cancel_requested or current.state.status != "running":
                raise asyncio.CancelledError
            try:
                async with asyncio.timeout(max(0, end - asyncio.get_running_loop().time())):
                    decision = await self.policy.decide(
                        tool,
                        actor=scope,
                        resource=ToolResource.from_json(resource_json),
                        authorizer=self.authorizer,
                    )
            except Exception:
                decision = PermissionDecision(decision="deny", reason="authorization_error")
            work.checkpoint()
            return decision

        loop = asyncio.get_running_loop()
        end = min(
            deadline if deadline is not None else float("inf"),
            loop.time() + min(run.limits.advance_seconds, run.limits.run_active_seconds),
        )
        call: asyncio.Task[Any] | ThreadFuture[Any] | None = None
        admission: Admission | None = None
        decision: PermissionDecision | None = None
        try:
            for attempt in range(run.limits.read_tool_retries + 1):
                decision = await permission()
                if decision.decision == "deny":
                    code = (
                        "authorization_error"
                        if decision.reason == "authorization_error"
                        else "permission_denied"
                    )
                    return ToolExecution(await save("not_executed", code), decision)
                if decision.decision == "ask":
                    operation = operation.model_copy(
                        update={"error_code": "approval_required", "version": operation.version + 1}
                    )
                    await journal.commit(
                        operation,
                        EventDraft(type="tool.prepared", data={"operation_id": operation_id}),
                    )
                    return ToolExecution(operation, decision)
                if tool.effect != "read":
                    return ToolExecution(
                        await save("not_executed", "effect_not_supported"), decision
                    )
                if loop.time() >= end:
                    return ToolExecution(await save("not_executed", "deadline_exceeded"), decision)
                synchronous = not inspect.iscoroutinefunction(tool.function)
                if synchronous:
                    try:
                        admission = self.pool.reserve()
                    except CapacityExceeded:
                        return ToolExecution(
                            await save("not_executed", "capacity_exceeded"), decision
                        )
                    try:
                        await admission.wait(
                            max(0, min(run.limits.tool_timeout_seconds, end - loop.time()))
                        )
                    except TimeoutError:
                        return ToolExecution(await save("not_executed", "queue_timeout"), decision)
                # Recheck after queue waiting; a committed marker never grants access.
                decision = await permission()
                if decision.decision != "allow":
                    if admission is not None:
                        admission.release()
                        admission = None
                    if decision.decision == "deny":
                        return ToolExecution(
                            await save("not_executed", "permission_denied"), decision
                        )
                    return ToolExecution(operation, decision)
                dispatched = operation.model_copy(
                    update={
                        "version": operation.version + 1,
                        "status": "dispatched",
                        "attempts": operation.attempts + 1,
                        "attempt_id": str(uuid4()),
                        "error_code": None,
                    }
                )
                await journal.commit(
                    dispatched,
                    EventDraft(
                        type="tool.dispatched",
                        data={"operation_id": operation_id, "attempt_id": dispatched.attempt_id},
                    ),
                )
                operation = dispatched
                work.checkpoint()
                decision = await permission()
                if decision.decision != "allow":
                    return ToolExecution(await save("not_executed", "permission_denied"), decision)
                if loop.time() >= end:
                    return ToolExecution(await save("not_executed", "deadline_exceeded"), decision)
                if synchronous:
                    assert admission is not None
                    try:
                        call = self.pool.submit(admission, lambda: tool.function(**bound))
                    except Exception:
                        return ToolExecution(
                            await save("not_executed", "tool_dispatch_error"), decision
                        )
                    admission = None
                    work.track(call)
                    pending = asyncio.wrap_future(call)
                else:
                    ready = asyncio.Event()

                    coroutine = _invoke(tool, bound, ready)
                    try:
                        call = asyncio.create_task(coroutine, name="drov-tool-call")
                    except BaseException:
                        coroutine.close()
                        raise
                    work.track(call)
                    ready.set()
                    pending = call
                pending.add_done_callback(_observe)
                done, _ = await asyncio.wait(
                    {pending},
                    timeout=max(0, min(run.limits.tool_timeout_seconds, end - loop.time())),
                )
                if not done:
                    call.cancel()
                    return ToolExecution(await save("failed_known", "tool_timeout"), decision)
                try:
                    value = pending.result()
                except TransientToolError:
                    await save("failed_known", "transient_tool_error")
                    if (
                        attempt < run.limits.read_tool_retries
                        and loop.time() + run.limits.retry_backoff_seconds < end
                    ):
                        await asyncio.sleep(run.limits.retry_backoff_seconds)
                        call = None
                        continue
                    return ToolExecution(operation, decision)
                except (Exception, asyncio.CancelledError):
                    return ToolExecution(await save("failed_known", "tool_error"), decision)
                try:
                    if inspect.iscoroutine(value):
                        value.close()
                        raise ValueError("Unexpected coroutine result")
                    encoded = tool.result_codec.encode(value)
                    if (
                        len(canonical_json(encoded).encode("utf-8"))
                        > run.limits.max_tool_result_bytes
                    ):
                        raise ValueError("Result too large")
                except (ValueError, TypeError, RecursionError, OverflowError):
                    return ToolExecution(await save("failed_known", "output_unavailable"), decision)
                return ToolExecution(
                    await save("succeeded", None, result=encoded, has_result=True), decision
                )
        except asyncio.CancelledError:
            if call is not None:
                call.cancel()
            await save("failed_known" if call is not None else "not_executed", "tool_cancelled")
            raise
        finally:
            if admission is not None:
                admission.release()
        raise AssertionError("Tool retry loop must produce a real result or failure")
