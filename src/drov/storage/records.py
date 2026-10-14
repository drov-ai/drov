"""Internal durable records. These are full records, not authorized API projections."""

from typing import Literal, Self

from pydantic import Field, model_validator

from drov._json import StrictJson
from drov.config import Limits, validate_input
from drov.types import (
    DurableEventType,
    Identifier,
    InteractionRequest,
    JsonOutcome,
    NonEmpty,
    OperationSummary,
    PositiveInt,
    RunContext,
    RunRef,
    RunStatus,
    ScopedIdentifier,
    Suspended,
    SuspensionReason,
    Usage,
    UtcTime,
    WireModel,
)


class SessionScope(RunContext):
    session_id: ScopedIdentifier


class MemoryScope(RunContext):
    agent_name: ScopedIdentifier


class StartRequest(WireModel):
    scope: SessionScope
    agent_name: ScopedIdentifier
    revision: ScopedIdentifier
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input: NonEmpty
    request_key: ScopedIdentifier | None = None
    limits: Limits = Field(default_factory=Limits)
    requested_limits: Limits | None = None

    @model_validator(mode="after")
    def valid_input(self) -> Self:
        validate_input(self.input, self.limits)
        return self


class RunState(RunRef):
    status: RunStatus
    reason: SuspensionReason | None = None
    cancel_requested: bool = False
    pending: InteractionRequest | None = None
    operations: list[OperationSummary] = Field(default_factory=list)
    outcome: JsonOutcome | None = None
    usage: Usage = Field(default_factory=Usage)
    recoverable: bool = False

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status == "suspended":
            if self.reason is None:
                raise ValueError("Suspension requires a reason")
            Suspended(
                run_id=self.run_id,
                state_version=self.state_version,
                reason=self.reason,
                pending=self.pending,
            )
        elif self.pending is not None or self.reason is not None:
            raise ValueError("Only suspended state has an active request or reason")
        if self.status in ("completed", "failed", "cancelled") and self.outcome is None:
            raise ValueError("Terminal state requires its recorded outcome")
        if self.outcome is not None and (
            self.outcome.run_id != self.run_id
            or self.outcome.state_version != self.state_version
            or self.outcome.status != self.status
        ):
            raise ValueError("Outcome does not match the run state")
        return self


class RunRecord(WireModel):
    schema_version: Literal[1] = 1
    scope: SessionScope
    agent_name: ScopedIdentifier
    revision: ScopedIdentifier
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input: NonEmpty
    limits: Limits
    created_at: UtcTime
    updated_at: UtcTime
    state: RunState
    last_seq: PositiveInt

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Update cannot precede creation")
        validate_input(self.input, self.limits)
        return self


class SessionRecord(WireModel):
    schema_version: Literal[1] = 1
    scope: SessionScope
    agent_name: ScopedIdentifier
    active_run_id: Identifier | None
    created_at: UtcTime
    updated_at: UtcTime
    version: PositiveInt = 1


class Checkpoint(WireModel):
    schema_version: Literal[1] = 1
    run_id: Identifier
    state_version: PositiveInt
    cursor: Identifier
    data: dict[str, StrictJson] = Field(default_factory=dict)


class EventDraft(WireModel):
    type: DurableEventType
    data: dict[str, StrictJson] = Field(default_factory=dict)


class EventRecord(RunRef):
    schema_version: Literal[1] = 1
    seq: PositiveInt
    type: Identifier
    occurred_at: UtcTime
    data: dict[str, StrictJson]


class StartKeyRecord(WireModel):
    schema_version: Literal[1] = 1
    fingerprint: str
    ref: RunRef


ItemKind = Literal[
    "operation",
    "interaction",
    "context",
    "model_input",
    "model_response",
    "receipt",
]


class RunItem(WireModel):
    schema_version: Literal[1] = 1
    run_id: Identifier
    kind: ItemKind
    item_id: Identifier
    version: PositiveInt = 1
    state: Identifier
    data: dict[str, StrictJson]


class MemoryRecord(WireModel):
    schema_version: Literal[1] = 1
    scope: MemoryScope
    key: NonEmpty
    version: PositiveInt
    content: str | None
    deleted: bool = False
    source: Identifier
    updated_at: UtcTime

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.deleted != (self.content is None):
            raise ValueError("Only deleted memory has no content")
        return self
