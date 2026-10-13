"""Internal operation facts and atomic storage recording; never an HTTP projection."""

import asyncio
from typing import Literal

from pydantic import Field, model_validator

from drov._json import StrictJson, canonical_json
from drov.errors import CapacityExceeded, Conflict, PersistenceUnavailable, RecordNotFound
from drov.storage.base import Storage
from drov.storage.records import EventDraft, RunItem
from drov.types import (
    Effect,
    Identifier,
    NonNegativeInt,
    OperationSummary,
    RunContext,
    ScopedIdentifier,
    WireModel,
)


class ToolOperation(WireModel):
    schema_version: Literal[1] = 1
    run_id: Identifier
    operation_id: Identifier
    tool_name: ScopedIdentifier
    tool_revision: ScopedIdentifier
    effect: Effect
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    arguments: dict[str, StrictJson]
    version: int = Field(default=1, ge=1)
    attempts: NonNegativeInt = 0
    attempt_id: Identifier | None = None
    status: Literal[
        "prepared", "dispatched", "succeeded", "failed_known", "unknown", "not_executed"
    ]
    has_result: bool = False
    result: StrictJson = None
    error_code: Identifier | None = None

    @model_validator(mode="after")
    def consistent(self) -> "ToolOperation":
        if self.has_result != (self.status == "succeeded"):
            raise ValueError("Only a succeeded operation has a result")
        if not self.has_result and self.result is not None:
            raise ValueError("Unavailable output must not carry a value")
        if (self.attempts > 0) != (self.attempt_id is not None):
            raise ValueError("Dispatched attempts require an ID")
        if (
            self.status in ("dispatched", "succeeded", "failed_known", "unknown")
            and self.attempts == 0
        ):
            raise ValueError("Executed operation states require a dispatched attempt")
        if self.status == "succeeded" and self.error_code is not None:
            raise ValueError("Successful results cannot carry an error")
        return self

    def summary(self) -> OperationSummary:
        return OperationSummary(
            operation_id=self.operation_id,
            tool_name=self.tool_name,
            tool_revision=self.tool_revision,
            effect=self.effect,
            status=self.status,
        )


class OperationJournal:
    def __init__(self, storage: Storage, scope: RunContext) -> None:
        self.storage, self.scope = storage, scope

    async def get(self, run_id: str, operation_id: str) -> ToolOperation | None:
        async with self.storage.transaction() as tx:
            try:
                item = await tx.get_item(self.scope, run_id, "operation", operation_id)
            except RecordNotFound:
                return None
        try:
            operation = ToolOperation.from_json(canonical_json(item.data))
        except (ValueError, TypeError) as exc:
            raise PersistenceUnavailable("Stored operation is invalid") from exc
        if (operation.run_id, operation.operation_id, operation.version, operation.status) != (
            item.run_id,
            item.item_id,
            item.version,
            item.state,
        ):
            raise PersistenceUnavailable("Operation payload differs from its index")
        return operation

    async def commit(self, operation: ToolOperation, event: EventDraft) -> None:
        async with self.storage.transaction() as tx:
            run = await tx.get_run(self.scope, operation.run_id)
            if run.state.status != "running":
                raise Conflict("Tools require a currently running record")
            checkpoint = await tx.get_checkpoint(self.scope, operation.run_id)
            data = dict(checkpoint.data)
            if event.type == "tool.dispatched":
                if run.state.cancel_requested:
                    raise asyncio.CancelledError
                count = data.get("tool_dispatch_count", 0)
                if type(count) is not int or count < 0:
                    raise PersistenceUnavailable("Invalid tool dispatch budget")
                if count >= run.limits.max_tool_calls:
                    raise CapacityExceeded("Run tool dispatch budget exhausted")
                data["tool_dispatch_count"] = count + 1
            summaries = [
                item for item in run.state.operations if item.operation_id != operation.operation_id
            ]
            summaries.append(operation.summary())
            version = run.state.state_version + 1
            await tx.put_item(
                self.scope,
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
            await tx.save_step(
                self.scope,
                state=run.state.model_copy(
                    update={"state_version": version, "operations": summaries}
                ),
                expected_version=version - 1,
                checkpoint=checkpoint.model_copy(update={"state_version": version, "data": data}),
                events=[event],
            )
