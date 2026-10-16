"""Optional Responses adapter. Construction and import never create a client."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from drov._json import StrictJson, canonical_json, load_json
from drov.config import ModelCapabilities
from drov.errors import (
    ConfigurationError,
    ModelProtocolError,
    ModelProviderError,
    ShutdownIncomplete,
    TransientModelError,
    UnsupportedSchema,
)
from drov.models.protocol import (
    ModelComplete,
    ModelDelta,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ToolCall,
)
from drov.tools.schema import strict_schema
from drov.types import Usage

if TYPE_CHECKING:
    from openai import AsyncOpenAI

_BUNDLE = "drov.openai.responses.v1"
_EVENTS = {
    "response.created",
    "response.in_progress",
    "response.output_item.added",
    "response.output_item.done",
    "response.content_part.added",
    "response.content_part.done",
    "response.output_text.delta",
    "response.output_text.done",
    "response.output_text.annotation.added",
    "response.refusal.delta",
    "response.refusal.done",
    "response.function_call_arguments.delta",
    "response.function_call_arguments.done",
    "response.reasoning_summary_part.added",
    "response.reasoning_summary_part.done",
    "response.reasoning_summary_text.delta",
    "response.reasoning_summary_text.done",
}


def _name(logical: str) -> str:
    return "drov_" + hashlib.sha256(logical.encode("utf-8")).hexdigest()[:56]


def _object(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise ModelProtocolError("Expected a Responses object")
    return cast(dict[str, Any], value)


def _string(value: object) -> str:
    if type(value) is not str:
        raise ModelProtocolError("Expected Responses text")
    return value


def _retry_after(headers: Any) -> float | None:
    try:
        if headers.get("retry-after-ms") is not None:
            return float(headers["retry-after-ms"]) / 1000
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return None


def _schema(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("type") != "object":
        raise UnsupportedSchema("Responses strict schemas require an object root")
    try:
        return strict_schema(value)
    except (KeyError, TypeError, AttributeError, ValueError):
        raise UnsupportedSchema("Unsupported Responses strict schema") from None


def _assistant(response: ModelResponse) -> StrictJson:
    if response.tool_calls:
        return {
            "message": response.message,
            "tool_calls": [call.model_dump(mode="json") for call in response.tool_calls],
        }
    return response.message


def _usage(value: object) -> Usage:
    if value is None:
        return Usage()
    data = _object(value)
    names = ("input_tokens", "output_tokens", "total_tokens")
    tokens = {name: data.get(name) for name in names}
    details = data.get("input_tokens_details")
    tokens["cached_input_tokens"] = (
        _object(details).get("cached_tokens") if details is not None else None
    )
    try:
        return Usage(**tokens, complete=all(v is not None for v in tokens.values()))
    except ValidationError:
        raise ModelProtocolError("Invalid Responses usage") from None


def _response(
    data: dict[str, Any], names: dict[str, str], *, plaintext_reasoning: bool = False
) -> ModelResponse:
    status = data.get("status")
    usage = _usage(data.get("usage"))
    if status == "incomplete":
        return ModelResponse(finish_reason="incomplete", usage=usage)
    if status != "completed" or data.get("error") is not None:
        raise ModelProviderError("Responses request failed")
    output = data.get("output")
    if type(output) is not list:
        raise ModelProtocolError("Missing Responses output")
    texts: list[str] = []
    final_texts: list[str] = []
    calls: list[ToolCall] = []
    refused = False
    ids: set[str] = set()
    for raw in output:
        item = _object(raw)
        item_id = _string(item.get("id"))
        if not item_id or item_id in ids:
            raise ModelProtocolError("Invalid or duplicate Responses item ID")
        ids.add(item_id)
        kind = item.get("type")
        if kind == "reasoning":
            # A stored remote ID alone cannot continue a stateless reasoning turn.
            if plaintext_reasoning:
                content = item.get("content")
                if type(content) is not list or not content:
                    raise ModelProtocolError("Reasoning item lacks plaintext continuation data")
                for part in content:
                    part = _object(part)
                    if part.get("type") != "reasoning_text":
                        raise ModelProtocolError("Unsupported plaintext reasoning part")
                    _string(part.get("text"))
                if item.get("encrypted_content") is not None and not isinstance(
                    item["encrypted_content"], str
                ):
                    raise ModelProtocolError("Invalid optional reasoning metadata")
            elif not _string(item.get("encrypted_content")):
                raise ModelProtocolError("Reasoning item lacks encrypted continuation data")
        elif kind == "function_call":
            if item.get("status") != "completed":
                raise ModelProtocolError("Incomplete Responses function call")
            wire = _string(item.get("name"))
            if wire not in names:
                raise ModelProtocolError("Responses returned an unknown function")
            try:
                arguments = _object(load_json(_string(item.get("arguments"))))
                calls.append(
                    ToolCall(
                        call_id=_string(item.get("call_id")), name=names[wire], arguments=arguments
                    )
                )
            except (ValueError, TypeError):
                raise ModelProtocolError("Invalid Responses function arguments") from None
        elif kind == "message":
            if item.get("role") != "assistant" or item.get("status") != "completed":
                raise ModelProtocolError("Incomplete Responses assistant message")
            if item.get("phase") not in (None, "commentary", "final_answer"):
                raise ModelProtocolError("Unsupported Responses message phase")
            content = item.get("content")
            if type(content) is not list or not content:
                raise ModelProtocolError("Missing Responses message content")
            for part in content:
                part = _object(part)
                if part.get("type") == "output_text":
                    text = _string(part.get("text"))
                    texts.append(text)
                    if item.get("phase") == "final_answer":
                        final_texts.append(text)
                elif part.get("type") == "refusal":
                    _string(part.get("refusal"))
                    refused = True
                else:
                    raise ModelProtocolError("Unsupported Responses content part")
        else:
            raise ModelProtocolError("Unsupported Responses output item")
    try:
        result = ModelResponse(
            message="".join(final_texts or texts) if texts else None,
            tool_calls=[] if refused else calls,
            finish_reason="refusal" if refused else "tool_calls" if calls else "completed",
            usage=usage,
        )
        if not refused:
            result = result.model_copy(
                update={
                    "provider_items": [
                        {
                            "type": "drov.deepseek.responses.v1"
                            if plaintext_reasoning
                            else _BUNDLE,
                            "assistant": _assistant(result),
                            "output": output,
                        }
                    ]
                }
            )
        return ModelResponse.from_json(result.model_dump_json())
    except (ValueError, TypeError):
        raise ModelProtocolError("Invalid complete Responses plan") from None


class OpenAIResponses:
    """Explicit model/capabilities; an injected AsyncOpenAI is always borrowed.

    Own clients are created lazily from the process environment. Use a Runtime
    model binding or call aclose explicitly. No arbitrary provider overrides,
    remote conversations, hosted tools, automatic truncation or SDK retries.
    """

    _protocol = _BUNDLE
    _events = _EVENTS

    def _parse_response(self, data: dict[str, Any], names: dict[str, str]) -> ModelResponse:
        return _response(data, names)

    def _observe_event(self, data: dict[str, Any]) -> None:
        """Private dialect diagnostics; observation never accepts a plan."""

    def __init__(
        self,
        *,
        model: str,
        capabilities: ModelCapabilities,
        client: AsyncOpenAI | None = None,
        max_output_tokens: int = 2048,
    ) -> None:
        if type(model) is not str or not model.strip() or any(ord(c) < 32 for c in model):
            raise ConfigurationError("Responses requires an explicit model ID")
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ConfigurationError("max_output_tokens must be a positive integer")
        self._model = model
        self._capabilities = ModelCapabilities.model_validate(capabilities)
        self._max_output_tokens = max_output_tokens
        self._source_client = client
        self._client: AsyncOpenAI | None = None
        self._closed = False
        self._active = 0
        if not self.capabilities.text:
            raise ConfigurationError("Responses v0.1 requires declared text capability")

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    @property
    def registration_config(self) -> dict[str, StrictJson]:
        return {
            "protocol": self._protocol,
            "model": self._model,
            "max_output_tokens": self._max_output_tokens,
        }

    def validate_configuration(
        self, *, tools: list[ModelTool], output_schema: dict[str, StrictJson] | None
    ) -> None:
        if tools and not self.capabilities.tools:
            raise ConfigurationError("Responses model has no declared tool capability")
        if output_schema is not None:
            if not self.capabilities.structured_output:
                raise ConfigurationError("Responses model has no declared structured output")
            _schema(output_schema)
        if len({_name(tool.name) for tool in tools}) != len(tools):
            raise ConfigurationError("Responses function name collision")
        for tool in tools:
            _schema(tool.parameters)

    def _input(self, request: ModelRequest) -> list[dict[str, Any]]:
        bundles = iter(request.provider_items)
        inputs: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "assistant":
                bundle = next(bundles, None)
                if (
                    bundle is None
                    or set(bundle) != {"type", "assistant", "output"}
                    or bundle["type"] != self._protocol
                    or bundle["assistant"] != message.content
                    or type(bundle["output"]) is not list
                ):
                    raise ModelProtocolError("Responses history has an unpaired protocol bundle")
                output = [_object(item) for item in cast(list[Any], bundle["output"])]
                content = message.content
                prior_names: dict[str, str] = {}
                if type(content) is dict:
                    for item in cast(list[Any], content.get("tool_calls", [])):
                        logical = _string(_object(item).get("name"))
                        prior_names[_name(logical)] = logical
                prior = self._parse_response({"status": "completed", "output": output}, prior_names)
                if _assistant(prior) != message.content or prior.finish_reason == "refusal":
                    raise ModelProtocolError("Responses history protocol content mismatch")
                inputs.extend(output)
            elif message.role == "tool":
                inputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.call_id,
                        "output": canonical_json(message.content),
                    }
                )
            else:
                inputs.append(
                    {
                        "role": message.role,
                        "content": message.content
                        if isinstance(message.content, str)
                        else canonical_json(message.content),
                    }
                )
        if next(bundles, None) is not None:
            raise ModelProtocolError("Responses history has extra protocol bundles")
        return inputs

    def _body(self, request: ModelRequest) -> dict[str, Any]:
        self.validate_configuration(tools=request.tools, output_schema=request.output_schema)
        body: dict[str, Any] = {
            "model": self._model,
            "input": self._input(request),
            "stream": self.capabilities.streaming,
            "store": False,
            "truncation": "disabled",
            "parallel_tool_calls": False,
            "include": ["reasoning.encrypted_content"],
            "max_output_tokens": self._max_output_tokens,
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": _name(tool.name),
                    "description": tool.description,
                    "parameters": _schema(tool.parameters),
                    "strict": True,
                }
                for tool in request.tools
            ]
        if request.output_schema is not None:
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "drov_output",
                    "schema": _schema(request.output_schema),
                    "strict": True,
                }
            }
        return body

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError:
                raise ConfigurationError("Install drov[openai] to use OpenAIResponses") from None
            if self._source_client is None:
                try:
                    self._client = AsyncOpenAI(max_retries=0)
                except Exception:
                    raise ConfigurationError(
                        "Configure Responses credentials in the host"
                    ) from None
            else:
                if not isinstance(self._source_client, AsyncOpenAI):
                    raise ConfigurationError("Responses client must be an AsyncOpenAI")
                self._client = self._source_client.with_options(max_retries=0)
        return self._client

    async def generate(self, request: ModelRequest) -> AsyncGenerator[ModelEvent]:
        if self._closed:
            raise ConfigurationError("Responses adapter is closed")
        body = self._body(ModelRequest.from_json(request.model_dump_json()))
        client = self._get_client()
        from openai import APIConnectionError, APIError, APIStatusError

        names = {_name(tool.name): tool.name for tool in request.tools}
        self._active += 1
        try:
            try:
                result = await client.responses.create(**body)
                if self.capabilities.streaming:
                    terminal: dict[str, Any] | None = None
                    last_sequence = -1
                    try:
                        async for event in result:
                            data = event.model_dump(
                                mode="json", exclude_unset=True, warnings="none"
                            )
                            self._observe_event(data)
                            kind = data.get("type")
                            sequence = data.get("sequence_number")
                            if type(sequence) is not int or sequence <= last_sequence:
                                raise ModelProtocolError("Invalid Responses event sequence")
                            last_sequence = sequence
                            if terminal is not None:
                                raise ModelProtocolError("Responses data after terminal event")
                            if kind in ("response.completed", "response.incomplete"):
                                terminal = _object(data.get("response"))
                                if terminal.get("status") != kind.removeprefix("response."):
                                    raise ModelProtocolError("Responses terminal status mismatch")
                            elif kind in ("response.failed", "error"):
                                raise ModelProviderError("Responses stream failed")
                            elif kind not in self._events:
                                raise ModelProtocolError("Unsupported Responses stream event")
                            elif kind in (
                                "response.output_item.added",
                                "response.output_item.done",
                            ):
                                if _object(data.get("item")).get("type") not in (
                                    "message",
                                    "function_call",
                                    "reasoning",
                                ):
                                    raise ModelProtocolError("Unsupported Responses stream item")
                            elif kind == "response.output_text.delta":
                                yield ModelDelta(text=_string(data.get("delta")))
                        if terminal is None:
                            raise ModelProtocolError("Responses stream ended before completion")
                        response = self._parse_response(terminal, names)
                    finally:
                        await result.close()
                else:
                    response = self._parse_response(
                        result.model_dump(mode="json", exclude_unset=True, warnings="none"), names
                    )
            except APIStatusError as exc:
                if exc.status_code in (408, 409, 429) or exc.status_code >= 500:
                    raise TransientModelError(
                        "Transient Responses HTTP failure",
                        retry_after_seconds=_retry_after(exc.response.headers),
                    ) from None
                raise ModelProviderError("Responses HTTP request rejected") from None
            except APIConnectionError:
                raise TransientModelError("Responses connection failed") from None
            except APIError:
                raise ModelProviderError("Responses client or stream failed") from None
            except (ValidationError, ValueError, TypeError, AttributeError):
                raise ModelProtocolError("Malformed Responses data") from None
            yield ModelComplete(response=response)
        finally:
            self._active -= 1

    async def aclose(self) -> None:
        if self._closed:
            return
        if self._active:
            raise ShutdownIncomplete("Responses still has active calls")
        if self._client is not None and self._source_client is None:
            await self._client.close()
        self._closed = True
