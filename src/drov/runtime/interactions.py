"""Durable approval/input requests and atomic, owned continuation of saved plans."""

import asyncio
import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from pydantic import TypeAdapter

from drov._json import StrictJson, canonical_json
from drov.auth import ToolResource
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    InteractionConflict,
    InteractionExpired,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
)
from drov.storage.records import EventDraft, RunItem, RunRecord
from drov.storage.transaction import Transaction
from drov.tools.approvals import approval_status
from drov.tools.execution import ToolExecution
from drov.tools.human import HumanInput
from drov.tools.operations import OperationJournal, ToolOperation
from drov.tools.permissions import PermissionDecision
from drov.tools.schema import ToolContext
from drov.types import (
    ApprovalDecision,
    ApprovalRequest,
    InputRequest,
    InputResponse,
    InteractionRequest,
    InteractionResponse,
    ReconciliationDecision,
    ResponseReceipt,
    RunContext,
    RunError,
)

if TYPE_CHECKING:
    from drov.runtime.core import _Work
    from drov.runtime.runs import RunController


class InteractionController:
    def __init__(self, runs: "RunController") -> None:
        self.runs = runs

    async def expire(self, record: RunRecord) -> None:
        async with self.runs.storage.transaction() as tx:
            current = await tx.get_run(record.scope, record.state.run_id)
            await self.expire_in(tx, current)

    async def expire_in(self, tx: Transaction, record: RunRecord) -> bool:
        request = record.state.pending
        if request is None or request.kind == "reconciliation" or tx._now() < request.expires_at:
            return False
        await self.runs.finish_in(
            tx,
            record.state.run_id,
            record.scope,
            error=RunError(
                code="interaction_expired", message="Interaction expired", stage="interaction"
            ),
        )
        return True

    async def suspend(
        self,
        record: RunRecord,
        operation: ToolOperation,
        *,
        active_seconds: float,
        input_tool: HumanInput | None = None,
    ) -> None:
        runs = self.runs
        async with runs.storage.transaction() as tx:
            current = await tx.get_run(record.scope, record.state.run_id)
            if current.state.cancel_requested:
                raise asyncio.CancelledError
            runs.runtime.validate_record(current)
            if current.state.status != "running" or operation.status != "prepared":
                raise InteractionConflict("Run cannot create an interaction")
            checkpoint = await tx.get_checkpoint(record.scope, record.state.run_id)
            now = tx._now()
            common: dict[str, Any] = dict(
                run_id=record.state.run_id,
                operation_id=operation.operation_id,
                request_id=str(uuid4()),
                created_at=now,
                expires_at=now
                + timedelta(
                    seconds=current.limits.input_timeout_seconds
                    if input_tool
                    else current.limits.approval_timeout_seconds
                ),
            )
            request: InteractionRequest
            if input_tool is not None:
                request = InputRequest(
                    **common,
                    prompt=cast(str, operation.arguments["prompt"]),
                    input_schema=input_tool.result_codec.schema,
                )
            else:
                request = ApprovalRequest(
                    **common,
                    prompt="Approve this saved tool call",
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
                        "config_hash": current.config_hash,
                    },
                ),
            )
            version = current.state.state_version + 1
            await tx.save_step(
                record.scope,
                expected_version=version - 1,
                state=current.state.model_copy(
                    update={
                        "status": "suspended",
                        "reason": request.kind,
                        "pending": request,
                        "state_version": version,
                        "outcome": None,
                    }
                ),
                checkpoint=checkpoint.model_copy(
                    update={
                        "state_version": version,
                        "data": checkpoint.data | {"active_seconds": active_seconds},
                    }
                ),
                events=[
                    EventDraft(
                        type="interaction.requested",
                        data={"request": request.model_dump(mode="json")},
                    ),
                    EventDraft(type="run.suspended", data={"operation_id": operation.operation_id}),
                ],
            )

    async def respond(
        self,
        run_id: str,
        *,
        request_id: str,
        response: InteractionResponse,
        context: RunContext,
    ) -> ResponseReceipt:
        runs = self.runs
        runs.runtime._require_open()
        context = RunContext.from_json(context.model_dump_json())
        async with runs.storage.transaction() as tx:
            record = await tx._locate_run(context, run_id)
        await runs.authorize(context, runs.resource(record), ("respond",))
        response = TypeAdapter(InteractionResponse).validate_python(response)
        response = TypeAdapter(InteractionResponse).validate_json(response.model_dump_json())
        if isinstance(response, ReconciliationDecision):
            return await runs.reconciliation.respond(
                run_id, request_id=request_id, response=response, context=context
            )
        response_data = response.model_dump(mode="json")
        owner = runs.resource(record).owner

        async def apply() -> ResponseReceipt:
            slot = None
            expired = False
            receipt: ResponseReceipt | None = None
            try:
                async with runs.storage.transaction() as tx:
                    current = await tx.get_run(owner, run_id)
                    await runs.authorize(context, runs.resource(current), ("respond",))
                    try:
                        prior = await tx.get_item(owner, run_id, "receipt", request_id)
                    except RecordNotFound:
                        prior = None
                    if prior is not None:
                        if prior.data.get("schema_version") != 1 or prior.state != "accepted":
                            raise PersistenceUnavailable("Unsupported response receipt")
                        if canonical_json(prior.data["response"]) != canonical_json(response_data):
                            raise InteractionConflict("Request already has a different response")
                        return ResponseReceipt.model_validate(prior.data["receipt"])
                    request = current.state.pending
                    if (
                        current.state.cancel_requested
                        or current.state.status != "suspended"
                        or request is None
                        or request.request_id != request_id
                    ):
                        raise InteractionConflict("Request is no longer pending")
                    if response.kind != request.kind:
                        raise InteractionConflict("Response kind differs from the request")
                    expired = await self.expire_in(tx, current)
                    if not expired:
                        runs.runtime.validate_record(current)
                        item = await tx.get_item(owner, run_id, "interaction", request_id)
                        operation_item = await tx.get_item(
                            owner, run_id, "operation", request.operation_id
                        )
                        operation = ToolOperation.from_json(canonical_json(operation_item.data))
                        if (
                            item.state != "pending"
                            or item.data.get("schema_version") != 1
                            or canonical_json(item.data["request"])
                            != canonical_json(request.model_dump(mode="json"))
                            or item.data["request_hash"] != operation.request_hash
                            or item.data["config_hash"] != current.config_hash
                            or operation.status != "prepared"
                            or operation.version != operation_item.version
                            or operation_item.state != operation.status
                        ):
                            raise InteractionConflict("Request differs from its original operation")
                        tool = next(
                            (
                                tool
                                for tool in runs.runtime._agents[
                                    (current.agent_name, current.revision)
                                ]._execution_tools
                                if tool.name == operation.tool_name
                                and tool.revision == operation.tool_revision
                            ),
                            None,
                        )
                        if tool is None:
                            raise InteractionConflict("Original tool revision is unavailable")
                        result: StrictJson = None
                        if isinstance(response, InputResponse):
                            if (
                                not isinstance(tool, HumanInput)
                                or not isinstance(request, InputRequest)
                                or tool.result_codec.schema != request.input_schema
                            ):
                                raise InteractionConflict("Original input schema is unavailable")
                            async with asyncio.timeout(current.limits.verify_timeout_seconds):
                                decision = await runs.runtime.policy.decide(
                                    tool,
                                    actor=owner,
                                    authorizer=runs.runtime.authorizer,
                                    resource=ToolResource(
                                        owner=owner,
                                        run_id=run_id,
                                        session_id=current.scope.session_id,
                                        tool_name=tool.name,
                                        operation_id=operation.operation_id,
                                        arguments=operation.arguments,
                                    ),
                                )
                            if decision.decision == "deny":
                                raise PermissionDenied("Input permission was revoked")
                            result = tool.result_codec.encode(
                                tool.result_codec.decode(response.value)
                            )
                        elif isinstance(response, ApprovalDecision):
                            if (
                                not isinstance(request, ApprovalRequest)
                                or request.tool_name != tool.name
                                or canonical_json(request.arguments_summary)
                                != canonical_json(operation.arguments)
                            ):
                                raise InteractionConflict(
                                    "Approved parameters differ from the operation"
                                )
                        if (
                            len(canonical_json(response_data).encode("utf-8"))
                            > current.limits.max_tool_result_bytes
                        ):
                            raise ValueError("Interaction response exceeds the size limit")
                        checkpoint = await tx.get_checkpoint(owner, run_id)
                        data = dict(checkpoint.data)
                        summaries = list(current.state.operations)
                        events = []
                        if isinstance(response, InputResponse) or (
                            isinstance(response, ApprovalDecision) and not response.approved
                        ):
                            if isinstance(response, InputResponse):
                                count = data.get("tool_dispatch_count", 0)
                                if type(count) is not int or count < 0:
                                    raise PersistenceUnavailable("Invalid tool dispatch budget")
                                if count >= current.limits.max_tool_calls:
                                    raise CapacityExceeded("Run tool dispatch budget exhausted")
                                data["tool_dispatch_count"] = count + 1
                                operation = operation.model_copy(
                                    update={
                                        "version": operation.version + 1,
                                        "status": "succeeded",
                                        "result": result,
                                        "has_result": True,
                                        "attempts": 1,
                                        "attempt_id": str(uuid4()),
                                        "error_code": None,
                                    }
                                )
                            else:
                                operation = operation.model_copy(
                                    update={
                                        "version": operation.version + 1,
                                        "status": "not_executed",
                                        "error_code": "approval_denied",
                                    }
                                )
                            await tx.put_item(
                                owner,
                                operation_item.model_copy(
                                    update={
                                        "version": operation.version,
                                        "state": operation.status,
                                        "data": operation.model_dump(mode="json"),
                                    }
                                ),
                                expected_version=operation.version - 1,
                            )
                            summaries = [
                                summary
                                for summary in summaries
                                if summary.operation_id != operation.operation_id
                            ] + [operation.summary()]
                            events.append(
                                EventDraft(
                                    type="tool.succeeded"
                                    if isinstance(response, InputResponse)
                                    else "tool.denied",
                                    data={"operation_id": operation.operation_id, "result": result}
                                    if isinstance(response, InputResponse)
                                    else {
                                        "operation_id": operation.operation_id,
                                        "error_code": "approval_denied",
                                    },
                                )
                            )
                        else:
                            grants = data.get("approval_grants", {})
                            if not isinstance(grants, dict):
                                raise PersistenceUnavailable("Invalid approval grant index")
                            data["approval_grants"] = grants | {operation.operation_id: request_id}
                        # Reserve before consumption; failure rolls back every mutation.
                        slot = runs.runtime._reserve()
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
                                    "response": response_data,
                                    "actor": context.model_dump(mode="json"),
                                    "receipt": receipt.model_dump(mode="json"),
                                },
                            ),
                        )
                        await tx.put_item(
                            owner,
                            item.model_copy(
                                update={"version": item.version + 1, "state": "consumed"}
                            ),
                            expected_version=item.version,
                        )
                        await tx.save_step(
                            owner,
                            expected_version=version - 1,
                            state=current.state.model_copy(
                                update={
                                    "status": "running",
                                    "reason": None,
                                    "pending": None,
                                    "outcome": None,
                                    "state_version": version,
                                    "operations": summaries,
                                    "recoverable": False,
                                }
                            ),
                            checkpoint=checkpoint.model_copy(
                                update={"state_version": version, "data": data}
                            ),
                            events=[
                                EventDraft(
                                    type="interaction.responded",
                                    data={
                                        "request_id": request_id,
                                        "response_id": receipt.response_id,
                                        "response": response_data,
                                        "actor": context.model_dump(mode="json"),
                                    },
                                ),
                                *events,
                                EventDraft(type="run.running"),
                            ],
                        )
                if expired:
                    raise InteractionExpired("Interaction expired")
                assert slot is not None and receipt is not None
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

    async def execute(
        self,
        tool: HumanInput,
        arguments: str,
        *,
        work: "_Work[Any]",
        record: RunRecord,
        operation_id: str,
        deadline: float,
        continuation: bool = False,
    ) -> ToolExecution:
        runs = self.runs
        if work.task is not asyncio.current_task():
            raise ConfigurationError("HumanInput belongs to the kernel task")
        work.checkpoint()
        owner = runs.resource(record).owner
        runs.runtime.validate_record(record)
        invalid = False
        normalized: dict[str, StrictJson] = {}
        try:
            bound = tool.bind_arguments(
                arguments,
                context=ToolContext(
                    **owner.model_dump(),
                    run_id=record.state.run_id,
                    operation_id=operation_id,
                    deps={},
                ),
                max_bytes=record.limits.max_arguments_bytes,
            )
            normalized = {
                name: codec.encode(bound[name]) for name, codec in tool.argument_codecs.items()
            }
            request: object = normalized
        except (ValueError, TypeError):
            invalid = True
            request = {"invalid_input_hash": hashlib.sha256(arguments.encode("utf-8")).hexdigest()}
        request_hash = hashlib.sha256(
            canonical_json(
                {
                    "tool": tool.name,
                    "revision": tool.revision,
                    **owner.model_dump(),
                    "run_id": record.state.run_id,
                    "operation_id": operation_id,
                    "arguments": request,
                }
            ).encode("utf-8")
        ).hexdigest()
        journal = OperationJournal(runs.storage, owner)
        previous = await journal.get(record.state.run_id, operation_id)
        if previous is not None:
            if previous.request_hash != request_hash:
                raise InteractionConflict("Input operation parameters changed")
            if not continuation or previous.status != "prepared":
                return ToolExecution(previous)
        operation = previous or ToolOperation(
            run_id=record.state.run_id,
            operation_id=operation_id,
            tool_name=tool.name,
            tool_revision=tool.revision,
            effect="read",
            request_hash=request_hash,
            arguments=normalized,
            status="prepared",
        )
        if previous is None:
            await journal.commit(
                operation, EventDraft(type="tool.prepared", data={"operation_id": operation_id})
            )
        decision = PermissionDecision(decision="deny", reason="deny")
        if not invalid:
            try:
                async with asyncio.timeout_at(deadline):
                    decision = await runs.runtime.policy.decide(
                        tool,
                        actor=owner,
                        authorizer=runs.runtime.authorizer,
                        resource=ToolResource(
                            owner=owner,
                            session_id=record.scope.session_id,
                            run_id=record.state.run_id,
                            operation_id=operation_id,
                            tool_name=tool.name,
                            arguments=normalized,
                        ),
                    )
            except Exception:
                decision = PermissionDecision(decision="deny", reason="authorization_error")
            if decision.decision != "deny":
                async with runs.storage.transaction() as tx:
                    current = await tx.get_run(owner, record.state.run_id)
                    status = await approval_status(tx, current, operation)
                if status == "expired":
                    raise InteractionExpired("Saved approval expired")
                if status == "granted":
                    decision = PermissionDecision(decision="allow", reason="approval")
        if invalid or decision.decision == "deny":
            operation = operation.model_copy(
                update={
                    "version": operation.version + 1,
                    "status": "not_executed",
                    "error_code": "invalid_arguments" if invalid else "permission_denied",
                }
            )
            await journal.commit(
                operation, EventDraft(type="tool.not_executed", data={"operation_id": operation_id})
            )
        return ToolExecution(operation, decision)
