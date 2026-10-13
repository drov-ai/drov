"""Offline adapter/resource spy. This is not a real model or public runtime."""

from collections.abc import AsyncIterator, Sequence

from drov.config import ModelCapabilities
from drov.models.protocol import ModelEvent, ModelRequest


class ScriptedAdapter:
    def __init__(
        self,
        events: Sequence[ModelEvent],
        *,
        expected: ModelRequest | None = None,
        capabilities: ModelCapabilities | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.capabilities = capabilities or ModelCapabilities(
            text=True,
            tools=True,
            structured_output=True,
            streaming=True,
            context_window=32768,
        )
        self.events = tuple(events)
        self.expected = expected
        self.failure = failure
        self.calls: list[ModelRequest] = []
        self.closed = False

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if self.closed or self.calls:
            raise AssertionError("Unexpected model request")
        if self.expected is not None and request != self.expected:
            raise AssertionError("Model input differs from saved contract")
        self.calls.append(request)
        for event in self.events:
            yield event
        if self.failure is not None:
            raise self.failure

    async def aclose(self) -> None:
        self.closed = True
