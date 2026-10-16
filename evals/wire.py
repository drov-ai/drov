"""Host-only bounded billing receipt capture, independent of plan acceptance.

Evaluation requests buffer SSE before SDK decoding so an early protocol rejection
cannot discard a later usage receipt. This changes delivery latency; it is not a
measurement of provider streaming latency. No response text is written here.
"""

from __future__ import annotations

import json
from typing import Any

from drov.errors import ModelProtocolError
from drov.models.openai import _usage


class WireReceipt:
    def __init__(self, *, limit: int = 2 * 1024 * 1024) -> None:
        self.limit = limit
        self.metadata: dict[str, Any] = {}

    async def capture(self, response: Any) -> None:
        self.metadata.clear()
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self.limit:
                await response.aclose()
                raise ValueError("Evaluation response exceeded its bounded receipt buffer")
            chunks.append(chunk)
        # Same cache used by httpx2.Response.aread; the SDK still decodes and
        # validates the original bytes, including every original SSE event.
        body = b"".join(chunks)
        response._content = body
        terminals: list[dict[str, Any]] = []
        for line in body.splitlines():
            if not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line[5:])
            except (ValueError, UnicodeError):
                continue
            if (
                isinstance(event, dict)
                and event.get("type")
                in ("response.completed", "response.incomplete", "response.failed")
                and isinstance(event.get("response"), dict)
            ):
                terminals.append(event["response"])
        if len(terminals) != 1:
            return
        terminal = terminals[0]
        try:
            usage = _usage(terminal.get("usage"))
        except ModelProtocolError:
            return
        if not usage.complete:
            return
        self.metadata = {
            "source": "bounded_http_sse_receipt",
            "provider_response_id": terminal.get("id"),
            "provider_model": terminal.get("model"),
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "input_tokens_details": {"cached_tokens": usage.cached_input_tokens},
            },
        }
