"""Bounded transient subscriptions; durable events are paged from storage."""

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Self

from drov.agent import Agent
from drov.config import Limits
from drov.errors import ConfigurationError
from drov.types import (
    DurableEvent,
    RunContext,
    RunOutcome,
    StreamGap,
    TokenDelta,
    UnknownDurableEvent,
)

if TYPE_CHECKING:
    from drov.runtime.runs import RunController

StreamEvent = DurableEvent | UnknownDurableEvent | TokenDelta | StreamGap


class RunEvents:
    def __init__(
        self,
        controller: "RunController",
        run_id: str,
        *,
        context: RunContext,
        after: str | None = None,
        include_content: bool = False,
    ) -> None:
        self.controller, self.run_id = controller, run_id
        self.context = RunContext.from_json(context.model_dump_json())
        self.after, self.include_content = after, include_content
        self._durable: deque[DurableEvent | UnknownDurableEvent] = deque()
        self._deltas: deque[str] = deque()
        self._dropped = 0
        self._opened = False
        self._closed = False
        self._changed = asyncio.Event()

    async def __aenter__(self) -> Self:
        if self._opened or self._closed:
            raise ConfigurationError("Subscription is single-use")
        await self.controller.visible(self.run_id, self.context, self.include_content)
        self.controller.cursor(self.run_id, self.after)
        self.controller.subscribers.setdefault(self.run_id, set()).add(self)
        self._opened = True
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        subscribers = self.controller.subscribers.get(self.run_id)
        if subscribers is not None:
            subscribers.discard(self)
            if not subscribers:
                del self.controller.subscribers[self.run_id]
        self._closed = True
        self._durable.clear()
        self._deltas.clear()
        self._changed.set()

    def delta(self, value: str) -> None:
        if not self.include_content or self._closed:
            return
        limits = self.controller.runtime.limits
        if (
            len(value.encode("utf-8")) > limits.max_delta_bytes
            or len(self._deltas) >= limits.subscription_buffer
        ):
            self._dropped += 1
        else:
            self._deltas.append(value)
        self._changed.set()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> StreamEvent:
        if not self._opened:
            raise ConfigurationError("Enter the subscription before iterating")
        while not self._closed:
            if self._durable or self._deltas or self._dropped:
                # A page fetched before revocation never grants future body access.
                await self.controller.visible(self.run_id, self.context, self.include_content)
                if self._durable:
                    event = self._durable.popleft()
                    self.after = f"{self.run_id}:{event.seq}"
                    return event
                if self._dropped:
                    count, self._dropped = self._dropped, 0
                    return StreamGap(run_id=self.run_id, data={"dropped_deltas": count})
                return TokenDelta(run_id=self.run_id, data=self._deltas.popleft())
            self._changed.clear()
            page = await self.controller.inspect(
                self.run_id,
                context=self.context,
                after=self.after,
                limit=min(500, self.controller.runtime.limits.page_size),
                include_content=self.include_content,
            )
            if page.events:
                self._durable.extend(page.events)
                continue
            if self._deltas or self._dropped:
                continue
            # Only the current snapshot, after all its committed events, can end a stream.
            if page.snapshot.status in ("suspended", "completed", "failed", "cancelled"):
                await self.aclose()
                raise StopAsyncIteration
            if self.run_id in self.controller.failures:
                raise self.controller.failures[self.run_id]
            if self.run_id not in self.controller.works:
                raise ConfigurationError("Run has no active owner; recovery requires T-09")
            # Polling bridges journal commits made by T-05 without an in-memory notification.
            # No durable event is queued or dropped; the next same-run cursor reads every commit.
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=0.05)
            except TimeoutError:
                pass
        raise StopAsyncIteration

    async def result(self) -> RunOutcome[Any]:
        return await self.controller.wait(self.run_id, context=self.context)


class RunStream(RunEvents):
    def __init__(
        self,
        controller: "RunController",
        agent: Agent[Any],
        input: str,
        *,
        session_id: str,
        context: RunContext,
        request_key: str | None = None,
        limits: Limits | None = None,
    ) -> None:
        super().__init__(controller, "unaccepted", context=context, include_content=True)
        self.agent, self.input, self.session_id = agent, input, session_id
        self.request_key, self.limits = request_key, limits

    async def __aenter__(self) -> Self:
        if self._opened or self._closed:
            raise ConfigurationError("Subscription is single-use")
        ref = await self.controller.start(
            self.agent,
            self.input,
            session_id=self.session_id,
            context=self.context,
            request_key=self.request_key,
            limits=self.limits,
            content=True,
        )
        self.run_id = ref.run_id
        return await super().__aenter__()
