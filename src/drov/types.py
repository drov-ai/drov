"""Versioned public values. These types do not implement a Runtime."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any, Generic, Literal, Self, TypeAlias, TypeVar, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
    model_validator,
)
from typing_extensions import TypeAliasType

from drov._json import StrictJson, load_json

T = TypeVar("T")


def _identifier(value: str) -> str:
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise ValueError("Identifiers cannot contain control characters")
    return value


Identifier: TypeAlias = Annotated[str, Field(min_length=1), AfterValidator(_identifier)]
ScopedIdentifier: TypeAlias = Annotated[Identifier, Field(max_length=128)]
NonEmpty: TypeAlias = Annotated[str, Field(min_length=1)]
PositiveInt: TypeAlias = Annotated[int, Field(gt=0)]
NonNegativeInt: TypeAlias = Annotated[int, Field(ge=0)]
PositiveSeconds: TypeAlias = Annotated[float, Field(gt=0, allow_inf_nan=False)]


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", value):
            raise ValueError("Expected UTC RFC3339 timestamp")
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("Expected timezone-aware UTC datetime")
    return value.astimezone(UTC)


UtcTime: TypeAlias = Annotated[datetime, BeforeValidator(_utc)]
RunStatus: TypeAlias = Literal[
    "accepted", "running", "suspended", "completed", "failed", "cancelled"
]
SuspensionReason: TypeAlias = Literal["approval", "input", "reconciliation", "recovery"]
InteractionKind: TypeAlias = Literal["approval", "input", "reconciliation"]
Effect: TypeAlias = Literal["read", "write", "unknown"]


class WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    @classmethod
    def from_json(cls, data: str | bytes) -> Self:
        return cls.model_validate(load_json(data))

    @model_validator(mode="before")
    @classmethod
    def strict_constants(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for name, info in cls.model_fields.items():
                if name in value and get_args(info.annotation) in ((1,), (0,), (True,), (False,)):
                    expected = get_args(info.annotation)[0]
                    if type(value[name]) is not type(expected):
                        raise ValueError("Literal constants require their exact JSON type")
        return value


class RunContext(WireModel):
    tenant_id: ScopedIdentifier
    user_id: ScopedIdentifier


class Usage(WireModel):
    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    cached_input_tokens: NonNegativeInt | None = None
    total_tokens: NonNegativeInt | None = None
    cost_amount: Annotated[str, Field(pattern=r"^(?:0|[1-9]\d*)(?:\.\d+)?$")] | None = None
    currency: NonEmpty | None = None
    complete: bool = False

    @model_validator(mode="after")
    def valid_cost(self) -> Usage:
        if (self.cost_amount is None) != (self.currency is None):
            raise ValueError("Cost amount and currency must be provided together")
        return self


class RunError(WireModel):
    code: Identifier
    message: str
    stage: Identifier
    retryable: bool = False
    operation_id: Identifier | None = None


class RunRef(WireModel):
    run_id: Identifier
    state_version: PositiveInt


class OperationSummary(WireModel):
    operation_id: Identifier
    tool_name: Identifier
    tool_revision: ScopedIdentifier
    effect: Effect
    status: Literal[
        "prepared", "dispatched", "succeeded", "failed_known", "unknown", "not_executed"
    ]


class InteractionSummary(WireModel):
    schema_version: Literal[1] = 1
    request_id: Identifier
    kind: InteractionKind
    run_id: Identifier
    operation_id: Identifier
    created_at: UtcTime
    expires_at: UtcTime

    @model_validator(mode="after")
    def valid_times(self) -> InteractionSummary:
        if self.expires_at <= self.created_at:
            raise ValueError("Expiry must follow creation")
        return self


class ApprovalRequest(InteractionSummary):
    kind: Literal["approval"] = "approval"
    prompt: str
    tool_name: Identifier
    arguments_summary: StrictJson


class InputRequest(InteractionSummary):
    kind: Literal["input"] = "input"
    prompt: str
    input_schema: dict[str, StrictJson]


class ReconciliationRequest(InteractionSummary):
    kind: Literal["reconciliation"] = "reconciliation"
    prompt: str
    explanation: NonEmpty
    tool_name: Identifier
    arguments_summary: StrictJson


InteractionRequest: TypeAlias = Annotated[
    ApprovalRequest | InputRequest | ReconciliationRequest, Field(discriminator="kind")
]


class ApprovalDecision(WireModel):
    kind: Literal["approval"] = "approval"
    approved: bool
    comment: str | None = None


class InputResponse(WireModel):
    kind: Literal["input"] = "input"
    value: StrictJson


class ReconciliationDecision(WireModel):
    kind: Literal["reconciliation"] = "reconciliation"
    resolution: Literal["applied", "not_applied", "unresolved"]
    evidence: NonEmpty
    result: StrictJson = None

    @property
    def has_result(self) -> bool:
        return "result" in self.model_fields_set

    @model_validator(mode="after")
    def valid_result(self) -> ReconciliationDecision:
        if self.resolution != "applied" and self.has_result:
            raise ValueError("Only applied reconciliation may carry a result")
        return self

    @model_serializer(mode="wrap")
    def serialize_presence(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if not self.has_result:
            data.pop("result", None)
        return data


InteractionResponse: TypeAlias = Annotated[
    ApprovalDecision | InputResponse | ReconciliationDecision, Field(discriminator="kind")
]


class Completed(RunRef, Generic[T]):
    status: Literal["completed"] = "completed"
    usage: Usage = Field(default_factory=Usage)
    output: T


class Suspended(RunRef):
    status: Literal["suspended"] = "suspended"
    usage: Usage = Field(default_factory=Usage)
    reason: SuspensionReason
    pending: InteractionRequest | None

    @model_validator(mode="after")
    def valid_pending(self) -> Suspended:
        if self.reason == "recovery":
            if self.pending is not None:
                raise ValueError("Recovery suspension has no active human request")
        elif self.pending is None or self.pending.kind != self.reason:
            raise ValueError("Suspension reason must match its interaction")
        if self.pending is not None and self.pending.run_id != self.run_id:
            raise ValueError("Interaction belongs to a different run")
        return self


class Failed(RunRef):
    status: Literal["failed"] = "failed"
    usage: Usage = Field(default_factory=Usage)
    error: RunError
    operations: list[OperationSummary] = Field(default_factory=list)


class Cancelled(RunRef):
    status: Literal["cancelled"] = "cancelled"
    usage: Usage = Field(default_factory=Usage)
    operations: list[OperationSummary] = Field(default_factory=list)


RunOutcome = TypeAliasType(
    "RunOutcome",
    Annotated[Completed[T] | Suspended | Failed | Cancelled, Field(discriminator="status")],
    type_params=(T,),
)
JsonOutcome: TypeAlias = RunOutcome[StrictJson]


class ResponseReceipt(RunRef):
    request_id: Identifier
    response_id: Identifier
    accepted_at: UtcTime


class CancelReceipt(RunRef):
    cancel_requested: bool
    status: RunStatus


class RunSnapshot(RunRef):
    status: RunStatus
    reason: SuspensionReason | None = None
    cancel_requested: bool = False
    agent_name: ScopedIdentifier
    revision: ScopedIdentifier
    session_id: ScopedIdentifier
    created_at: UtcTime
    updated_at: UtcTime
    pending: InteractionRequest | InteractionSummary | None = None
    operations: list[OperationSummary] = Field(default_factory=list)
    outcome: JsonOutcome | None = None
    recoverable: bool = False
    usage: Usage = Field(default_factory=Usage)
    content_visible: bool = False

    @model_validator(mode="after")
    def valid_projection(self) -> RunSnapshot:
        if self.updated_at < self.created_at:
            raise ValueError("Update cannot precede creation")
        if not self.content_visible:
            if self.outcome is not None or (
                self.pending is not None and type(self.pending) is not InteractionSummary
            ):
                raise ValueError("Metadata projection cannot carry outcome or interaction body")
        elif self.pending is not None and type(self.pending) is InteractionSummary:
            raise ValueError("Content projection needs a complete interaction request")
        if self.status != "suspended" and (self.reason is not None or self.pending is not None):
            raise ValueError("Only suspended runs have a reason or active request")
        if self.status == "suspended":
            if self.reason is None:
                raise ValueError("Suspended run needs a reason")
            if self.reason == "recovery":
                if self.pending is not None:
                    raise ValueError("Recovery suspension has no active request")
            elif self.pending is None or self.pending.kind != self.reason:
                raise ValueError("Suspension reason must match pending request")
        if self.pending is not None and self.pending.run_id != self.run_id:
            raise ValueError("Pending request belongs to a different run")
        if self.outcome is not None and (
            self.outcome.run_id != self.run_id
            or self.outcome.status != self.status
            or self.outcome.state_version != self.state_version
        ):
            raise ValueError("Outcome does not match snapshot")
        return self


DurableEventType: TypeAlias = Literal[
    "run.accepted",
    "run.running",
    "run.suspended",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run.cancel_requested",
    "model.attempt_started",
    "model.attempt_completed",
    "model.attempt_failed",
    "tool.prepared",
    "tool.dispatched",
    "tool.succeeded",
    "tool.failed_known",
    "tool.denied",
    "tool.unknown",
    "tool.not_executed",
    "interaction.requested",
    "interaction.responded",
    "interaction.expired",
    "context.prepared",
    "memory.changed",
    "recovery.required",
    "verification.started",
    "verification.completed",
]


class DurableEvent(RunRef):
    schema_version: Literal[1] = 1
    seq: PositiveInt
    type: DurableEventType
    occurred_at: UtcTime
    durable: Literal[True] = True
    data: dict[str, StrictJson]
    content_visible: bool = False


class UnknownDurableEvent(DurableEvent):
    """Read-only retention of a future event; never dispatch it as a command."""

    type: Identifier  # type: ignore[assignment]


class TokenDelta(WireModel):
    run_id: Identifier
    type: Literal["token.delta"] = "token.delta"
    durable: Literal[False] = False
    data: str


class StreamGap(WireModel):
    run_id: Identifier
    type: Literal["stream.gap"] = "stream.gap"
    durable: Literal[False] = False
    data: dict[str, StrictJson]


class StreamErrorData(WireModel):
    code: Identifier
    message: str


class StreamError(WireModel):
    run_id: Identifier
    type: Literal["stream.error"] = "stream.error"
    durable: Literal[False] = False
    data: StreamErrorData


TransientEvent: TypeAlias = Annotated[
    TokenDelta | StreamGap | StreamError, Field(discriminator="type")
]


class ContextSource(WireModel):
    id: Identifier
    version: NonEmpty
    kind: Literal[
        "instructions", "input", "history", "memory", "tool", "schema", "provider", "artifact"
    ]
    included: bool
    reason: NonEmpty
    transform: Literal["none", "preview", "excluded"] = "none"
    estimated_tokens: NonNegativeInt
    content: StrictJson = None


class ContextManifest(WireModel):
    schema_version: Literal[1, 2] = 1
    model_call_id: Identifier
    run_id: Identifier
    agent_name: ScopedIdentifier
    revision: ScopedIdentifier
    input_ref: Identifier
    input_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    budget: NonNegativeInt
    estimator: NonEmpty
    estimated_tokens: NonNegativeInt = 0
    reported_usage: Usage = Field(default_factory=Usage)
    sources: list[ContextSource]
    retrieval_status: Literal["disabled", "included", "empty", "timeout", "failed", "excluded"]
    content_visible: bool = False

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and "schema_version" in value
            and type(value["schema_version"]) is not int
        ):
            raise ValueError("Context schema version requires an integer")
        return value

    @model_validator(mode="after")
    def valid_content(self) -> ContextManifest:
        if self.schema_version == 1 and any(item.kind == "artifact" for item in self.sources):
            raise ValueError("Artifact sources require context schema 2")
        for source in self.sources:
            if source.content is not None and (
                not self.content_visible or source.kind == "provider"
            ):
                raise ValueError("Context projection cannot expose this source body")
        return self


class RunInspection(WireModel):
    snapshot: RunSnapshot
    events: list[DurableEvent | UnknownDurableEvent]
    next_cursor: Identifier | None = None
    has_more: bool = False
    context_manifests: list[ContextManifest] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_page(self) -> RunInspection:
        previous = 0
        for event in self.events:
            if event.run_id != self.snapshot.run_id or event.seq <= previous:
                raise ValueError("Page events must belong to the run in increasing order")
            if not self.snapshot.content_visible and event.content_visible:
                raise ValueError("Metadata page cannot carry content events")
            previous = event.seq
        expected = f"{self.snapshot.run_id}:{previous}" if self.events else None
        if self.next_cursor != expected or (self.has_more and not self.events):
            raise ValueError("Invalid event page cursor")
        for manifest in self.context_manifests:
            if manifest.run_id != self.snapshot.run_id or manifest.content_visible:
                raise ValueError("Inspection embeds only same-run context summaries")
        return self


class SessionSummary(WireModel):
    session_id: ScopedIdentifier
    agent_name: ScopedIdentifier
    active_run_id: Identifier | None = None
    created_at: UtcTime
    updated_at: UtcTime

    @model_validator(mode="after")
    def valid_times(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("Update cannot precede creation")
        return self


class SessionPage(WireModel):
    items: list[SessionSummary]
    next_cursor: Identifier | None = None
    has_more: bool = False


class SessionView(SessionSummary):
    runs: list[RunRef]
    next_cursor: Identifier | None = None
    has_more: bool = False


def parse_outcome(data: str | bytes) -> JsonOutcome:
    return TypeAdapter(JsonOutcome).validate_python(load_json(data), strict=True)


def outcome_adapter(output_type: type[T]) -> TypeAdapter[RunOutcome[T]]:
    """Bind an outcome codec to the declared final output type."""
    bound: Any = RunOutcome[output_type]  # type: ignore[valid-type]
    return TypeAdapter(bound)


def parse_response(data: str | bytes) -> InteractionResponse:
    return TypeAdapter(InteractionResponse).validate_python(load_json(data), strict=True)


def parse_event(data: str | bytes) -> DurableEvent | UnknownDurableEvent:
    value = load_json(data)
    if not isinstance(value, dict):
        raise ValueError("Event must be an object")
    if value.get("type") in get_args(DurableEventType):
        return DurableEvent.model_validate(value)
    return UnknownDurableEvent.model_validate(value)
