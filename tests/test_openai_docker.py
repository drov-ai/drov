"""Loopback Responses -> actual Docker -> artifact -> same adapter, no real model."""

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("openai")

from drov import Agent, ApprovalDecision, ResourceBinding, Runtime
from drov.artifacts import LocalArtifacts
from drov.models.protocol import ModelTool
from drov.sandbox import DockerSandbox, PythonSandbox
from drov.storage import SQLiteStorage
from tests.test_openai_responses import Reply, call, completed, fixture, request, response
from tests.test_sandbox_docker import CONTEXT, observed
from tests.test_sandbox_docker import config as config
from tests.test_sandbox_isolation import check_clean

pytestmark = pytest.mark.docker


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_responses_approval_actual_python_and_persistent_download(
    tmp_path: Path, config: dict[str, str], stream: bool
) -> None:
    sandbox = PythonSandbox(backend="code")
    async with fixture([Reply(), Reply(completed())]) as (server, _, adapter):
        wire = adapter._body(
            request(
                tools=[
                    ModelTool(
                        name=sandbox.name,
                        description=sandbox.description,
                        parameters=sandbox.parameters,
                    )
                ]
            )
        )["tools"][0]["name"]
        arguments = json.dumps(
            {
                "code": "from pathlib import Path; "
                "Path('/work/outputs/result.csv').write_text('value\\n60\\n'); print(60)",
                "outputs": ["result.csv"],
            }
        )
        server.replies[0].body = completed(response([call(wire, arguments)]))
        agent: Agent[str] = Agent(
            model=adapter, name="docker-acceptance", revision="1", tools=[sandbox]
        )
        backend = DockerSandbox(image=config["image"], docker=config["docker"], host=config["host"])
        async with Runtime(
            agents=[agent],
            storage=SQLiteStorage(tmp_path / "state.db"),
            artifacts=LocalArtifacts(directory=tmp_path / "bytes"),
            dependencies={"code": ResourceBinding.owned(backend)},
        ) as runtime:
            if stream:
                async with runtime.stream(
                    agent, "compute", session_id="s", context=CONTEXT
                ) as subscription:
                    async for _ in subscription:
                        pass
                    paused = await subscription.result()
            else:
                paused = await runtime.run(agent, "compute", session_id="s", context=CONTEXT)
            assert paused.status == "suspended" and paused.pending is not None
            assert len(server.requests) == 1
            await runtime.respond(
                paused.run_id,
                request_id=paused.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=CONTEXT,
            )
            done = await runtime.wait(paused.run_id, context=CONTEXT)
            assert done.status == "completed" and done.output == "hello"
            result = await observed(runtime, done.run_id)
            assert result.exit_code == 0 and result.stdout == "60\n"
            data = await runtime.read_artifact(result.artifacts[0], context=CONTEXT)
            assert data == b"value\n60\n"
            assert hashlib.sha256(data).hexdigest() == result.artifacts[0].sha256
            await check_clean(runtime, backend, done.run_id)
            assert len(server.requests) == 2
