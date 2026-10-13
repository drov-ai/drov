"""Deterministic offline adapter. It performs no inference or network calls."""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass

from drov.config import ModelCapabilities
from drov.models.protocol import ModelComplete, ModelEvent, ModelRequest, ModelResponse


@dataclass(frozen=True)
class ScriptedStep:
    events: Sequence[ModelEvent] = ()
    error: Exception | None = None
    check: Callable[[ModelRequest], None] | None = None


class ScriptedModel:
    """One predefined step per attempt; reject extra calls instead of inventing answers."""

    def __init__(self, steps: Sequence[ModelResponse | ScriptedStep]) -> None:
        self.capabilities = ModelCapabilities(
            text=True, tools=True, structured_output=True, streaming=True, context_window=32768
        )
        self._steps = tuple(steps)
        self.calls: list[ModelRequest] = []
        self.closed = False

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if self.closed or len(self.calls) >= len(self._steps):
            raise AssertionError("Unexpected scripted model call")
        step = self._steps[len(self.calls)]
        snapshot = ModelRequest.from_json(request.model_dump_json())
        self.calls.append(snapshot)
        if isinstance(step, ModelResponse):
            step = ScriptedStep(events=(ModelComplete(response=step),))
        if step.check is not None:
            step.check(snapshot)
        for event in step.events:
            await asyncio.sleep(0)
            yield event
        if step.error is not None:
            raise step.error

    async def aclose(self) -> None:
        self.closed = True
