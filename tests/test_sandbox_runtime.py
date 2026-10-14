"""SDK/SQLite semantics with a labeled backend fixture, not real code/isolator quality."""

import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from drov import Agent, ApprovalDecision, PermissionPolicy, ResourceBinding, RunContext, Runtime
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.errors import (
    ConfigurationError,
    InteractionConflict,
    PersistenceUnavailable,
    RecordNotFound,
)
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse, ToolCall
from drov.sandbox import PythonSandbox, SandboxLimits, SandboxResult
from drov.sandbox._backend import DockerBackend, FactWriter
from drov.sandbox._leases import LeaseTransaction, SandboxLease
from drov.sandbox._profile import ExecutionProfile
from drov.storage import SQLiteStorage
from drov.storage.records import MemoryScope
from drov.storage.sqlite import SQLiteDriver
from drov.types import ApprovalRequest, ReconciliationDecision

CONTEXT = RunContext(tenant_id="fixture", user_id="alice")
CODE = "print(60)"


class BackendFixture(DockerBackend):
    def __init__(self) -> None:
        super().__init__(
            image="sha256:" + "a" * 64,
            docker=Path(sys.executable),
            host="unix:///fixture-not-a-daemon.sock",
        )
        self.executions: list[tuple[str, str]] = []
        self.destroyed: set[str] = set()
        self.opens = 0
        self.fail_cleanup = False

    async def open(self) -> None:
        self._require_loop()
        self.opens += 1
        self._profile = ExecutionProfile("sha256:" + "a" * 64, "arm64", SandboxLimits())
        self._binding = {"fixture_protocol_only": True, "profile": 1}
        self._state = "open"

    async def revalidate(self) -> None:
        assert self.is_open

    async def execute(
        self,
        lease: SandboxLease,
        code: str,
        *,
        inputs: Any,
        file_limits: Any,
        fact: FactWriter,
        seconds: float,
    ) -> tuple[SandboxLease, list[ArtifactInput]]:
        assert code == CODE and lease.code_hash == hashlib.sha256(code.encode()).hexdigest()
        assert lease.active and lease.create_intent
        self.executions.append((lease.execution_key, code))
        lease = await fact({"container_id": "a" * 64})
        lease = await fact({"exec_create_intent": True})
        lease = await fact({"exec_id": "b" * 64})
        lease = await fact({"exec_start_intent": True})
        lease = await fact(
            {
                "observation": {
                    "execution_key": lease.execution_key,
                    "exit_code": 0,
                    "termination": "exited",
                    "stdout": "60\n",
                    "stderr": "",
                    "stdout_truncated": False,
                    "stderr_truncated": False,
                    "artifact_status": "complete",
                    "artifact_error": None,
                }
            }
        )
        return lease, [ArtifactInput(name="result.csv", data=b"value\n60\n", media_type="text/csv")]

    async def destroy(self, lease: SandboxLease, fact: FactWriter) -> SandboxLease:
        if self.fail_cleanup:
            raise ConfigurationError("Fixture cleanup acknowledgement lost")
        if lease.destroyed:
            return lease
        self.destroyed.add(lease.execution_key)
        return await fact({"destroyed": True})

    async def resource(self, lease: SandboxLease) -> dict[str, Any] | None:
        return None if lease.destroyed else {"State": {"Running": True}}


def definition(arguments: dict[str, Any] | None = None) -> tuple[Agent[str], ScriptedModel]:
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id="original",
                        name="sandbox.run_python",
                        arguments=arguments
                        if arguments is not None
                        else {"code": CODE, "outputs": ["result.csv"]},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ModelResponse(message="Fixture final response", finish_reason="completed"),
        ]
    )
    agent: Agent[str] = Agent(
        model=model, name="sandbox-fixture", revision="1", tools=[PythonSandbox(backend="code")]
    )
    return agent, model


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    if os.name != "posix":
        pytest.skip("SDK sandbox ArtifactStore currently requires POSIX private files")
    return tmp_path


