"""Real SDK Docker acceptance. Explicit non-secret config; core CI skips this group."""

import asyncio
import json
import os
from pathlib import Path
from typing import cast

import pytest

from drov import (
    Agent,
    ApprovalDecision,
    Limits,
    PermissionPolicy,
    ResourceBinding,
    RunContext,
    Runtime,
)
from drov._json import StrictJson
from drov.artifacts import ArtifactLimits, LocalArtifacts
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse, ToolCall
from drov.sandbox import DockerSandbox, PythonSandbox, SandboxLimits, SandboxResult
from drov.sandbox._engine import UnixEngine
from drov.sandbox._leases import LeaseTransaction
from drov.storage import SQLiteStorage
from drov.storage.records import MemoryScope

pytestmark = pytest.mark.docker
CONTEXT = RunContext(tenant_id="fixture", user_id="alice")


@pytest.fixture
def config() -> dict[str, str]:
    path = os.environ.get("DROV_DOCKER_TEST_CONFIG")
    if not path:
        if os.environ.get("DROV_REQUIRE_DOCKER") == "1":
            pytest.fail("Docker acceptance requires its explicit non-secret config")
        pytest.skip("Explicit local Docker acceptance config not provided")
    record = json.loads(Path(path).read_text())
    return {"docker": record["docker_cli"], "host": record["docker_host"], "image": record["image"]}


def model_for(code: str, outputs: list[str]) -> ScriptedModel:
    return ScriptedModel(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id="original",
                        name="sandbox.run_python",
                        arguments={"code": code, "outputs": cast(StrictJson, outputs)},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ModelResponse(message="Observed result returned", finish_reason="completed"),
        ]
    )


def create_runtime(
    root: Path,
    model: ScriptedModel,
    config: dict[str, str],
    *,
    allow: bool = True,
    file_limits: ArtifactLimits | None = None,
    limits: SandboxLimits | None = None,
    run_limits: Limits | None = None,
) -> tuple[Runtime, Agent[str], DockerSandbox]:
    agent: Agent[str] = Agent(
        model=model,
        name="docker-acceptance",
        revision="1",
        tools=[PythonSandbox(backend="code")],
        limits=run_limits,
    )
    backend = DockerSandbox(**config, limits=limits)
    runtime = Runtime(
        agents=[agent],
        storage=SQLiteStorage(root / "state.db"),
        artifacts=LocalArtifacts(directory=root / "bytes", limits=file_limits),
        dependencies={"code": ResourceBinding.owned(backend)},
        policy=PermissionPolicy(allow={"sandbox.run_python"} if allow else set()),
    )
    return runtime, agent, backend


async def observed(runtime: Runtime, run_id: str) -> SandboxResult:
    inspection = await runtime.inspect(run_id, context=CONTEXT, include_content=True)
    event = next(item for item in inspection.events if item.type == "tool.succeeded")
    return SandboxResult.model_validate(event.data["result"])


@pytest.mark.asyncio
async def test_real_approval_before_create_and_two_independent_runs_with_explicit_reuse(
    tmp_path: Path, config: dict[str, str]
) -> None:
    first_code = (
        "from pathlib import Path; assert not Path('/work/sentinel').exists(); "
        "Path('/work/sentinel').write_text('private'); "
        "Path('/work/outputs/result.csv').write_text('value\\n60\\n'); print(60)"
    )
    second = ModelResponse(
        tool_calls=[
            ToolCall(
                call_id="reuse",
                name="sandbox.run_python",
                arguments={"code": "pending", "outputs": ["reused.csv"]},
            )
        ],
        finish_reason="tool_calls",
    )
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id="first",
                        name="sandbox.run_python",
                        arguments={"code": first_code, "outputs": ["result.csv"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ModelResponse(message="first observed", finish_reason="completed"),
            second,
            ModelResponse(message="second observed", finish_reason="completed"),
        ]
    )
    runtime, agent, backend = create_runtime(tmp_path, model, config, allow=False)
    async with runtime:
        pending = await runtime.run(agent, "first", session_id="first", context=CONTEXT)
        assert pending.status == "suspended" and pending.pending is not None
        async with runtime.storage.transaction() as tx:
            run = await tx.get_run(CONTEXT, pending.run_id)
            lease = await LeaseTransaction(tx).get(
                MemoryScope(**CONTEXT.model_dump(), agent_name="docker-acceptance"),
                pending.run_id,
                run.state.operations[0].operation_id,
            )
        assert (
            lease is not None and not lease.create_intent and await backend.resource(lease) is None
        )
        done = await runtime.resume(
            pending.run_id,
            request_id=pending.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=CONTEXT,
        )
        assert done.status == "completed"
        first = await observed(runtime, done.run_id)
        assert first.artifact_status == "complete" and first.exit_code == 0
        ref = first.artifacts[0]
        assert await runtime.read_artifact(ref, context=CONTEXT) == b"value\n60\n"
        second.tool_calls[0].arguments.update(
            {
                "inputs": [ref.artifact_id],
                "code": "from pathlib import Path; assert not Path('/work/sentinel').exists(); "
                + "data=Path("
                + repr("/work/inputs/" + ref.artifact_id + "/result.csv")
                + ").read_bytes(); "
                + "assert data==b'value\\n60\\n'; "
                + "Path('/work/outputs/reused.csv').write_bytes(data); print(60)",
            }
        )
        pending2 = await runtime.run(
            agent,
            "reuse only granted result",
            session_id="second",
            context=CONTEXT,
            artifacts=[ref],
        )
        assert pending2.status == "suspended" and pending2.pending is not None
        done2 = await runtime.resume(
            pending2.run_id,
            request_id=pending2.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=CONTEXT,
        )
        assert done2.status == "completed"
        reused = await observed(runtime, done2.run_id)
        assert reused.exit_code == 0 and reused.artifacts[0].sha256 == ref.sha256
        assert reused.execution_key != first.execution_key and len(model.calls) == 4
        assert await runtime.read_artifact(reused.artifacts[0], context=CONTEXT) == b"value\n60\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,outputs",
    [
        ("pass", ["missing.csv"]),
        ("import os; os.symlink('/etc/passwd','/work/outputs/link.csv')", ["link.csv"]),
        (
            "import os; open('/work/outputs/a','w').write('x');"
            "os.link('/work/outputs/a','/work/outputs/b')",
            ["a"],
        ),
        ("import os; os.mkfifo('/work/outputs/fifo')", ["fifo"]),
        ("open('/work/outputs/large','w').write('x'*40)", ["large"]),
        ("open('/work/outputs/fake.png','wb').write(b'\\x89PNG\\r\\n\\x1a\\n')", ["fake.png"]),
    ],
)
async def test_real_invalid_exports_preserve_exit_but_publish_no_references(
    tmp_path: Path, config: dict[str, str], code: str, outputs: list[str]
) -> None:
    runtime, agent, _ = create_runtime(
        tmp_path,
        model_for(code, outputs),
        config,
        file_limits=ArtifactLimits(max_artifact_bytes=32, max_export_bytes=64),
    )
    async with runtime:
        done = await runtime.run(agent, "invalid export", session_id="s", context=CONTEXT)
        assert done.status == "completed"
        result = await observed(runtime, done.run_id)
        assert result.exit_code == 0 and result.termination == "exited"
        assert (
            result.artifact_status == "rejected"
            and result.artifacts == []
            and result.artifact_error is not None
        )


