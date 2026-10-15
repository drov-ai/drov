"""SDK boundary and Runtime semantics. Synthetic provider, not cloud isolation evidence."""

import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from drov import ApprovalDecision, PermissionPolicy, ResourceBinding, Runtime
from drov.artifacts import LocalArtifacts
from drov.errors import ConfigurationError, ShutdownIncomplete
from drov.integrations.e2b import E2BSandbox
from drov.sandbox._e2b_runner import RUN, SETUP
from drov.sandbox._leases import LeaseTransaction, SandboxLease
from drov.sandbox._runner import EXPORT
from drov.storage import SQLiteStorage
from tests.test_e2b_contracts import backend, lease_for
from tests.test_sandbox_runtime import CONTEXT, definition

sdk = pytest.importorskip("e2b")


class Cloud:
    def __init__(self) -> None:
        self.items: dict[str, Any] = {}
        self.creates = 0
        self.runs = 0
        self.kills = 0
        self.lose_create = False
        self.lose_kill = False
        self.fail_query = False
        self.request: str | None = None
        self.create_options: dict[str, Any] = {}

    async def ready(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            status="ready", template_id=args[0].template_id, build_id=args[0].build_id
        )

    async def create(self, reference: str, **opts: Any) -> Any:
        self.creates += 1
        self.create_options = opts
        now = datetime.now(UTC)
        info = SimpleNamespace(
            sandbox_id="cloud1",
            metadata=opts["metadata"],
            template_id=reference.split(":")[0],
            cpu_count=2,
            memory_mb=2048,
            allow_internet_access=False,
            network={"allow_public_traffic": False},
            lifecycle={"on_timeout": "kill", "auto_resume": False},
            volume_mounts=[],
            started_at=now,
            end_at=now + timedelta(seconds=opts["timeout"]),
        )
        self.items["cloud1"] = info
        if self.lose_create:
            raise RuntimeError("synthetic secret in provider error")
        return SimpleNamespace(
            sandbox_id="cloud1",
            get_info=lambda **_: self.info("cloud1"),
            files=SimpleNamespace(write=self.write),
        )

    async def write(self, path: str, data: str, **opts: Any) -> None:
        assert path == "/opt/drov-job/request.json" and opts == {"user": "root"}
        self.request = data

    async def info(self, resource_id: str, **opts: Any) -> Any:
        if self.fail_query:
            raise RuntimeError("synthetic secret in provider error")
        if resource_id not in self.items:
            raise sdk.SandboxNotFoundException("absent")
        return self.items[resource_id]

    async def kill(self, resource_id: str, **opts: Any) -> bool:
        self.kills += 1
        self.items.pop(resource_id, None)
        if self.lose_kill:
            raise RuntimeError("synthetic lost kill reply")
        return True

    def list(self, **opts: Any) -> Any:
        async def page() -> list[Any]:
            result.has_next = False
            return list(self.items.values())

        result = SimpleNamespace(has_next=True, next_items=page)
        return result

    async def program(self, sandbox: Any, program: str, *, argument: str | None = None) -> str:
        if program == SETUP:
            return json.dumps(
                dict(
                    uid=0,
                    workspace_bytes=64 * 1024**2,
                    cpu_count=2,
                    packages={"numpy": "2.4.6", "pandas": "3.0.6", "matplotlib": "3.11.2"},
                )
            )
        if program == RUN:
            self.runs += 1
            return json.dumps(
                dict(
                    exit_code=0,
                    termination="exited",
                    stdout="60\n",
                    stderr="",
                    stdout_truncated=False,
                    stderr_truncated=False,
                )
            )
        assert program == EXPORT
        data = b"value\n60\n"
        return json.dumps(
            {
                "files": [
                    {
                        "name": "result.csv",
                        "data": base64.b64encode(data).decode(),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                ]
            }
        )


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> Cloud:
    fixture = Cloud()
    monkeypatch.setattr(sdk.AsyncTemplate, "get_build_status", fixture.ready)
    monkeypatch.setattr(sdk.AsyncSandbox, "create", fixture.create)
    monkeypatch.setattr(sdk.AsyncSandbox, "get_info", fixture.info)
    monkeypatch.setattr(sdk.AsyncSandbox, "kill", fixture.kill)
    monkeypatch.setattr(sdk.AsyncSandbox, "list", fixture.list)
    monkeypatch.setattr(E2BSandbox, "_program", lambda self, *a, **k: fixture.program(*a, **k))
    return fixture


def runtime_for(directory: Path, agent: Any, value: E2BSandbox, *, allow: bool = False) -> Runtime:
    if os.name != "posix":
        pytest.skip("ArtifactStore requires POSIX")
    return Runtime(
        agents=[agent],
        storage=SQLiteStorage(directory / "state.db"),
        artifacts=LocalArtifacts(directory=directory / "bytes"),
        dependencies={"code": ResourceBinding.owned(value)},
        policy=PermissionPolicy(allow={"sandbox.run_python"} if allow else set()),
    )


@pytest.mark.asyncio
async def test_same_approval_result_download_and_budget_protocol(
    tmp_path: Path, cloud: Cloud
) -> None:
    value = backend()
    agent, model = definition()
    async with runtime_for(tmp_path, agent, value) as runtime:
        pending = await runtime.run(agent, "compute", session_id="s", context=CONTEXT)
        assert pending.status == "suspended" and pending.pending is not None and cloud.creates == 0
        result = await runtime.resume(
            pending.run_id,
            request_id=pending.pending.request_id,
            response=ApprovalDecision(approved=True),
            context=CONTEXT,
        )
        assert result.status == "completed" and cloud.creates == cloud.runs == cloud.kills == 1
        state = await runtime.storage.get_run(CONTEXT, result.run_id)
        async with runtime.storage.transaction() as tx:
            lease = await LeaseTransaction(tx).get(
                lease_for(value).owner, result.run_id, state.state.operations[0].operation_id
            )
        assert (
            lease is not None and lease.destroyed and not lease.active and lease.remote_start_intent
        )
        assert lease.remote_id == "cloud1" and lease.container_id is None
        assert "synthetic-test-credential" not in lease.model_dump_json()
        assert (
            cloud.create_options["retries"] == 0
            and cloud.create_options["allow_internet_access"] is False
        )
        assert cloud.create_options["envs"] == {}
        assert len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["lose_create", "lose_kill"])
