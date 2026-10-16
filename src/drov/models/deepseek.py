"""DeepSeek's stateless Responses dialect, using an explicitly borrowed client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from drov._json import StrictJson
from drov.config import ModelCapabilities
from drov.errors import ConfigurationError
from drov.models.openai import _EVENTS, OpenAIResponses, _response
from drov.models.protocol import ModelRequest, ModelResponse

if TYPE_CHECKING:
    from openai import AsyncOpenAI


class DeepSeekResponses(OpenAIResponses):
    """Plaintext continuation stays private in versioned provider items.

    Configure the host's AsyncOpenAI with DeepSeek's endpoint and credentials.
    This adapter borrows it, disables SDK retries on its copy, and never loads
    credentials or chooses an endpoint. Drov still authorizes and serializes
    every tool even when the provider proposes multiple calls.
    """

    _protocol = "drov.deepseek.responses.v1"
    _events = _EVENTS | {"response.reasoning_text.delta", "response.reasoning_text.done"}

    def __init__(
        self,
        *,
        model: str,
        capabilities: ModelCapabilities,
        client: AsyncOpenAI,
        max_output_tokens: int = 2048,
        reasoning_effort: Literal["none", "low", "high", "max"] = "none",
    ) -> None:
        if client is None:
            raise ConfigurationError("DeepSeek requires an explicitly configured borrowed client")
        if reasoning_effort not in ("none", "low", "high", "max"):
            raise ConfigurationError("Unsupported DeepSeek reasoning effort")
        super().__init__(
            model=model,
            capabilities=capabilities,
            client=client,
            max_output_tokens=max_output_tokens,
        )
        self._reasoning_effort = reasoning_effort
        self._last_response_metadata: dict[str, Any] = {}

    @property
    def registration_config(self) -> dict[str, StrictJson]:
        return super().registration_config | {"reasoning_effort": self._reasoning_effort}

    def _parse_response(self, data: dict[str, Any], names: dict[str, str]) -> ModelResponse:
        response = _response(data, names, plaintext_reasoning=True)
        if "usage" in data:
            self._last_response_metadata = {
                "provider_model": data.get("model"),
                "provider_response_id": data.get("id"),
                "usage": data["usage"],
            }
        return response

    def _body(self, request: ModelRequest) -> dict[str, Any]:
        body = super()._body(request)
        for ignored in ("include", "truncation", "parallel_tool_calls", "store"):
            del body[ignored]
        body["reasoning"] = {"effort": self._reasoning_effort}
        return body
