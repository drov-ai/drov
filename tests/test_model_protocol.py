import pytest
from pydantic import ValidationError

from drov import Agent, Limits, Memory, ModelCapabilities
from drov.errors import ConfigurationError, ModelProtocolError
from drov.models.protocol import (
    ModelAdapter,
    ModelComplete,
    ModelDelta,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCall,
    collect_response,
)
from tests.fixtures import ScriptedAdapter


def request() -> ModelRequest:
    return ModelRequest(model_call_id="call", messages=[ModelMessage(role="user", content="hello")])


def complete() -> ModelComplete:
    return ModelComplete(response=ModelResponse(message="hello", finish_reason="completed"))


@pytest.mark.asyncio
async def test_adapter_protocol_complete_and_opaque_roundtrip() -> None:
    value = ModelResponse(
        finish_reason="tool_calls",
        tool_calls=[ToolCall(call_id="call-1", name="query", arguments={"id": "a"})],
        provider_items=[{"type": "opaque_protocol", "encrypted": "synthetic"}],
    )
    restored = ModelResponse.from_json(value.model_dump_json())
    adapter = ScriptedAdapter(
        [ModelDelta(text="preview"), ModelComplete(response=restored)], expected=request()
    )
    assert isinstance(adapter, ModelAdapter)
    assert await collect_response(adapter.generate(request())) == value
    assert len(adapter.calls) == 1
    assert value.usage.input_tokens is None
    await adapter.aclose()
    assert adapter.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [],
        [ModelDelta(text="partial")],
        [complete(), complete()],
        [complete(), ModelDelta(text="late")],
    ],
)
async def test_partial_or_invalid_stream_does_not_become_response(
    events: list[ModelDelta | ModelComplete],
) -> None:
    adapter = ScriptedAdapter(events)
    with pytest.raises(ModelProtocolError):
        await collect_response(adapter.generate(request()))


@pytest.mark.asyncio
async def test_adapter_failure_keeps_failure() -> None:
    adapter = ScriptedAdapter(
        [ModelDelta(text="partial")], failure=TimeoutError("scripted timeout")
    )
    with pytest.raises(TimeoutError):
        await collect_response(adapter.generate(request()))


@pytest.mark.asyncio
async def test_openai_module_and_definition_need_no_extra_and_missing_extra_has_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    from drov.models.openai import OpenAIResponses

    original = builtins.__import__

    def without_openai(name: str, *args: object, **kwargs: object) -> object:
        if name == "openai":
            raise ModuleNotFoundError("Optional client intentionally unavailable")
        return original(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", without_openai)
    adapter = OpenAIResponses(model="offline", capabilities=ModelCapabilities(text=True))
    assert adapter._client is None
    with pytest.raises(ConfigurationError, match=r"drov\[openai\]"):
        await collect_response(adapter.generate(request()))
    await adapter.aclose()


def test_tool_plan_and_message_validation() -> None:
    call = ToolCall(call_id="a", name="query", arguments={})
    with pytest.raises(ValidationError):
        ModelResponse(tool_calls=[call, call], finish_reason="tool_calls")
    with pytest.raises(ValidationError):
        ModelResponse(tool_calls=[call], finish_reason="incomplete")
    with pytest.raises(ValidationError):
        ModelResponse(finish_reason="completed")
    with pytest.raises(ValidationError):
        ModelMessage(role="tool", content={"ok": True})
    with pytest.raises(ValidationError):
        ModelMessage(role="user", content="hello", call_id="a")


def test_agent_definition_opens_nothing_and_requires_explicit_capabilities() -> None:
    adapter = ScriptedAdapter([])
    agent: Agent[str] = Agent(model=adapter, name="assistant", revision="1")
    assert agent.require_registration_identity() == ("assistant", "1")
    assert adapter.calls == [] and adapter.closed is False
    assert callable(agent.run)
    ephemeral: Agent[str] = Agent(model=adapter)
    with pytest.raises(ConfigurationError):
        ephemeral.require_registration_identity()
    with pytest.raises(ConfigurationError):
        Agent(model=adapter, name="partial")
    unknown = ScriptedAdapter([], capabilities=ModelCapabilities(text=True))
    with pytest.raises(ConfigurationError):
        Agent(model=unknown)
    assert (
        Agent[str](model=unknown, limits=Limits(max_input_tokens=1000)).limits.max_input_tokens
        == 1000
    )
    with pytest.raises(ConfigurationError):
        Agent(model=unknown, limits=Limits(max_input_tokens=1000), memory=Memory())
    with pytest.raises(ConfigurationError):
        Agent(model=adapter, limits=Limits(max_input_tokens=32768))
    assert adapter.calls == []