async def test_lost_reply_cleans_original_without_recreate(
    tmp_path: Path, cloud: Cloud, failure: str
) -> None:
    setattr(cloud, failure, True)
    agent, _ = definition()
    value = backend()
    runtime = runtime_for(tmp_path, agent, value, allow=True)
    await runtime.open()
    try:
        result = await runtime.run(agent, "compute", session_id="s", context=CONTEXT)
        assert cloud.creates == 1
        if failure == "lose_kill":
            assert result.status == "suspended"
            assert runtime._sandboxes is not None
            with pytest.raises(ShutdownIncomplete):
                await runtime._sandboxes.aclose()
            cloud.lose_kill = False
            await runtime.recover(result.run_id, context=CONTEXT)
        assert not cloud.items
    finally:
        cloud.lose_create = cloud.lose_kill = False
        await runtime.aclose()
    assert cloud.creates == 1


@pytest.mark.asyncio
async def test_absent_unacknowledged_create_stays_unknown(cloud: Cloud) -> None:
    async with backend() as value:
        lease = lease_for(value)

        async def fact(patch: dict[str, Any]) -> SandboxLease:
            pytest.fail("absence fabricated a destruction fact")

        with pytest.raises(ConfigurationError, match="absence does not prove"):
            await value.destroy(lease, fact)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,wrong",
    [
        ("metadata", {}),
        ("template_id", "other"),
        ("cpu_count", 8),
        ("memory_mb", 512),
        ("allow_internet_access", True),
        ("network", {"allow_public_traffic": True}),
        ("lifecycle", {"on_timeout": "pause"}),
        ("volume_mounts", [{"name": "unexpected"}]),
    ],
)
async def test_actual_provider_controls_are_verified(cloud: Cloud, field: str, wrong: Any) -> None:
    async with backend() as value:
        lease = lease_for(value)
        obj = await cloud.create(
            "fixture-template:build", metadata=value._metadata(lease), timeout=180
        )
        setattr(cloud.items[obj.sandbox_id], field, wrong)
        lease = lease.model_copy(update={"remote_id": obj.sandbox_id})
        with pytest.raises(ConfigurationError):
            value._check_info(cloud.items[obj.sandbox_id], lease)


