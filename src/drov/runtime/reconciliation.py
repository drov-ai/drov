"""Ambiguous external effects remain durable until authorized evidence resolves them."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from drov._json import StrictJson, canonical_json
from drov.auth import ToolResource
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    InteractionConflict,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
)
from drov.storage.records import EventDraft, RunItem, RunRecord
from drov.storage.transaction import Transaction
from drov.tools.operations import ToolOperation
from drov.tools.python import PythonTool
from drov.tools.schema import ToolContext
from drov.types import (
    ReconciliationDecision,
    ReconciliationRequest,
    ResponseReceipt,
    RunContext,
    RunError,
)

if TYPE_CHECKING:
    from drov.runtime.core import _Work
    from drov.runtime.runs import RunController


def ambiguous(record: RunRecord) -> bool:
    return any(
        op.effect != "read" and op.status in ("dispatched", "unknown")
        for op in record.state.operations
    )


async def operation_in(tx: Transaction, record: RunRecord, operation_id: str) -> ToolOperation:
    item = await tx.get_item(record.scope, record.state.run_id, "operation", operation_id)
    operation = ToolOperation.from_json(canonical_json(item.data))
    if (operation.run_id, operation.operation_id, operation.version, operation.status) != (
        item.run_id,
        item.item_id,
        item.version,
        item.state,
    ):
        raise PersistenceUnavailable("Operation payload differs from its index")
    return operation


async def put_operation(tx: Transaction, record: RunRecord, operation: ToolOperation) -> None:
    await tx.put_item(
        record.scope,
        RunItem(
            run_id=operation.run_id,
            kind="operation",
            item_id=operation.operation_id,
            version=operation.version,
            state=operation.status,
            data=operation.model_dump(mode="json"),
        ),
        expected_version=operation.version - 1,
    )


class ReconciliationController:
    def __init__(self, runs: "RunController") -> None:
        self.runs = runs
        self.verifying: dict[tuple[str, str], _Work[ReconciliationDecision]] = {}

    def live_calls(self, run_id: str) -> bool:
        return any(
            work.run_id == run_id
            and work.kind == "agent"
            and any(not call.done() for call in work._calls)
            for work in self.runs.runtime._works
        )

    def stopping_error(self, record: RunRecord, data: dict[str, StrictJson]) -> RunError | None:
        saved = data.get("stop_error")
        if saved is not None:
            return RunError.model_validate(saved)
        if record.limits.expires_at is not None and datetime.now(UTC) >= record.limits.expires_at:
            return RunError(
                code="deadline_exceeded", message="Run deadline exceeded", stage="runtime"
            )
        seconds = data.get("active_seconds", 0)
        if type(seconds) not in (int, float) or cast(Any, seconds) < 0:
            raise PersistenceUnavailable("Invalid active execution budget")
        if cast(Any, seconds) >= record.limits.run_active_seconds:
            return RunError(
                code="limit_exceeded", message="Run active budget exhausted", stage="runtime"
            )
        return None

    async def request_in(
        self,
        tx: Transaction,
        record: RunRecord,
        operation: ToolOperation,
    ) -> ReconciliationRequest:
        now = tx._now()
        request = ReconciliationRequest(
            request_id=str(uuid4()),
            run_id=record.state.run_id,
            operation_id=operation.operation_id,
            created_at=now,
            expires_at=now + timedelta(seconds=record.limits.reconciliation_reminder_seconds),
            prompt="Verify the effect of this original operation",
            explanation=operation.error_code or "external_effect_unknown",
            tool_name=operation.tool_name,
            arguments_summary=operation.arguments,
        )
        await tx.put_item(
            record.scope,
            RunItem(
                run_id=record.state.run_id,
                kind="interaction",
                item_id=request.request_id,
                state="pending",
                data={
                    "schema_version": 1,
                    "request": request.model_dump(mode="json"),
                    "request_hash": operation.request_hash,
                    "config_hash": record.config_hash,
                },
            ),
        )
        return request

    async def suspend_in(
        self,
        tx: Transaction,
        record: RunRecord,
        *,
        error: RunError | None = None,
        cancelled: bool = False,
        active_seconds: float | None = None,
    ) -> None:
        summary = next(
            op
            for op in record.state.operations
            if op.effect != "read" and op.status in ("dispatched", "unknown")
        )
        operation = await operation_in(tx, record, summary.operation_id)
        events = []
        if operation.status == "dispatched":
            operation = operation.model_copy(
                update={
                    "version": operation.version + 1,
                    "status": "unknown",
                    "error_code": "recovery_effect_unknown",
                }
            )
            await put_operation(tx, record, operation)
            events.append(
                EventDraft(type="tool.unknown", data={"operation_id": operation.operation_id})
            )
        checkpoint = await tx.get_checkpoint(record.scope, record.state.run_id)
        data = dict(checkpoint.data)
        if error is not None:
            data["stop_error"] = error.model_dump(mode="json")
        if active_seconds is not None:
            # A call still alive retains its reserved time as well as the actual resource.
            data["active_seconds"] = (
                max(cast(float, data.get("active_seconds", 0)), active_seconds)
                if self.live_calls(record.state.run_id)
                else active_seconds
            )
        pending = record.state.pending
        if (
            not isinstance(pending, ReconciliationRequest)
            or pending.operation_id != operation.operation_id
        ):
            pending = await self.request_in(tx, record, operation)
            events.append(
                EventDraft(
                    type="interaction.requested", data={"request": pending.model_dump(mode="json")}
                )
            )
        version = record.state.state_version + 1
        await tx.save_step(
            record.scope,
            expected_version=version - 1,
            state=record.state.model_copy(
                update={
                    "status": "suspended",
                    "reason": "reconciliation",
                    "pending": pending,
                    "outcome": None,
                    "state_version": version,
                    "cancel_requested": record.state.cancel_requested or cancelled,
                    "operations": [
                        op
                        for op in record.state.operations
                        if op.operation_id != operation.operation_id
                    ]
                    + [operation.summary()],
                }
            ),
            checkpoint=checkpoint.model_copy(update={"state_version": version, "data": data}),
            events=[
                *events,
                EventDraft(type="run.suspended", data={"operation_id": operation.operation_id}),
            ],
        )

    async def suspend(self, run_id: str, owner: RunContext, *, active_seconds: float) -> None:
        async with self.runs.storage.transaction() as tx:
            record = await tx.get_run(owner, run_id)
            await self.suspend_in(tx, record, active_seconds=active_seconds)

    async def pending_in(
        self,
        tx: Transaction,
        owner: RunContext,
        run_id: str,
        request_id: str,
    ) -> tuple[RunRecord, ReconciliationRequest, RunItem, ToolOperation]:
        record = await tx.get_run(owner, run_id)
        request = record.state.pending
        if (
            record.state.status != "suspended"
            or not isinstance(request, ReconciliationRequest)
            or request.request_id != request_id
        ):
            raise InteractionConflict("Reconciliation request is no longer pending")
        item = await tx.get_item(owner, run_id, "interaction", request_id)
        operation = await operation_in(tx, record, request.operation_id)
        if (
            item.state != "pending"
            or type(item.data.get("schema_version")) is not int
            or item.data.get("schema_version") != 1
            or canonical_json(item.data["request"])
            != canonical_json(request.model_dump(mode="json"))
            or item.data["config_hash"] != record.config_hash
            or item.data["request_hash"] != operation.request_hash
            or canonical_json(request.arguments_summary) != canonical_json(operation.arguments)
            or request.tool_name != operation.tool_name
            or operation.effect == "read"
            or operation.status not in ("dispatched", "unknown")
        ):
            raise InteractionConflict("Reconciliation differs from the original operation")
        return record, request, item, operation

    def original_tool(self, record: RunRecord, operation: ToolOperation) -> PythonTool:
        self.runs.runtime.validate_record(record)
        tools = self.runs.runtime._agents[(record.agent_name, record.revision)].tools
        tool = next(
            (
                tool
                for tool in tools
                if tool.name == operation.tool_name and tool.revision == operation.tool_revision
            ),
            None,
        )
        if not isinstance(tool, PythonTool):
            raise ConfigurationError("Original executable tool is unavailable")
        return tool

    async def respond(
        self,
        run_id: str,
        *,
        request_id: str,
        response: ReconciliationDecision,
        context: RunContext,
    ) -> ResponseReceipt:
        runs = self.runs
        async with runs.storage.transaction() as tx:
            record = await tx._locate_run(context, run_id)
        await runs.authorize(context, runs.resource(record), ("respond",))
        owner = runs.resource(record).owner
        payload = response.model_dump(mode="json")
        if (
            not response.evidence.strip()
            or len(canonical_json(payload).encode("utf-8")) > record.limits.max_tool_result_bytes
        ):
            raise ValueError("Evidence must be non-empty and within the response limit")

        async def apply() -> ResponseReceipt:
            slot = None
            try:
                async with runs.storage.transaction() as tx:
                    current = await tx.get_run(owner, run_id)
                    await runs.authorize(context, runs.resource(current), ("respond",))
                    try:
                        prior = await tx.get_item(owner, run_id, "receipt", request_id)
                    except RecordNotFound:
                        prior = None
                    if prior is not None:
                        if (
                            type(prior.data.get("schema_version")) is not int
                            or prior.data.get("schema_version") != 1
                            or prior.state != "accepted"
                        ):
                            raise PersistenceUnavailable("Unsupported response receipt")
                        if canonical_json(prior.data["response"]) != canonical_json(payload):
                            raise InteractionConflict("Request already has a different response")
                        return ResponseReceipt.model_validate(prior.data["receipt"])
                    current, request, item, operation = await self.pending_in(
                        tx, owner, run_id, request_id
                    )
                    if operation.tool_name == "sandbox.run_python":
                        raise InteractionConflict(
                            "Sandbox output/cleanup must be resolved through recover; "
                            "human evidence cannot replace original execution facts"
                        )
                    checkpoint = await tx.get_checkpoint(owner, run_id)
                    data = dict(checkpoint.data)
                    stop = self.stopping_error(current, data)
                    terminal = response.resolution != "unresolved" and (
                        current.state.cancel_requested or stop is not None
                    )
                    result: StrictJson = None
                    if response.resolution == "not_applied" and self.live_calls(run_id):
                        raise InteractionConflict(
                            "Original local call is still active; cannot redispatch"
                        )
                    if response.resolution == "applied":
                        if not terminal or response.has_result:
                            tool = self.original_tool(current, operation)
                            if not response.has_result:
                                raise ValueError("Continuing requires a verified tool result")
                            result = tool.result_codec.encode(
                                tool.result_codec.decode(response.result)
                            )
                            if (
                                len(canonical_json(result).encode("utf-8"))
                                > current.limits.max_tool_result_bytes
                            ):
                                raise ValueError("Verified result exceeds the limit")
                        operation = operation.model_copy(
                            update={
                                "version": operation.version + 1,
                                "status": "succeeded" if response.has_result else "failed_known",
                                "has_result": response.has_result,
                                "result": result,
                                "error_code": None if response.has_result else "output_unavailable",
                                "effect_resolution": "applied",
                                "evidence": response.evidence,
                            }
                        )
                    elif response.resolution == "not_applied":
                        if not terminal:
                            self.original_tool(current, operation)
                        operation = operation.model_copy(
                            update={
                                "version": operation.version + 1,
                                "status": "not_executed" if terminal else "prepared",
                                "error_code": None,
                                "effect_resolution": "not_applied",
                                "evidence": response.evidence,
                            }
                        )
                        grants = data.get("approval_grants", {})
                        if not isinstance(grants, dict):
                            raise PersistenceUnavailable("Invalid approval grant index")
                        data["approval_grants"] = {
                            k: v for k, v in grants.items() if k != operation.operation_id
                        }
                    else:
                        operation = operation.model_copy(
                            update={
                                "version": operation.version + 1,
                                "status": "unknown",
                                "evidence": response.evidence,
                            }
                        )
                    if not terminal and response.resolution != "unresolved":
                        self.runs.runtime.validate_record(current)
                        slot = runs.runtime._reserve()
                    await put_operation(tx, current, operation)
                    summaries = [
                        op
                        for op in current.state.operations
                        if op.operation_id != operation.operation_id
                    ] + [operation.summary()]
                    version = current.state.state_version + 1
                    receipt = ResponseReceipt(
                        run_id=run_id,
                        request_id=request_id,
                        response_id=str(uuid4()),
                        accepted_at=tx._now(),
                        state_version=version,
                    )
                    await tx.put_item(
                        owner,
                        RunItem(
                            run_id=run_id,
                            kind="receipt",
                            item_id=request_id,
                            state="accepted",
                            data={
                                "schema_version": 1,
                                "response": payload,
                                "actor": context.model_dump(mode="json"),
                                "receipt": receipt.model_dump(mode="json"),
                            },
                        ),
                    )
                    await tx.put_item(
                        owner,
                        item.model_copy(update={"version": item.version + 1, "state": "consumed"}),
                        expected_version=item.version,
                    )
                    events = [
                        EventDraft(
                            type="interaction.responded",
                            data={
                                "request_id": request_id,
                                "response_id": receipt.response_id,
                                "response": payload,
                                "actor": context.model_dump(mode="json"),
                            },
                        )
                    ]
                    if terminal:
                        await runs.finish_in(
                            tx,
                            run_id,
                            owner,
                            error=stop,
                            cancelled=current.state.cancel_requested,
                            operations=summaries,
                            extra_events=events,
                        )
                    else:
                        pending = (
                            await self.request_in(tx, current, operation)
                            if response.resolution == "unresolved"
                            else None
                        )
                        if pending is not None:
                            events.append(
                                EventDraft(
                                    type="interaction.requested",
                                    data={"request": pending.model_dump(mode="json")},
                                )
                            )
                        else:
                            events.append(
                                EventDraft(
                                    type="tool.succeeded"
                                    if operation.has_result
                                    else "tool.prepared",
                                    data={
                                        "operation_id": operation.operation_id,
                                        **({"result": result} if operation.has_result else {}),
                                    },
                                )
                            )
                        events.append(
                            EventDraft(
                                type="run.suspended" if pending is not None else "run.running"
                            )
                        )
                        await tx.save_step(
                            owner,
                            expected_version=version - 1,
                            state=current.state.model_copy(
                                update={
                                    "state_version": version,
                                    "status": "suspended" if pending is not None else "running",
                                    "pending": pending,
                                    "reason": "reconciliation" if pending is not None else None,
                                    "outcome": None,
                                    "operations": summaries,
                                }
                            ),
                            checkpoint=checkpoint.model_copy(
                                update={"state_version": version, "data": data}
                            ),
                            events=events,
                        )
                if slot is not None:
                    try:
                        work = runs.runtime._spawn(
                            slot, lambda work: runs.execute(run_id, owner, work), check_cancel=False
                        )
                    except Exception:
                        await runs.suspend_recovery(run_id, owner)
                        raise
                    runs.attach(run_id, work)
                return receipt
            finally:
                if slot is not None and not slot.claimed:
                    slot.release()

        return await runs.control(apply)

    async def verify(
        self, run_id: str, *, request_id: str, context: RunContext
    ) -> ReconciliationDecision:
        runs = self.runs
        record = await runs.visible(run_id, context, True)
        await runs.authorize(context, runs.resource(record), ("respond", "verify"))
        owner = runs.resource(record).owner
        key: tuple[str, str]

        async with runs.storage.transaction() as tx:
            current, _, _, operation = await self.pending_in(tx, owner, run_id, request_id)
        if operation.tool_name == "sandbox.run_python":
            runs.runtime.validate_record(current)
            controller = runs.runtime._sandboxes
            if controller is None:
                raise ConfigurationError("Original sandbox backend is not configured")
            key = (run_id, operation.operation_id)
            prior = self.verifying.get(key)
            if prior is not None and not prior.task.done():
                raise CapacityExceeded("This operation has an active verifier")
            slot = runs.runtime._reserve()

            async def observe(work: "_Work[ReconciliationDecision]") -> ReconciliationDecision:
                result = await controller.verify(current, operation.operation_id)
                async with runs.storage.transaction() as tx:
                    latest, _, _, _ = await self.pending_in(tx, owner, run_id, request_id)
                    await runs.authorize(
                        context,
                        runs.resource(latest),
                        ("read", "view_content", "respond", "verify"),
                    )
                return result

            work = runs.runtime._spawn(slot, observe)
            work.kind = "verify"
            work.run_id = run_id
            self.verifying[key] = work
            work.task.add_done_callback(lambda _: self.verifying.pop(key, None))
            return await work.wait()

        async def schedule() -> "_Work[ReconciliationDecision]":
            nonlocal key
            slot = None
            try:
                async with runs.storage.transaction() as tx:
                    current, _, _, operation = await self.pending_in(tx, owner, run_id, request_id)
                    await runs.authorize(
                        context,
                        runs.resource(current),
                        ("read", "view_content", "respond", "verify"),
                    )
                    tool = self.original_tool(current, operation)
                    if tool.verifier is None:
                        raise ConfigurationError("Original tool has no registered verifier")
                    key = (run_id, operation.operation_id)
                    prior = self.verifying.get(key)
                    if prior is not None and (
                        not prior.task.done() or any(not call.done() for call in prior._calls)
                    ):
                        raise CapacityExceeded("This operation has an active verifier")
                    async with asyncio.timeout(current.limits.verify_timeout_seconds):
                        decision = await runs.runtime.policy.decide(
                            tool,
                            actor=owner,
                            resource=ToolResource(
                                owner=owner,
                                run_id=run_id,
                                session_id=current.scope.session_id,
                                tool_name=operation.tool_name,
                                operation_id=operation.operation_id,
                                arguments=operation.arguments,
                            ),
                            authorizer=runs.runtime.authorizer,
                        )
                    if decision.decision == "deny":
                        raise PermissionDenied("Original user's query permission is unavailable")
                    slot = runs.runtime._reserve()
                    verification_id = str(uuid4())
                    await self.audit_in(
                        tx,
                        current,
                        EventDraft(
                            type="verification.started",
                            data={
                                "verification_id": verification_id,
                                "operation_id": operation.operation_id,
                                "request_id": request_id,
                            },
                        ),
                    )
                assert slot is not None
                work = runs.runtime._spawn(
                    slot,
                    lambda work: self.query(
                        work, current, tool, operation, request_id, verification_id, context
                    ),
                    check_cancel=False,
                )
                work.kind, work.run_id = "verify", run_id
                self.verifying[key] = work
                return work
            finally:
                if slot is not None and not slot.claimed:
                    slot.release()

        work = await runs.control(schedule)
        result = await work.wait()
        # A late suggestion is not usable for a consumed/replaced request.
        current = await runs.visible(run_id, context, True)
        await runs.authorize(context, runs.resource(current), ("respond", "verify"))
        async with runs.storage.transaction() as tx:
            await self.pending_in(tx, owner, run_id, request_id)
        return result

    async def audit_in(self, tx: Transaction, record: RunRecord, event: EventDraft) -> None:
        verification_id = cast(str, event.data["verification_id"])
        complete = event.type == "verification.completed"
        await tx.put_item(
            record.scope,
            RunItem(
                run_id=record.state.run_id,
                kind="receipt",
                item_id=f"verification:{verification_id}:{'completed' if complete else 'started'}",
                state="completed" if complete else "started",
                data={"schema_version": 1, "record_type": "verification", **event.data},
            ),
        )
        if record.state.status in ("completed", "failed", "cancelled"):
            return  # Audit may finish later; terminal run/outcome versions stay immutable.
        checkpoint = await tx.get_checkpoint(record.scope, record.state.run_id)
        version = record.state.state_version + 1
        await tx.save_step(
            record.scope,
            expected_version=version - 1,
            state=record.state.model_copy(update={"state_version": version}),
            checkpoint=checkpoint.model_copy(update={"state_version": version}),
            events=[event],
        )

    async def query(
        self,
        work: "_Work[ReconciliationDecision]",
        record: RunRecord,
        tool: PythonTool,
        operation: ToolOperation,
        request_id: str,
        verification_id: str,
        actor: RunContext,
    ) -> ReconciliationDecision:
        from drov.runtime.core import _owned_task

        assert tool.verifier is not None
        work.checkpoint()
        owner = self.runs.resource(record).owner
        callback = tool.verifier

        async def invoke() -> ReconciliationDecision:
            work.checkpoint()
            async with self.runs.storage.transaction() as tx:
                current, _, _, saved = await self.pending_in(
                    tx, owner, record.state.run_id, request_id
                )
            self.original_tool(current, saved)
            await self.runs.authorize(
                actor, self.runs.resource(current), ("read", "view_content", "respond", "verify")
            )
            decision = await self.runs.runtime.policy.decide(
                tool,
                actor=owner,
                resource=ToolResource(
                    owner=owner,
                    run_id=record.state.run_id,
                    session_id=record.scope.session_id,
                    tool_name=operation.tool_name,
                    operation_id=operation.operation_id,
                    arguments=operation.arguments,
                ),
                authorizer=self.runs.runtime.authorizer,
            )
            if decision.decision == "deny":
                raise PermissionDenied("Query permission changed")
            work.checkpoint()
            return await callback(
                ToolContext(
                    **owner.model_dump(),
                    run_id=record.state.run_id,
                    operation_id=operation.operation_id,
                    deps=self.runs.runtime._dependencies,
                )
            )

        call = _owned_task(invoke, name="drov-verifier-call")
        work.track(call)
        key = (record.state.run_id, operation.operation_id)

        def clear() -> None:
            if work.task.done() and all(future.done() for future in work._calls):
                if self.verifying.get(key) is work:
                    self.verifying.pop(key, None)

        def changed(future: Any) -> None:
            asyncio.get_running_loop().call_soon(clear)

        call.add_done_callback(changed)
        work.task.add_done_callback(changed)
        error = None
        try:
            done, _ = await asyncio.wait({call}, timeout=record.limits.verify_timeout_seconds)
            if not done:
                call.cancel()
                result = ReconciliationDecision(
                    resolution="unresolved", evidence="Verifier timed out; effect remains unknown"
                )
                error = "verification_timeout"
            else:
                result = ReconciliationDecision.from_json(call.result().model_dump_json())
                if (
                    not result.evidence.strip()
                    or len(result.model_dump_json().encode("utf-8"))
                    > record.limits.max_tool_result_bytes
                ):
                    raise ValueError("Invalid verification evidence")
                if result.has_result:
                    tool.result_codec.decode(result.result)
        except (Exception, asyncio.CancelledError):
            call.cancel()
            result = ReconciliationDecision(
                resolution="unresolved", evidence="Verifier failed; effect remains unknown"
            )
            error = "verification_failed"
        async with self.runs.storage.transaction() as tx:
            current = await tx.get_run(owner, record.state.run_id)
            await self.audit_in(
                tx,
                current,
                EventDraft(
                    type="verification.completed",
                    data={
                        "verification_id": verification_id,
                        "request_id": request_id,
                        "operation_id": operation.operation_id,
                        "resolution": result.resolution,
                        "decision": result.model_dump(mode="json"),
                        "actor": actor.model_dump(mode="json"),
                        "error_code": error,
                    },
                ),
            )
        return result