@pytest.mark.asyncio
async def test_real_code_exception_and_output_limit_are_actual_results(
    tmp_path: Path, config: dict[str, str]
) -> None:
    model = model_for("raise ValueError('synthetic failure')", [])
    runtime, agent, _ = create_runtime(tmp_path, model, config)
    async with runtime:
        done = await runtime.run(agent, "exception", session_id="s", context=CONTEXT)
        result = await observed(runtime, done.run_id)
        assert (
            result.exit_code == 1
            and result.termination == "exited"
            and "ValueError: synthetic failure" in result.stderr
        )
    other = tmp_path / "bounded"
    other.mkdir()
    runtime, agent, _ = create_runtime(
        other,
        model_for("import os;os.write(1,b'x'*100000)", []),
        config,
        limits=SandboxLimits(max_output_bytes=32),
    )
    async with runtime:
        done = await runtime.run(agent, "bounded output", session_id="s", context=CONTEXT)
        result = await observed(runtime, done.run_id)
        assert (
            result.termination == "output_limit"
            and result.stdout_truncated
            and len(result.stdout.encode()) == 32
        )


@pytest.mark.asyncio
async def test_real_cancel_destroys_original_environment_without_final_model(
    tmp_path: Path, config: dict[str, str]
) -> None:
    model = model_for(
        "import os,time;\nif os.fork()==0: os.setsid()\nprint('started',flush=True);time.sleep(30)",
        [],
    )
    runtime, agent, backend = create_runtime(tmp_path, model, config)
    async with runtime:
        ref = await runtime.start(agent, "cancel actual code", session_id="s", context=CONTEXT)
        lease = None
        for _ in range(500):
            async with runtime.storage.transaction() as tx:
                run = await tx.get_run(CONTEXT, ref.run_id)
                if run.state.operations:
                    lease = await LeaseTransaction(tx).get(
                        MemoryScope(**CONTEXT.model_dump(), agent_name="docker-acceptance"),
                        ref.run_id,
                        run.state.operations[0].operation_id,
                    )
            if lease is not None and lease.exec_start_intent and lease.exec_id is not None:
                state = await UnixEngine(config["host"], control_seconds=5).inspect_exec(
                    lease.exec_id
                )
                if state["Running"] is True:
                    break
            await asyncio.sleep(0.01)
        assert lease is not None and lease.exec_start_intent
        assert lease.container_id is not None
        engine = UnixEngine(config["host"], control_seconds=5)
        program = (
            "import pathlib\ncount=0\n"
            "for path in pathlib.Path('/proc').glob('[0-9]*/status'):\n"
            "    try: rows=path.read_text().splitlines()\n"
            "    except FileNotFoundError: continue\n"
            "    uid=next(row for row in rows if row.startswith('Uid:')).split()[1]\n"
            "    if uid=='65533': count+=1\n"
            "print(count)"
        )
        async with asyncio.timeout(5):
            while True:
                probe = await engine.create_exec(lease.container_id, program, control=True)
                response = await engine.start_exec(probe, seconds=2, max_bytes=32)
                if int(response.stdout) >= 3:  # wrapper, original child and detached child
                    break
                await asyncio.sleep(0.01)
        await runtime.cancel(ref.run_id, context=CONTEXT)
        final = await runtime.wait(ref.run_id, context=CONTEXT)
        assert final.status == "cancelled" and len(model.calls) == 1
        async with runtime.storage.transaction() as tx:
            lease = await LeaseTransaction(tx).get(lease.owner, ref.run_id, lease.operation_id)
        assert lease is not None and lease.destroyed and not lease.active
        assert await backend.resource(lease) is None