@pytest.mark.asyncio
async def test_budget_denial_never_creates_remote_resource(tmp_path: Path, cloud: Cloud) -> None:
    agent, _ = definition()
    async with runtime_for(tmp_path, agent, backend(max_cost_micro_usd=1), allow=True) as runtime:
        result = await runtime.run(agent, "compute", session_id="s", context=CONTEXT)
        state = await runtime.storage.get_run(CONTEXT, result.run_id)
        assert state.state.operations[0].status == "not_executed"
        assert cloud.creates == cloud.runs == 0


@pytest.mark.asyncio
async def test_provider_errors_are_redacted(cloud: Cloud) -> None:
    async with backend() as value:
        lease = lease_for(value).model_copy(update={"remote_id": "cloud1"})
        cloud.fail_query = True
        with pytest.raises(ConfigurationError) as caught:
            await value.resource(lease)
        assert "synthetic secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_credential_resolution_failure_is_redacted() -> None:
    def unavailable(_: str) -> str:
        raise ValueError("synthetic secret")

    value = backend(resolve_credential=unavailable)
    with pytest.raises(ConfigurationError):
        await value.open()


@pytest.mark.asyncio
async def test_cost_reservations_survive_close_and_are_not_refunded(
    tmp_path: Path, cloud: Cloud
) -> None:
    reservation = backend().reservation_micro_usd
    for index in range(2):
        agent, _ = definition()
        value = backend(max_cost_micro_usd=reservation)
        async with runtime_for(tmp_path, agent, value, allow=True) as runtime:
            result = await runtime.run(
                agent, "budget fixture", session_id=str(index), context=CONTEXT
            )
            record = await runtime.storage.get_run(CONTEXT, result.run_id)
            assert record.state.operations[0].status == (
                "succeeded" if index == 0 else "not_executed"
            )
    assert cloud.creates == cloud.runs == 1


@pytest.mark.asyncio
async def test_borrowed_backend_remains_open_and_is_exclusive(tmp_path: Path, cloud: Cloud) -> None:
    agent, _ = definition()
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    async with backend() as value:
        first = runtime_for(tmp_path / "first", agent, value)
        first._dependency_bindings["code"] = ResourceBinding.borrowed(value)
        second = runtime_for(tmp_path / "second", agent, value)
        second._dependency_bindings["code"] = ResourceBinding.borrowed(value)
        async with first:
            with pytest.raises(ConfigurationError, match="another Runtime"):
                await second.open()
        assert value.is_open and cloud.creates == 0


@pytest.mark.asyncio
async def test_invalid_controls_refuse_execution_but_allow_owned_cleanup(
    tmp_path: Path, cloud: Cloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = cloud.create

    async def invalid(*args: Any, **opts: Any) -> Any:
        result = await original(*args, **opts)
        cloud.items[result.sandbox_id].network = {"allow_public_traffic": True}
        return result

    monkeypatch.setattr(sdk.AsyncSandbox, "create", invalid)
    agent, _ = definition()
    async with runtime_for(tmp_path, agent, backend(), allow=True) as runtime:
        outcome = await runtime.run(agent, "invalid controls", session_id="s", context=CONTEXT)
        run = await runtime.storage.get_run(CONTEXT, outcome.run_id)
        assert run.state.operations[0].status == "failed_known"
    assert cloud.creates == cloud.kills == 1 and cloud.runs == 0 and not cloud.items