def runtime_for(
    directory: Path,
    agent: Agent[str],
    backend: BackendFixture,
    *,
    allow: bool = False,
    deny: bool = False,
) -> Runtime:
    return Runtime(
        agents=[agent],
        storage=SQLiteStorage(directory / "state.db"),
        artifacts=LocalArtifacts(directory=directory / "bytes"),
        dependencies={"code": ResourceBinding.owned(backend)},
        policy=PermissionPolicy(
            allow={"sandbox.run_python"} if allow else set(),
            deny={"sandbox.run_python"} if deny else set(),
        ),
    )


@pytest.mark.asyncio
async def test_default_ask_binds_original_plan_and_publishes_after_destroy(directory: Path) -> None:
    agent, model = definition()
    backend = BackendFixture()
    async with runtime_for(directory, agent, backend) as runtime:
        paused = await runtime.run(agent, "compute", session_id="one", context=CONTEXT)
        assert paused.status == "suspended" and paused.pending is not None
        assert isinstance(paused.pending, ApprovalRequest)
        assert isinstance(paused.pending.arguments_summary, dict)
        assert backend.executions == [] and len(model.calls) == 1
        assert paused.pending.arguments_summary["code"] == CODE
        assert paused.pending.arguments_summary["_backend_hash"] == backend.config_hash
        first = await runtime.respond(
            paused.run_id,
            request_id=paused.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=CONTEXT,
        )
        assert (
            await runtime.respond(
                paused.run_id,
                request_id=paused.pending.request_id,
                response=ApprovalDecision(approved=True),
                context=CONTEXT,
            )
            == first
        )
        done = await runtime.wait(paused.run_id, context=CONTEXT)
        assert done.status == "completed" and len(model.calls) == 2
        inspected = await runtime.inspect(paused.run_id, context=CONTEXT, include_content=True)
        event = next(item for item in inspected.events if item.type == "tool.succeeded")
        result = SandboxResult.model_validate(event.data["result"])
        assert result.execution_key in backend.destroyed and len(backend.executions) == 1
        assert await runtime.read_artifact(result.artifacts[0], context=CONTEXT) == b"value\n60\n"
        checkpoint = await runtime.storage.get_checkpoint(CONTEXT, paused.run_id)
        assert checkpoint.data["tool_dispatch_count"] == 1
    async with Runtime(
        agents=[],
        storage=SQLiteStorage(directory / "state.db"),
        artifacts=LocalArtifacts(directory=directory / "bytes"),
    ) as reopened:
        assert await reopened.read_artifact(result.artifacts[0], context=CONTEXT) == b"value\n60\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("deny", [False, True])
async def test_denial_and_rejected_approval_create_nothing(directory: Path, deny: bool) -> None:
    agent, _ = definition()
    backend = BackendFixture()
    async with runtime_for(directory, agent, backend, deny=deny) as runtime:
        result = await runtime.run(agent, "compute", session_id="one", context=CONTEXT)
        if not deny:
            assert result.status == "suspended" and result.pending is not None
            result = await runtime.resume(
                result.run_id,
                request_id=result.pending.request_id,
                response=ApprovalDecision(approved=False),
                context=CONTEXT,
            )
        assert result.status == "completed" and backend.executions == []
        assert (await runtime.storage.get_checkpoint(CONTEXT, result.run_id)).data.get(
            "tool_dispatch_count", 0
        ) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"code": ""},
        {"code": CODE, "image": "arbitrary"},
        {"code": CODE, "inputs": ["art_" + "a" * 32]},
        {"code": CODE, "outputs": ["../outside"]},
        {"code": CODE, "outputs": ["/absolute"]},
        {"code": CODE, "outputs": ["a/result.csv", "b/result.csv"]},
        {"code": CODE, "outputs": ["x\\y"]},
    ],
)
async def test_invalid_or_ungranted_plan_creates_nothing(
    directory: Path, arguments: dict[str, Any]
) -> None:
    agent, _ = definition(arguments)
    backend = BackendFixture()
    async with runtime_for(directory, agent, backend, allow=True) as runtime:
        result = await runtime.run(agent, "compute", session_id="one", context=CONTEXT)
        assert result.status == "completed" and backend.executions == []
        assert (await runtime.storage.get_checkpoint(CONTEXT, result.run_id)).data.get(
            "tool_dispatch_count", 0
        ) == 0


