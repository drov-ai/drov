"""Local operations: mutation, result, budget and events commit together."""

import asyncio
import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from drov._json import StrictJson, canonical_json
from drov.auth import Authorizer, ToolResource
from drov.errors import ConfigurationError, Conflict, InteractionExpired, PersistenceUnavailable
from drov.memory.tools import TOOLS, Forgotten, MemoryTool, Remembered, memory_source, value
from drov.storage.base import Storage
from drov.storage.records import EventDraft, MemoryRecord, MemoryScope, RunRecord
from drov.storage.transaction import Transaction
from drov.tools.approvals import approval_status
from drov.tools.execution import ToolExecution
from drov.tools.operations import OperationJournal, ToolOperation
from drov.tools.permissions import PermissionDecision, PermissionPolicy
from drov.tools.schema import ToolContext
from drov.types import RunContext

if TYPE_CHECKING:
    from drov.runtime.core import _Work


class MemoryDispatcher:
    def __init__(
        self,
        *,
        storage: Storage,
        policy: PermissionPolicy,
        authorizer: Authorizer,
        validate: Callable[[RunRecord], object],
    ) -> None:
        self.storage, self.policy, self.authorizer, self.validate = (
            storage,
            policy,
            authorizer,
            validate,
        )

    async def execute(
        self,
        tool: MemoryTool,
        arguments: str,
        *,
        work: "_Work[Any]",
        record: RunRecord,
        operation_id: str,
        deadline: float,
        continuation: bool = False,
    ) -> ToolExecution:
        if work.task is not asyncio.current_task() or not any(tool is item for item in TOOLS):
            raise ConfigurationError("Memory dispatch requires a registered kernel handler")
        work.checkpoint()
        scope = RunContext(tenant_id=record.scope.tenant_id, user_id=record.scope.user_id)
        run_id = record.state.run_id
        run = await self.storage.get_run(scope, run_id)
        deadline = min(
            deadline, asyncio.get_running_loop().time() + run.limits.tool_timeout_seconds
        )
        self.validate(run)
        journal = OperationJournal(self.storage, scope)
        normalized: dict[str, StrictJson] = {}
        invalid = False
        try:
            bound = tool.bind_arguments(
                arguments,
                context=ToolContext(
                    **scope.model_dump(), run_id=run_id, operation_id=operation_id, deps={}
                ),
                max_bytes=run.limits.max_arguments_bytes,
            )
            normalized = {
                key: codec.encode(bound[key]) for key, codec in tool.argument_codecs.items()
            }
            key = normalized.get("key")
            if key is not None and (not isinstance(key, str) or not key):
                raise ValueError("Memory key must be non-empty")
            if tool.name == "memory.recall":
                limit = normalized["limit"]
                if (
                    type(limit) is not int
                    or not 1 <= limit <= 50
                    or (key is not None and normalized["query"])
                ):
                    raise ValueError("Invalid recall arguments")
            else:
                if (
                    type(normalized["expected_version"]) is not int
                    or normalized["expected_version"] < 0
                ):
                    raise ValueError("Memory version must be nonnegative")
                if (
                    len(
                        canonical_json(
                            {k: v for k, v in normalized.items() if k in ("key", "content")}
                        ).encode("utf-8")
                    )
                    > run.limits.max_memory_bytes
                ):
                    raise ValueError("Memory exceeds the configured byte limit")
            request: object = normalized
        except (ValueError, TypeError):
            invalid = True
            request = {"invalid_input_hash": hashlib.sha256(arguments.encode("utf-8")).hexdigest()}
        request_hash = hashlib.sha256(
            canonical_json(
                {
                    "tool": tool.name,
                    "revision": tool.revision,
                    "tenant_id": scope.tenant_id,
                    "user_id": scope.user_id,
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "arguments": request,
                }
            ).encode("utf-8")
        ).hexdigest()
        previous = await journal.get(run_id, operation_id)
        if previous is not None:
            if previous.request_hash != request_hash:
                raise Conflict("Operation ID was used for another Memory call")
            if not continuation or previous.status != "prepared":
                return ToolExecution(previous)
        operation = previous or ToolOperation(
            run_id=run_id,
            operation_id=operation_id,
            tool_name=tool.name,
            tool_revision=tool.revision,
            effect=tool.effect,
            request_hash=request_hash,
            arguments=normalized,
            status="prepared",
        )
        if previous is None:
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

        async def reject(code: str, decision: PermissionDecision | None = None) -> ToolExecution:
            rejected = operation.model_copy(
                update={
                    "version": operation.version + 1,
                    "status": "not_executed",
                    "error_code": code,
                }
            )
            await journal.commit(
                rejected,
                EventDraft(
                    type="tool.not_executed",
                    data={
                        "operation_id": operation_id,
                        "error_code": code,
                    },
                ),
            )
            return ToolExecution(rejected, decision)

        if invalid:
            return await reject("invalid_arguments")
        resource = ToolResource(
            owner=scope,
            run_id=run_id,
            session_id=run.scope.session_id,
            tool_name=tool.name,
            operation_id=operation_id,
            arguments=normalized,
        ).model_dump_json()

        async def permission(tx: Transaction | None = None) -> PermissionDecision:
            work.checkpoint()
            try:
                async with asyncio.timeout_at(deadline):
                    decision = await self.policy.decide(
                        tool,
                        actor=scope,
                        resource=ToolResource.from_json(resource),
                        authorizer=self.authorizer,
                    )
                    if decision.decision != "deny":
                        if tx is None:
                            async with self.storage.transaction() as grant_tx:
                                current = await grant_tx.get_run(scope, run_id)
                                grant = await approval_status(grant_tx, current, operation)
                        else:
                            grant = await approval_status(tx, current_run, operation)
                        if grant == "expired":
                            raise InteractionExpired(
                                "Saved approval expired before local execution"
                            )
                        if grant == "granted":
                            return PermissionDecision(decision="allow", reason="approval")
                    return decision
            except (InteractionExpired, PersistenceUnavailable):
                raise
            except Exception:
                return PermissionDecision(decision="deny", reason="authorization_error")

        decision = await permission()
        if decision.decision == "ask":
            return ToolExecution(operation, decision)
        if decision.decision != "allow":
            return await reject(
                "authorization_error"
                if decision.reason == "authorization_error"
                else "permission_denied",
                decision,
            )
        memory_scope = MemoryScope(**scope.model_dump(), agent_name=run.agent_name)
        try:
            async with asyncio.timeout_at(deadline), self.storage.transaction() as tx:
                current = await tx.get_run(scope, run_id)
                self.validate(current)
                work.checkpoint()
                if current.state.status != "running" or current.state.cancel_requested:
                    raise asyncio.CancelledError
                # Recheck after waiting for the transaction gate, before touching memory.
                current_run = current
                final_decision = await permission(tx)
                work.checkpoint()
                if final_decision.decision != "allow":
                    raise _PermissionChanged(final_decision)
                result: object
                sources = []
                changed: MemoryRecord | None = None
                error: str | None = None
                if tool.name == "memory.recall":
                    records = await tx.recall_memory(
                        memory_scope,
                        key=cast(str | None, normalized["key"]),
                        query=cast(str | None, normalized["query"]),
                        limit=cast(int, normalized["limit"]),
                    )
                    result = [value(item) for item in records]
                    sources = [memory_source(item, "explicit_recall") for item in records]
                else:
                    key = cast(str, normalized["key"])
                    expected = cast(int, normalized["expected_version"])
                    previous_memory = await tx.get_memory(memory_scope, key)
                    if (previous_memory.version if previous_memory else 0) != expected or (
                        tool.name == "memory.forget"
                        and (previous_memory is None or previous_memory.deleted)
                    ):
                        error, result = "memory_conflict", None
                    else:
                        deleted = tool.name == "memory.forget"
                        changed = MemoryRecord(
                            scope=memory_scope,
                            key=key,
                            version=expected + 1,
                            content=None if deleted else cast(str, normalized["content"]),
                            deleted=deleted,
                            source=f"{run_id}:operation:{operation_id}",
                            updated_at=tx._now(),
                        )
                        result = (
                            Forgotten(key, changed.version, True)
                            if deleted
                            else Remembered(key, changed.version)
                        )
                encoded: StrictJson = None
                if error is None:
                    encoded = tool.result_codec.encode(result)
                    if (
                        len(canonical_json(encoded).encode("utf-8"))
                        > run.limits.max_tool_result_bytes
                    ):
                        error, changed, sources = "output_unavailable", None, []
                        encoded = None
                finished = operation.model_copy(
                    update={
                        "version": operation.version + 1,
                        "status": "succeeded" if error is None else "failed_known",
                        "has_result": error is None,
                        "result": encoded,
                        "error_code": error,
                        "attempts": 1,
                        "attempt_id": str(uuid4()),
                        "memory_sources": sources,
                    }
                )
                extra_events = []
                if changed is not None:
                    await tx.put_memory(changed, expected_version=changed.version - 1)
                    extra_events.append(
                        EventDraft(
                            type="memory.changed",
                            data={
                                "operation_id": operation_id,
                                "action": "forget" if changed.deleted else "remember",
                                "key": changed.key,
                                "version": changed.version,
                            },
                        )
                    )
                await journal.commit_in(
                    tx,
                    finished,
                    EventDraft(
                        type="tool.succeeded" if error is None else "tool.failed_known",
                        data={
                            "operation_id": operation_id,
                            "error_code": error,
                            **({"result": encoded} if error is None else {}),
                        },
                    ),
                    extra_events=extra_events,
                    consume_call=True,
                )
            return ToolExecution(finished, decision)
        except _PermissionChanged as exc:
            if exc.decision.decision == "ask":
                return ToolExecution(operation, exc.decision)
            return await reject(
                "permission_denied",
                exc.decision,
            )
        except TimeoutError:
            # Cancellation may race with a commit already settled by SQLite. Preserve that fact.
            saved = await journal.get(run_id, operation_id)
            if saved is not None and saved.status != "prepared":
                return ToolExecution(saved, decision)
            return await reject("memory_timeout", decision)


class _PermissionChanged(Exception):
    def __init__(self, decision: PermissionDecision) -> None:
        self.decision = decision
