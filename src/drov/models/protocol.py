"""Normalized adapter boundary. Deltas are provisional until a complete response."""

from collections.abc import AsyncIterator
from typing import Annotated, Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import Field, model_validator

from drov._json import StrictJson
from drov.config import ModelCapabilities
from drov.errors import ModelProtocolError
from drov.types import Identifier, Usage, WireModel


class ModelTool(WireModel):
    name: Identifier
    description: str
    parameters: dict[str, StrictJson]


class ToolCall(WireModel):
    call_id: Identifier
    name: Identifier
    arguments: dict[str, StrictJson]


class ModelMessage(WireModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: StrictJson
    call_id: Identifier | None = None

    @model_validator(mode="after")
    def valid_call(self) -> "ModelMessage":
        if (self.role == "tool") != (self.call_id is not None):
            raise ValueError("Only tool messages must carry a call_id")
        return self


class ModelRequest(WireModel):
    model_call_id: Identifier
    messages: list[ModelMessage]
    tools: list[ModelTool] = Field(default_factory=list)
    output_schema: dict[str, StrictJson] | None = None
    provider_items: list[dict[str, StrictJson]] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_tools(self) -> "ModelRequest":
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("Duplicate exported model tool names")
        return self


class ModelResponse(WireModel):
    message: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    provider_items: list[dict[str, StrictJson]] = Field(default_factory=list)
    finish_reason: Literal["completed", "tool_calls", "refusal", "incomplete"]

    @model_validator(mode="after")
    def valid_plan(self) -> "ModelResponse":
        if len({call.call_id for call in self.tool_calls}) != len(self.tool_calls):
            raise ValueError("Duplicate model tool call IDs")
        if bool(self.tool_calls) != (self.finish_reason == "tool_calls"):
            raise ValueError("Only a complete tool_calls response may carry a tool plan")
        if self.finish_reason == "completed" and self.message is None:
            raise ValueError("Completed text response needs a message")
        return self


class ModelDelta(WireModel):
    type: Literal["delta"] = "delta"
    text: str


class ModelComplete(WireModel):
    type: Literal["complete"] = "complete"
    response: ModelResponse


ModelEvent: TypeAlias = Annotated[ModelDelta | ModelComplete, Field(discriminator="type")]


@runtime_checkable
class ModelAdapter(Protocol):
    @property
    def capabilities(self) -> ModelCapabilities: ...

    def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...

    async def aclose(self) -> None: ...


async def collect_response(events: AsyncIterator[ModelEvent]) -> ModelResponse:
    """Contract helper: never turn partial text into an executable tool plan."""
    response: ModelResponse | None = None
    async for event in events:
        if response is not None:
            raise ModelProtocolError("Adapter emitted data after its complete response")
        if isinstance(event, ModelComplete):
            response = ModelResponse.model_validate(event.response)
        elif not isinstance(event, ModelDelta):
            raise ModelProtocolError("Adapter emitted an unsupported event")
    if response is None:
        raise ModelProtocolError("Adapter stream ended without a complete response")
    return response
