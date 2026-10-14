"""Real SDK approval → Docker CSV/PNG → same Agent → post-close authorized download.

The model is a deterministic metadata-driven fixture, not a live model evaluation.
Explicit Docker resources are required; ordinary package examples do not run this.
"""

import argparse
import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

from drov import Agent, ApprovalDecision, ResourceBinding, RunContext, Runtime, SQLiteStorage
from drov.artifacts import ArtifactInput, ArtifactRef, LocalArtifacts
from drov.config import ModelCapabilities
from drov.models.protocol import ModelComplete, ModelEvent, ModelRequest, ModelResponse, ToolCall
from drov.sandbox import DockerSandbox, PythonSandbox, SandboxResult
from drov.sandbox._png import validate_png

CSV = b"value\n12\n18\n30\n"
CODE = r"""
import csv,pathlib
values=[int(row['value']) for row in csv.DictReader(open(INPUT_PATH))]
count=len(values);total=sum(values);mean=total/count
with open('/work/outputs/summary.csv','w',newline='') as stream:
    writer=csv.writer(stream,lineterminator='\n')
    writer.writerow(['count','sum','mean']);writer.writerow([count,total,mean])
print(total)
"""
CHART = r"""
import numpy as np,pandas as pd,matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
frame=pd.read_csv(INPUT_PATH)
assert int(np.sum(frame['value'].to_numpy()))==total
figure,axis=plt.subplots(figsize=(4,3),dpi=100)
axis.bar(range(count),values);axis.set_ylabel('value');figure.tight_layout()
figure.savefig('/work/outputs/chart.png');plt.close(figure)
"""


class FixtureModel:
    capabilities = ModelCapabilities(
        text=True, tools=True, structured_output=True, streaming=True, context_window=32768
    )

    def __init__(self, chart: bool) -> None:
        self.chart = chart
        self.calls: list[ModelRequest] = []
        self.result: SandboxResult | None = None

    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls.append(request)
        if len(self.calls) == 1:
            identifiers: list[str] = []

            def visit(value: object) -> None:
                if isinstance(value, str) and value.startswith(
                    "Granted artifact descriptors (file data): "
                ):
                    visit(json.loads(value.split(": ", 1)[1]))
                if isinstance(value, dict):
                    if value.get("name") == "numbers.csv" and isinstance(
                        value.get("artifact_id"), str
                    ):
                        identifiers.append(value["artifact_id"])
                    for child in value.values():
                        visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)

            for message in request.messages:
                visit(message.content)
            assert len(set(identifiers)) == 1
            response = ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id="calculate",
                        name="sandbox.run_python",
                        arguments={
                            "code": "INPUT_PATH="
                            + repr("/work/inputs/" + identifiers[0] + "/numbers.csv")
                            + "\n"
                            + CODE
                            + (CHART if self.chart else ""),
                            "inputs": [identifiers[0]],
                            "outputs": ["summary.csv", "chart.png"]
                            if self.chart
                            else ["summary.csv"],
                        },
                    )
                ],
                finish_reason="tool_calls",
            )
        else:
            assert len(self.calls) == 2
            tool = next(message for message in request.messages if message.role == "tool")
            assert isinstance(tool.content, dict) and tool.content["status"] == "succeeded"
            self.result = SandboxResult.model_validate(tool.content["result"])
            assert self.result.exit_code == 0 and self.result.stdout == "60\n"
            assert self.result.artifact_status == "complete"
            response = ModelResponse(
                message="Actual sum is 60; delivered files: "
                + ", ".join(ref.artifact_id for ref in self.result.artifacts),
                finish_reason="completed",
            )
        yield ModelComplete(response=response)

    async def aclose(self) -> None:
        pass


async def check(args: argparse.Namespace) -> None:
    root: Path = args.directory.absolute()
    context = RunContext(tenant_id="fixture", user_id="alice")
    model = FixtureModel(args.chart)
    agent: Agent[str] = Agent(
        model=model, name="csv-sandbox", revision="1", tools=[PythonSandbox(backend="code")]
    )
    backend = DockerSandbox(image=args.image, docker=args.docker, host=args.host)
    files = [ArtifactInput(name="numbers.csv", data=CSV, media_type="text/csv")]
    async with Runtime(
        agents=[agent],
        storage=SQLiteStorage(root / "state.db"),
        artifacts=LocalArtifacts(directory=root / "bytes"),
        dependencies={"code": ResourceBinding.owned(backend)},
    ) as runtime:
        if args.stream:
            async with runtime.stream(
                agent,
                "Compute sum and deliver actual files",
                session_id="s",
                context=context,
                artifacts=files,
            ) as stream:
                async for _ in stream:
                    pass
                pending = await stream.result()
        else:
            pending = await runtime.run(
                agent,
                "Compute sum and deliver actual files",
                session_id="s",
                context=context,
                artifacts=files,
            )
        assert pending.status == "suspended" and pending.reason == "approval"
        assert pending.pending is not None
        result = await runtime.resume(
            pending.run_id,
            request_id=pending.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=context,
        )
        assert result.status == "completed" and model.result is not None
        refs = model.result.artifacts
        assert (
            await runtime.read_artifact(refs[0], context=context) == b"count,sum,mean\n3,60,20.0\n"
        )
    # A new Runtime has no code backend/Agent; reading does not create resources.
    downloaded: list[dict[str, object]] = []
    async with Runtime(
        agents=[],
        storage=SQLiteStorage(root / "state.db"),
        artifacts=LocalArtifacts(directory=root / "bytes"),
    ) as runtime:
        for ref in refs:
            data = await runtime.read_artifact(ref, context=context)
            assert hashlib.sha256(data).hexdigest() == ref.sha256
            info = await runtime.get_artifact(
                ref.artifact_id, context=context, include_content=True
            )
            item: dict[str, object] = {
                "name": info.name,
                "sha256": ref.sha256,
                "size_bytes": len(data),
                "media_type": ref.media_type,
            }
            if ref.media_type == "image/png":
                assert validate_png(data) == (400, 300)
                item["dimensions"] = [400, 300]
            downloaded.append(item)
    report = {
        "status": result.status,
        "model_kind": "deterministic fixture",
        "model_calls": len(model.calls),
        "files": downloaded,
        "input_sha256": hashlib.sha256(CSV).hexdigest(),
        "refs": [ArtifactRef.model_validate(ref).model_dump(mode="json") for ref in refs],
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--chart", action="store_true")
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    asyncio.run(check(args))


if __name__ == "__main__":
    main()