@pytest.mark.asyncio
async def test_publication_rolls_back_and_recovery_uses_original_staged_result(
    directory: Path,
) -> None:
    agent, model = definition()
    backend = BackendFixture()
    async with runtime_for(directory, agent, backend, allow=True) as runtime:
        async with runtime.storage.transaction() as tx:
            assert isinstance(tx._driver, SQLiteDriver)
            await tx._driver.execute("""CREATE TRIGGER reject_sandbox_result
                BEFORE UPDATE ON records
                WHEN NEW.kind='operation' AND json_extract(NEW.payload,'$.data.status')='succeeded'
                BEGIN SELECT RAISE(ABORT,'fixture publication failure'); END""")
        with pytest.raises(PersistenceUnavailable):
            await runtime.run(agent, "compute", session_id="one", context=CONTEXT)
        assert len(backend.executions) == 1 and len(model.calls) == 1
        async with runtime.storage.transaction() as tx:
            assert isinstance(tx._driver, SQLiteDriver)
            runs = await tx._unfinished_runs()
            assert len(runs) == 1
            record = runs[0]
            rows, _ = await tx._driver.execute("SELECT count(*) FROM artifact_records")
            assert rows[0][0] == 0
            owner = MemoryScope(tenant_id="fixture", user_id="alice", agent_name="sandbox-fixture")
            lease = await LeaseTransaction(tx).get(
                owner, record.state.run_id, record.state.operations[0].operation_id
            )
            assert lease is not None and lease.destroyed and lease.active
            await tx._driver.execute("DROP TRIGGER reject_sandbox_result")
        stage = await runtime._files.store()._snapshot(lease.stage_id or "")
        with pytest.raises(RecordNotFound):
            await runtime.read_artifact(stage.files[0].ref, context=CONTEXT)
        recovered = await runtime.recover(record.state.run_id, context=CONTEXT)
        assert recovered.status == "completed" and len(backend.executions) == 1
        assert len(model.calls) == 2
        assert await runtime.read_artifact(stage.files[0].ref, context=CONTEXT) == b"value\n60\n"


@pytest.mark.asyncio
async def test_temporary_sandbox_rejects_before_model_or_resource() -> None:
    agent, model = definition()
    with pytest.raises(ConfigurationError, match="persistent Runtime"):
        await agent.run("compute")
    assert model.calls == []


@pytest.mark.asyncio
async def test_unknown_cleanup_cannot_be_replaced_by_human_result_and_verify_is_readonly(
    directory: Path,
) -> None:
    agent, model = definition()
    backend = BackendFixture()
    backend.fail_cleanup = True
    async with runtime_for(directory, agent, backend, allow=True) as runtime:
        pending = await runtime.run(agent, "compute", session_id="s", context=CONTEXT)
        assert (
            pending.status == "suspended"
            and pending.reason == "reconciliation"
            and pending.pending is not None
        )
        with pytest.raises(InteractionConflict, match="recover"):
            await runtime.respond(
                pending.run_id,
                request_id=pending.pending.request_id,
                response=ReconciliationDecision(
                    resolution="applied",
                    evidence="Cannot replace actual cleanup",
                    result={"forged": True},
                ),
                context=CONTEXT,
            )
        verified = await runtime.verify(
            pending.run_id, request_id=pending.pending.request_id, context=CONTEXT
        )
        assert verified.resolution == "unresolved" and backend.destroyed == set()
        assert len(backend.executions) == 1 and len(model.calls) == 1
        backend.fail_cleanup = False
        final = await runtime.recover(pending.run_id, context=CONTEXT)
        assert final.status == "completed" and len(backend.executions) == 1
