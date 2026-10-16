"""Explicit offline fixture and budgeted real model; never silently switch modes."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from drov._json import canonical_json
from drov.config import ModelCapabilities
from drov.errors import ModelProtocolError
from drov.models.openai import _usage
from drov.models.protocol import ModelAdapter, ModelComplete, ModelEvent, ModelRequest
from evals.budget import BudgetStop, Ledger
from evals.common import final, plan
from evals.wire import WireReceipt

CAPS = ModelCapabilities(
    text=True, tools=True, streaming=True, structured_output=True, context_window=1_000_000
)


def memory_records(request: ModelRequest) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for message in request.messages:
        content = message.content
        if isinstance(content, dict) and content.get("type") == "drov.memory":
            record = content.get("record")
            if isinstance(record, dict):
                records.append(record)
    return records


class RecordedModel:
    def __init__(
        self,
        source: ModelAdapter,
        directory: Path,
        *,
        ledger: Ledger | None = None,
        batch_hash: str = "",
        trial: str = "",
        wire_receipt: WireReceipt | None = None,
        max_output_tokens: int = 2048,
    ) -> None:
        self.source, self.directory, self.ledger = source, directory, ledger
        self.batch_hash, self.trial = batch_hash, trial
        self.calls: list[ModelRequest] = []
        self.wire_receipt = wire_receipt
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("Expected positive output reservation")
        self.max_output_tokens = max_output_tokens

    @property
    def capabilities(self) -> ModelCapabilities:
        return self.source.capabilities

    @property
    def registration_config(self) -> dict[str, Any]:
        return getattr(self.source, "registration_config", {"fixture": "t16-v1"})

    def validate_configuration(self, **kwargs: Any) -> None:
        validate = getattr(self.source, "validate_configuration", None)
        if validate is not None:
            validate(**kwargs)

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls.append(request)
        self.directory.mkdir(parents=True, exist_ok=True)
        stem = self.directory / f"model-{len(self.calls):02}"
        request_json = request.model_dump_json(indent=2)
        stem.with_suffix(".request.json").write_text(request_json, encoding="utf-8")
        identifier = None
        if self.ledger is not None:
            body = self.source._body(request)  # type: ignore[attr-defined]
            if body.get("max_output_tokens") != self.max_output_tokens:
                raise ValueError("Provider output allowance differs from cost reservation")
            identifier = self.ledger.reserve(
                batch_hash=self.batch_hash,
                trial=self.trial,
                request_hash=hashlib.sha256(request_json.encode()).hexdigest(),
                input_bytes=len(canonical_json(body).encode()) + 1024,
                max_output_tokens=self.max_output_tokens,
            )
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat()
        if self.wire_receipt is not None:
            self.wire_receipt.metadata.clear()
        try:
            async for event in self.source.generate(request):
                if isinstance(event, ModelComplete):
                    stem.with_suffix(".response.json").write_text(
                        event.response.model_dump_json(indent=2), encoding="utf-8"
                    )
                    if self.ledger is not None and identifier is not None:
                        self.ledger.settle(identifier, event.response.usage)
                    metadata = {
                        "started_at": started_at,
                        "elapsed_seconds": time.monotonic() - started,
                        "provider": getattr(self.source, "_last_response_metadata", {}),
                    }
                    stem.with_suffix(".metadata.json").write_text(
                        json.dumps(metadata, indent=2), encoding="utf-8"
                    )
                yield event
        except Exception as exc:
            provider = getattr(self.source, "_last_response_metadata", {})
            if not provider.get("usage") and self.wire_receipt is not None:
                provider = self.wire_receipt.metadata
            diagnostics = {
                "started_at": started_at,
                "error_type": type(exc).__name__,
                "protocol_message": str(exc) if isinstance(exc, ModelProtocolError) else None,
                "last_event": getattr(self.source, "_last_protocol_event", {}),
                "provider": provider,
                "usage_settled": False,
            }
            if (
                isinstance(exc, ModelProtocolError)
                and self.ledger is not None
                and identifier is not None
                and provider.get("usage") is not None
            ):
                try:
                    self.ledger.settle(identifier, _usage(provider["usage"]))
                    diagnostics["usage_settled"] = True
                except (BudgetStop, ModelProtocolError):
                    pass
            stem.with_suffix(".error.json").write_text(
                json.dumps(diagnostics, indent=2), encoding="utf-8"
            )
            raise

    async def aclose(self) -> None:
        await self.source.aclose()


class FixtureModel:
    capabilities = CAPS

    def __init__(self, task: dict[str, Any], phase: str) -> None:
        self.task, self.phase = task, phase

    async def aclose(self) -> None:
        pass

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        task, phase = self.task, self.phase
        tools = [m.content for m in request.messages if m.role == "tool"]
        if task["id"].endswith("09"):
            result = self._code(request, tools)
        elif task["scenario"] == "S-01":
            result = (
                final(task["expected"]["output"])
                if tools
                else plan("search_docs", {"query": "缓存"})
            )
        elif task["scenario"] == "S-02":
            if phase in ("save", "update", "forget", "write-failure") and not tools:
                args: dict[str, Any] = {
                    "key": "reply-style",
                    "expected_version": 1 if phase in ("update", "forget") else 0,
                }
                if phase != "forget":
                    args["content"] = "详细中文" if phase == "update" else "简洁中文"
                result = plan("memory.forget" if phase == "forget" else "memory.remember", args)
            else:
                records = memory_records(request)
                record = records[0] if records else None
                status = {
                    "save": "saved",
                    "update": "updated",
                    "forget": "forgotten",
                    "write-failure": "write_failed",
                }.get(phase, "present" if record else "absent")
                result = final(
                    {
                        "status": status,
                        "content": ("详细中文" if phase == "update" else "简洁中文")
                        if phase in ("save", "update")
                        else record["content"]
                        if record
                        else None,
                        "version": (2 if phase in ("update", "forget") else 1)
                        if phase in ("save", "update", "forget")
                        else record["version"]
                        if record
                        else None,
                    }
                )
        else:
            if not tools and task["variant"] == "clarify":
                result = plan("ask_item", {"prompt": "请提供事项ID"})
            elif not tools or (
                isinstance(tools[-1], dict) and tools[-1].get("tool_name") == "ask_item"
            ):
                result = plan("update_item", {"item_id": "item-01", "status": "processing"})
            elif isinstance(tools[-1], dict) and tools[-1].get("result") == {"item_id": "item-01"}:
                result = plan("update_item", {"item_id": "item-01", "status": "processing"})
            else:
                result = final(
                    {
                        "item_id": "item-01",
                        "status": "denied" if task["variant"] in ("deny", "revoke") else "applied",
                    }
                )
        yield ModelComplete(response=result)

    def _code(self, request: ModelRequest, tools: list[Any]) -> Any:
        variant = self.task["variant"]
        results = [
            t["result"]
            for t in tools
            if isinstance(t, dict)
            and isinstance(t.get("result"), dict)
            and "exit_code" in t["result"]
        ]
        if not results:
            descriptors: list[dict[str, Any]] = []
            for m in request.messages:
                if isinstance(m.content, str) and m.content.startswith(
                    "Granted artifact descriptors"
                ):
                    descriptors = json.loads(m.content.split(": ", 1)[1])
            descriptor = descriptors[0]
            identifier = descriptor["artifact_id"]
            path = "/work/inputs/" + identifier + "/" + descriptor["name"]
            if self.phase == "reuse":
                code = (
                    "from pathlib import Path\n"
                    "assert not Path('/work/outputs/summary.csv').exists()\n"
                    f"data=Path({path!r}).read_bytes()\n"
                    "Path('/work/outputs/copied.csv').write_bytes(data)\nprint('3,60,20.0')"
                )
                outputs = ["copied.csv"]
            elif variant == "proposal":
                code = (
                    "import csv\n"
                    f"v=[int(r['value']) for r in csv.DictReader(open({path!r}))]\n"
                    "from pathlib import Path\n"
                    "Path('/work/outputs/proposal.csv').write_text('item_id,status,total\\nitem-01,processing,'+str(sum(v))+'\\n')\n"
                    "print(len(v),sum(v),sum(v)/len(v))"
                )
                outputs = ["proposal.csv"]
            else:
                code = (
                    "import csv\n"
                    f"v=[int(r['value']) for r in csv.DictReader(open({path!r}))]\n"
                    "with open('/work/outputs/summary.csv','w') as f:\n"
                    " w=csv.writer(f,lineterminator='\\n');w.writerow(['count','sum','mean']);"
                    "w.writerow([len(v),sum(v),sum(v)/len(v)])\n"
                    "print(len(v),sum(v),sum(v)/len(v))\n"
                )
                outputs = ["summary.csv"]
                if variant == "csv_chart":
                    code += (
                        "import matplotlib\nmatplotlib.use('Agg')\n"
                        "import matplotlib.pyplot as plt\n"
                        "f,a=plt.subplots(figsize=(4,3),dpi=100);a.bar(range(len(v)),v);"
                        "f.savefig('/work/outputs/chart.png');plt.close(f)\n"
                    )
                    outputs.append("chart.png")
            return plan(
                "sandbox.run_python", {"code": code, "inputs": [identifier], "outputs": outputs}
            )
        if variant == "proposal" and len(tools) == 1:
            return plan("update_item", {"item_id": "item-01", "status": "processing"})
        return final(
            {
                "count": 3,
                "total": 60,
                "mean": 20.0,
                "artifact_ids": [a["artifact_id"] for r in results for a in r["artifacts"]],
                "business_status": "denied" if variant == "proposal" else "not_requested",
            }
        )
