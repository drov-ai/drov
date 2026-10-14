"""Runtime-only permission, lease, execution and atomic output publication."""

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from drov._json import StrictJson, canonical_json
from drov.artifacts.records import ArtifactRecord
from drov.artifacts.transaction import ArtifactTransaction
from drov.artifacts.types import ArtifactInput, file_path
from drov.auth import ToolResource
from drov.errors import (
    ArtifactUnavailable,
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    InteractionExpired,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
    ShutdownIncomplete,
)
from drov.sandbox._backend import DockerBackend
from drov.sandbox._docker import _settle
from drov.sandbox._leases import LeaseTransaction, SandboxLease
from drov.sandbox.tools import PythonSandbox
from drov.sandbox.types import SandboxArtifactError, SandboxResult
from drov.storage.records import EventDraft, MemoryScope, RunRecord
from drov.tools.approvals import approval_status
from drov.tools.execution import ToolExecution
from drov.tools.operations import OperationJournal, ToolOperation
from drov.tools.permissions import PermissionDecision
from drov.tools.schema import ToolContext
from drov.types import ReconciliationDecision, RunContext

if TYPE_CHECKING:
    from drov.agent import Agent
    from drov.runtime.core import Runtime, _Work
    from drov.storage.transaction import Transaction


class SandboxController:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime

    def backend(self, tool: PythonSandbox) -> DockerBackend:
        backend = self.runtime._dependencies.get(tool.backend)
        if not isinstance(backend, DockerBackend) or not backend.is_open:
            raise ConfigurationError("PythonSandbox requires an open configured Docker backend")
        return backend

    def bindings(self, agent: "Agent[Any]") -> dict[str, str]:
        return {
            tool.backend: self.backend(tool).config_hash
            for tool in agent.tools
            if isinstance(tool, PythonSandbox)
        }

    async def precheck(self, agent: "Agent[Any]") -> None:
        for tool in agent.tools:
            if isinstance(tool, PythonSandbox):
                await self.backend(tool).revalidate()

    async def aclose(self) -> None:
        storage = self.runtime._storage
        if storage is not None:
            async with storage.transaction() as tx:
                if await LeaseTransaction(tx).active_count():
                    raise ShutdownIncomplete("Unconfirmed sandbox leases retain storage ownership")

    async def verify(self, record: RunRecord, operation_id: str) -> ReconciliationDecision:
        owner = MemoryScope(
            tenant_id=record.scope.tenant_id,
            user_id=record.scope.user_id,
            agent_name=record.agent_name,
        )
        async with self.runtime.storage.transaction() as tx:
            lease = await LeaseTransaction(tx).get(owner, record.state.run_id, operation_id)
        if lease is None:
            raise ConfigurationError("Original sandbox lease is missing")
        agent = self.runtime._agents[(record.agent_name, record.revision)]
        tool = next(item for item in agent.tools if isinstance(item, PythonSandbox))
        async with asyncio.timeout(record.limits.verify_timeout_seconds):
            resource = await self.backend(tool).resource(lease)
        observed = "present" if resource is not None else "absent"
        return ReconciliationDecision(
            resolution="unresolved",
            evidence="Original sandbox environment is "
            + observed
            + "; recover must confirm cleanup and original output. "
            "Verification does not execute or destroy code.",
        )

    async def execute(
        self,
        tool: PythonSandbox,
        arguments: str,
        *,
        work: "_Work[Any]",
        record: RunRecord,
        operation_id: str,
        deadline: float | None = None,
        continuation: bool = False,
    ) -> ToolExecution:
        if work.task is not asyncio.current_task():
            raise ConfigurationError("Sandbox dispatch belongs to the kernel task")
        work.checkpoint()
        storage = self.runtime.storage
        scope = RunContext(tenant_id=record.scope.tenant_id, user_id=record.scope.user_id)
        owner = MemoryScope(**scope.model_dump(), agent_name=record.agent_name)
        backend, store = self.backend(tool), self.runtime._files.store()
        run = await storage.get_run(scope, record.state.run_id)
        self.runtime.validate_record(run)
        journal = OperationJournal(storage, scope)
        previous = await journal.get(run.state.run_id, operation_id)
        if previous is not None and previous.status != "prepared":
            return ToolExecution(previous)
        code = ""
        error = None
        inputs: list[ArtifactInput] = []
        grants = []
        normalized: dict[str, StrictJson] = {}
        raw_normalized: dict[str, Any] = {}
        try:
            bound = tool.bind_arguments(
                arguments,
                context=ToolContext(
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    run_id=run.state.run_id,
                    operation_id=operation_id,
                    deps=self.runtime._dependencies,
                ),
                max_bytes=run.limits.max_arguments_bytes,
            )
            code = cast(str, bound["code"])
            if (
                not code
                or "\0" in code
                or len(code.encode()) > backend.profile.limits.max_code_bytes
            ):
                raise ValueError("invalid_code")
            ids = cast(list[str], bound["inputs"])
            outputs = [file_path(name) for name in cast(list[str], bound["outputs"])]
            if len(set(ids)) != len(ids) or len(ids) > store.limits.max_input_files:
                raise ValueError("invalid_inputs")
            if len(outputs) > store.limits.max_output_files or len(
                {name.split("/")[-1] for name in outputs}
            ) != len(outputs):
                raise ValueError("invalid_outputs")
            raw_normalized = {"code": code, "inputs": ids, "outputs": outputs}
            async with storage.transaction() as tx:
                available = {
                    grant.ref.artifact_id: grant
                    for grant in await self.runtime._files.validate(
                        run, actor=scope, tx=tx, check_bytes=False
                    )
                }
                grants = [available[identifier] for identifier in ids]
                for grant in grants:
                    original = await ArtifactTransaction(tx).locate(scope, grant.ref.artifact_id)
                    try:
                        data = await self.runtime._files.load_bytes(original)
                    except (ConfigurationError, PersistenceUnavailable) as exc:
                        raise ArtifactUnavailable("Approved input bytes are unavailable") from exc
                    await self.runtime._files.authorize(
                        scope, self.runtime._files.resource(original, target=run), content=True
                    )
                    inputs.append(
                        ArtifactInput(name=grant.name, data=data, media_type=grant.ref.media_type)
                    )
            if sum(len(item.data) for item in inputs) > min(
                store.limits.max_input_bytes, 15 * 1024**2
            ):
                raise ValueError("input_limit")
            normalized = {
                "code": code,
                "inputs": cast(StrictJson, ids),
                "outputs": cast(StrictJson, outputs),
                "_resolved_inputs": [grant.model_dump(mode="json") for grant in grants],
                "_backend_hash": backend.config_hash,
            }
        except (ArtifactUnavailable, PermissionDenied, RecordNotFound):
            error = "artifact_unavailable"
            if previous is not None and canonical_json(raw_normalized) == canonical_json(
                {key: previous.arguments[key] for key in ("code", "inputs", "outputs")}
            ):
                normalized = previous.arguments
            else:
                normalized = {"invalid_input_hash": hashlib.sha256(arguments.encode()).hexdigest()}
        except (ValueError, TypeError, KeyError, ConfigurationError):
            error = "invalid_arguments"
            normalized = {"invalid_input_hash": hashlib.sha256(arguments.encode()).hexdigest()}
        request_hash = hashlib.sha256(
            canonical_json(
                {
                    "tool": tool.name,
                    "revision": tool.revision,
                    "scope": owner.model_dump(mode="json"),
                    "run_id": run.state.run_id,
                    "operation_id": operation_id,
                    "arguments": normalized,
                }
            ).encode()
        ).hexdigest()
        if previous is not None and previous.request_hash != request_hash:
            raise Conflict("Approved sandbox plan changed")
        operation = previous or ToolOperation(
            run_id=run.state.run_id,
            operation_id=operation_id,
            tool_name=tool.name,
            tool_revision=tool.revision,
            effect="write",
            request_hash=request_hash,
            arguments=normalized,
            status="prepared",
        )
        async with storage.transaction() as tx:
            lease = await LeaseTransaction(tx).get(owner, run.state.run_id, operation_id)
        if previous is None:
            async with storage.transaction() as tx:
                await journal.commit_in(
                    tx,
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
                if error is None:
                    lease = SandboxLease(
                        execution_key=uuid4().hex,
                        owner=owner,
                        run_id=run.state.run_id,
                        operation_id=operation_id,
                        backend_name=tool.backend,
                        backend_hash=backend.config_hash,
                        backend_config=cast(dict[str, StrictJson], backend._binding),
                        code_hash=hashlib.sha256(code.encode()).hexdigest(),
                        inputs=grants,
                        outputs=cast(list[str], normalized["outputs"]),
                    )
                    await LeaseTransaction(tx).put(lease)

        async def save(
            status: str, code: str | None, result: SandboxResult | None = None
        ) -> ToolOperation:
            nonlocal operation
            operation = ToolOperation.model_validate(
                operation.model_dump()
                | {
                    "version": operation.version + 1,
                    "status": status,
                    "error_code": code,
                    "has_result": result is not None,
                    "result": result.model_dump(mode="json") if result is not None else None,
                }
            )
            await journal.commit(
                operation,
                EventDraft(
                    type=cast(Any, "tool." + status),
                    data={"operation_id": operation_id, "error_code": code},
                ),
            )
            return operation

        if error is not None:
            return ToolExecution(await save("not_executed", error))
        assert lease is not None
        resource = ToolResource(
            owner=scope,
            run_id=run.state.run_id,
            session_id=run.scope.session_id,
            tool_name=tool.name,
            operation_id=operation_id,
            arguments=normalized,
        )

        async def permission() -> PermissionDecision:
            current = await storage.get_run(scope, run.state.run_id)
            self.runtime.validate_record(current)
            if current.state.cancel_requested or current.state.status != "running":
                raise asyncio.CancelledError
            try:
                seconds = min(
                    run.limits.tool_timeout_seconds,
                    max(0, deadline - asyncio.get_running_loop().time())
                    if deadline is not None
                    else run.limits.tool_timeout_seconds,
                )
                async with asyncio.timeout(seconds):
                    decision = await self.runtime.policy.decide(
                        tool,
                        actor=scope,
                        resource=ToolResource.from_json(resource.model_dump_json()),
                        authorizer=self.runtime.authorizer,
                    )
            except Exception:
                return PermissionDecision(decision="deny", reason="authorization_error")
            if decision.decision == "deny":
                return decision
            async with storage.transaction() as tx:
                grant = await approval_status(tx, current, operation)
                await self.runtime._files.validate(current, actor=scope, tx=tx)
            if grant == "expired":
                raise InteractionExpired("Sandbox approval expired")
            if decision.decision == "ask" and grant == "granted":
                return PermissionDecision(decision="allow", reason="approval")
            return decision

        decision = await permission()
        if decision.decision == "deny":
            return ToolExecution(await save("not_executed", "permission_denied"), decision)
        if decision.decision == "ask":
            return ToolExecution(operation, decision)
        await backend.revalidate()
        work.checkpoint()
        decision = await permission()
        if decision.decision != "allow":
            return ToolExecution(operation, decision)
        loop = asyncio.get_running_loop()
        end = min(
            deadline if deadline is not None else loop.time() + run.limits.tool_timeout_seconds,
            loop.time() + backend.profile.limits.execution_seconds,
        )
        if end <= loop.time():
            return ToolExecution(await save("not_executed", "deadline_exceeded"))
        try:
            async with storage.transaction() as tx:
                if await LeaseTransaction(tx).active_count() >= backend.profile.limits.concurrency:
                    raise CapacityExceeded("Sandbox active lease capacity reached")
                operation = operation.model_copy(
                    update={
                        "version": operation.version + 1,
                        "status": "dispatched",
                        "attempts": 1,
                        "attempt_id": str(uuid4()),
                        "error_code": None,
                    }
                )
                lease = lease.model_copy(
                    update={"version": lease.version + 1, "active": True, "create_intent": True}
                )
                await journal.commit_in(
                    tx,
                    operation,
                    EventDraft(
                        type="tool.dispatched",
                        data={"operation_id": operation_id, "execution_key": lease.execution_key},
                    ),
                )
                await LeaseTransaction(tx).put(lease)
        except CapacityExceeded:
            return ToolExecution(await save("not_executed", "capacity_exceeded"))

        async def fact(patch: dict[str, Any]) -> SandboxLease:
            nonlocal lease
            assert lease is not None
            if patch.get("exec_start_intent"):
                if (await permission()).decision != "allow":
                    raise ConfigurationError("Original sandbox permission changed")
            async with storage.transaction() as tx:
                current = await LeaseTransaction(tx).get(owner, run.state.run_id, operation_id)
                assert current is not None
                changed = SandboxLease.model_validate(
                    current.model_dump() | patch | {"version": current.version + 1}
                )
                await LeaseTransaction(tx).put(changed)
            lease = changed
            return changed

        async def execute_owned() -> SandboxResult:
            nonlocal lease
            assert lease is not None
            try:
                lease, exported = await backend.execute(
                    lease,
                    code,
                    inputs=inputs,
                    file_limits=store.limits,
                    fact=fact,
                    seconds=max(0.001, end - loop.time()),
                )
                if exported:
                    reserved = await store._reserve(
                        owner=owner,
                        run_id=run.state.run_id,
                        operation_id=operation_id,
                        purpose="output",
                        size_bytes=sum(len(item.data) for item in exported),
                    )
                    await fact({"stage_id": reserved.stage_id})
                    await store._stage(reserved.stage_id, exported)
            finally:
                # Cancellation of the code stream never cancels independent cleanup.
                cleanup = asyncio.create_task(backend.destroy(lease, fact))
                while not cleanup.done():
                    try:
                        lease = await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        pass
                lease = cleanup.result()
            return await self.result(lease)

        call = asyncio.create_task(execute_owned(), name="drov-sandbox-execution")
        work.track(call)
        try:
            result = await asyncio.shield(call)
        except asyncio.CancelledError:
            call.cancel()
            while not call.done():
                try:
                    await asyncio.shield(call)
                except (asyncio.CancelledError, Exception):
                    pass
            if not call.cancelled():
                call.exception()
            await save("failed_known" if lease.destroyed else "unknown", "tool_cancelled")
            if lease.destroyed:
                await fact({"active": False})
            raise
        except Exception:
            if lease.destroyed:
                await fact({"active": False})
            return ToolExecution(
                await save("failed_known" if lease.destroyed else "unknown", "output_unavailable")
            )
        if (
            len(canonical_json(result.model_dump(mode="json")).encode())
            > run.limits.max_tool_result_bytes
        ):
            await fact({"active": False})
            return ToolExecution(await save("failed_known", "output_unavailable"))
        try:
            async with storage.transaction() as tx:
                current = await tx.get_run(scope, run.state.run_id)
                if current.state.cancel_requested:
                    raise asyncio.CancelledError
                await self.publish_in(tx, lease, result)
                operation = operation.model_copy(
                    update={
                        "version": operation.version + 1,
                        "status": "succeeded",
                        "has_result": True,
                        "result": result.model_dump(mode="json"),
                        "error_code": None,
                    }
                )
                await journal.commit_in(
                    tx,
                    operation,
                    EventDraft(
                        type="tool.succeeded",
                        data={
                            "operation_id": operation_id,
                            "result": result.model_dump(mode="json"),
                        },
                    ),
                )
        except asyncio.CancelledError:

            async def cancel_publication() -> None:
                from drov.runtime.reconciliation import operation_in

                async with storage.transaction() as tx:
                    current = await tx.get_run(scope, run.state.run_id)
                    saved = await operation_in(tx, current, operation_id)
                    if saved.status == "succeeded":
                        return
                    current_lease = await LeaseTransaction(tx).get(
                        owner, run.state.run_id, operation_id
                    )
                    if current_lease is None or not current_lease.destroyed:
                        return
                    updated = saved.model_copy(
                        update={
                            "version": saved.version + 1,
                            "status": "failed_known",
                            "error_code": "tool_cancelled",
                            "has_result": False,
                            "result": None,
                        }
                    )
                    await LeaseTransaction(tx).put(
                        current_lease.model_copy(
                            update={"version": current_lease.version + 1, "active": False}
                        )
                    )
                    if current.state.status == "running":
                        await journal.commit_in(
                            tx,
                            updated,
                            EventDraft(
                                type="tool.failed_known", data={"operation_id": operation_id}
                            ),
                        )

            cleaning = asyncio.create_task(cancel_publication())
            work.track(cleaning)
            await _settle(cleaning)
            raise
        except (Conflict, PersistenceUnavailable) as exc:
            raise PersistenceUnavailable(
                "Original sandbox result publication requires recovery"
            ) from exc
        return ToolExecution(operation)

    async def result(self, lease: SandboxLease) -> SandboxResult:
        if not lease.destroyed or lease.observation is None:
            raise ConfigurationError("Original sandbox output is unavailable")
        data: dict[str, Any] = dict(lease.observation)
        error = data.pop("artifact_error")
        data["artifact_error"] = SandboxArtifactError(code=cast(str, error)) if error else None
        if data["artifact_status"] == "complete":
            if lease.stage_id is None:
                raise ConfigurationError("Original export bytes are unavailable")
            stage = await self.runtime._files.store()._snapshot(lease.stage_id)
            if (
                stage.state != "staged"
                or stage.owner != lease.owner
                or stage.operation_id != lease.operation_id
            ):
                raise ConfigurationError("Original export stage differs")
            for entry in stage.files:
                await self.runtime._files.store()._read_staged(
                    lease.owner, stage.stage_id, entry.ref, name=entry.name
                )
            data["artifacts"] = [entry.ref for entry in stage.files]
        return SandboxResult.model_validate(data)

    async def publish_in(
        self, tx: "Transaction", lease: SandboxLease, result: SandboxResult
    ) -> None:
        if not lease.destroyed:
            raise ConfigurationError("Sandbox output publication requires destruction evidence")
        if result.artifacts:
            assert lease.stage_id is not None
            store = self.runtime._files.store()
            stage = await store._snapshot(lease.stage_id)
            for entry in stage.files:
                await ArtifactTransaction(tx).insert(
                    ArtifactRecord(
                        ref=entry.ref,
                        name=entry.name,
                        owner=lease.owner,
                        run_id=lease.run_id,
                        operation_id=lease.operation_id,
                        store_id=store.store_id,
                        stage_id=stage.stage_id,
                    )
                )
        await LeaseTransaction(tx).put(
            lease.model_copy(update={"active": False, "version": lease.version + 1})
        )

    async def recover(self, record: RunRecord) -> bool:
        """Query/clean/publish the original execution, never create/start replacement code."""
        storage = self.runtime.storage
        owner = MemoryScope(
            tenant_id=record.scope.tenant_id,
            user_id=record.scope.user_id,
            agent_name=record.agent_name,
        )
        agent = self.runtime._agents[(record.agent_name, record.revision)]
        changed = False
        for summary in record.state.operations:
            if summary.tool_name != "sandbox.run_python" or summary.status not in (
                "dispatched",
                "unknown",
            ):
                continue
            tool = next(item for item in agent.tools if isinstance(item, PythonSandbox))
            backend = self.backend(tool)
            async with storage.transaction() as tx:
                lease = await LeaseTransaction(tx).get(
                    owner, record.state.run_id, summary.operation_id
                )
            if lease is None:
                raise ConfigurationError("Original sandbox lease is missing")

            async def fact(
                patch: dict[str, Any], operation_id: str = summary.operation_id
            ) -> SandboxLease:
                nonlocal lease
                assert lease is not None
                async with storage.transaction() as tx:
                    current = await LeaseTransaction(tx).get(
                        owner, record.state.run_id, operation_id
                    )
                    assert current is not None
                    updated = SandboxLease.model_validate(
                        current.model_dump() | patch | {"version": current.version + 1}
                    )
                    await LeaseTransaction(tx).put(updated)
                lease = updated
                return updated

            lease = await backend.destroy(lease, fact)
            result: SandboxResult | None = None
            if not record.state.cancel_requested:
                try:
                    result = await self.result(lease)
                    if (
                        len(canonical_json(result.model_dump(mode="json")).encode())
                        > record.limits.max_tool_result_bytes
                    ):
                        result = None
                except (ConfigurationError, PersistenceUnavailable):
                    pass  # Lost bytes/output are never a reason to run original code again.
            from drov.runtime.reconciliation import operation_in, put_operation

            async with storage.transaction() as tx:
                current = await tx.get_run(owner, record.state.run_id)
                operation = await operation_in(tx, current, summary.operation_id)
                if operation.status not in ("unknown", "dispatched"):
                    raise Conflict("Original sandbox operation was already resolved")
                if current.state.cancel_requested:
                    result = None
                if result is not None:
                    await self.publish_in(tx, lease, result)
                elif lease.active:
                    await LeaseTransaction(tx).put(
                        lease.model_copy(update={"active": False, "version": lease.version + 1})
                    )
                operation = operation.model_copy(
                    update={
                        "version": operation.version + 1,
                        "status": "succeeded" if result is not None else "failed_known",
                        "has_result": result is not None,
                        "result": result.model_dump(mode="json") if result is not None else None,
                        "error_code": None
                        if result is not None
                        else "tool_cancelled"
                        if current.state.cancel_requested
                        else "output_unavailable",
                    }
                )
                await put_operation(tx, current, operation)
                if current.state.pending is not None:
                    item = await tx.get_item(
                        owner, record.state.run_id, "interaction", current.state.pending.request_id
                    )
                    await tx.put_item(
                        owner,
                        item.model_copy(update={"version": item.version + 1, "state": "withdrawn"}),
                        expected_version=item.version,
                    )
                checkpoint = await tx.get_checkpoint(owner, record.state.run_id)
                version = current.state.state_version + 1
                operations = [
                    operation.summary() if item.operation_id == operation.operation_id else item
                    for item in current.state.operations
                ]
                await tx.save_step(
                    owner,
                    expected_version=version - 1,
                    state=current.state.model_copy(
                        update={
                            "status": "suspended",
                            "reason": "recovery",
                            "pending": None,
                            "recoverable": True,
                            "state_version": version,
                            "operations": operations,
                        }
                    ),
                    checkpoint=checkpoint.model_copy(update={"state_version": version}),
                    events=[
                        EventDraft(
                            type=cast(Any, "tool." + operation.status),
                            data={
                                "operation_id": operation.operation_id,
                                **(
                                    {"result": result.model_dump(mode="json")}
                                    if result is not None
                                    else {}
                                ),
                            },
                        ),
                        EventDraft(type="recovery.required"),
                    ],
                )
            changed = True
        return changed
