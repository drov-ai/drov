"""Core-only, inert optional construction and durable backend compatibility."""

import ast
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from drov.errors import ConfigurationError
from drov.integrations.e2b import E2BLimits, E2BSandbox
from drov.sandbox._e2b_runner import RUN, SETUP
from drov.sandbox._leases import SandboxLease
from drov.storage.records import MemoryScope


def backend(**changes: Any) -> E2BSandbox:
    options: dict[str, Any] = dict(
        template_id="fixture-template",
        build_id="347a1104-f90d-4644-8024-12fc30619da0",
        credential_ref="host-only",
        resolve_credential=lambda _: "synthetic-test-credential",
        region="project-default",
        cloud_data_policy="allow",
        budget_id="fixture-budget",
        max_cost_micro_usd=100_000,
        rate_micro_usd_per_second=100,
    )
    return E2BSandbox(**(options | changes))


def lease_for(value: E2BSandbox, code: str = "print(60)") -> SandboxLease:
    import hashlib
    from uuid import uuid4

    return SandboxLease(
        execution_key=uuid4().hex,
        owner=MemoryScope(tenant_id="fixture", user_id="alice", agent_name="sandbox-fixture"),
        run_id="run1",
        operation_id="op1",
        backend_name="code",
        backend_hash=value.config_hash,
        backend_config=value.binding,
        code_hash=hashlib.sha256(code.encode()).hexdigest(),
        active=True,
        create_intent=True,
    )


def test_construction_never_resolves_credentials_or_imports_sdk() -> None:
    with patch("importlib.import_module", side_effect=AssertionError("import")):
        resource = backend(resolve_credential=lambda _: pytest.fail("credential read"))
        assert not resource.is_open
    for program in (RUN, SETUP):
        ast.parse(program)


@pytest.mark.parametrize(
    "changes",
    [
        {"build_id": "latest"},
        {"build_id": "default"},
        {"template_id": "../../template"},
        {"credential_ref": "contains spaces"},
        {"cloud_data_policy": "deny"},
        {"require_region_attestation": True},
        {"max_cost_micro_usd": 0},
        {"max_cost_micro_usd": True},
        {"rate_micro_usd_per_second": 36},
        {"rate_micro_usd_per_second": True},
    ],
)
def test_unsupported_or_unauthorized_profiles_fail_without_io(changes: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError):
        backend(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_seconds": 0},
        {"execution_seconds": 61},
        {"execution_seconds": 30},
        {"control_seconds": 0},
        {"lifetime_seconds": 0},
        {"lifetime_seconds": 301},
        {"max_code_bytes": 65537},
        {"max_output_bytes": 65537},
        {"concurrency": 2},
        {"memory_bytes": 1},
        {"cpus": 0.1},
        {"pids": 1000},
    ],
)
def test_hard_profile_cannot_be_weakened(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        E2BLimits(**changes)


def test_old_docker_lease_json_remains_readable() -> None:
    original: dict[str, Any] = dict(
        execution_key="a" * 32,
        owner=dict(tenant_id="fixture", user_id="alice", agent_name="sandbox-fixture"),
        run_id="run1",
        operation_id="op1",
        backend_name="code",
        backend_hash="b" * 64,
        code_hash="c" * 64,
        active=True,
        create_intent=True,
        container_id="d" * 64,
    )
    old = SandboxLease.model_validate(original)
    assert old.remote_id is None and not old.remote_start_intent
    with pytest.raises(ValidationError):
        SandboxLease.model_validate(original | {"remote_id": "remote"})
    with pytest.raises(ValidationError):
        SandboxLease.model_validate(original | {"remote_start_intent": True})


def test_host_credential_loader_has_private_literal_only_boundary(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from _e2b_host import load_key

    path = tmp_path / "private.env"
    path.write_text("UNRELATED=ignored\nE2B_API_KEY='synthetic-fixture'\n")
    path.chmod(0o600)
    assert load_key(path) == "synthetic-fixture"
    path.write_text("E2B_API_KEY=first\nE2B_API_KEY=second\n")
    with pytest.raises(ValueError):
        load_key(path)
    if sys.platform != "win32":
        path.chmod(0o644)
        with pytest.raises(ValueError):
            load_key(path)


@pytest.mark.asyncio
async def test_explicit_cloud_storage_version_preserves_docker_payloads(tmp_path: Path) -> None:
    import json

    from drov.storage import SQLiteStorage

    path = tmp_path / "state.db"
    async with SQLiteStorage(path) as storage:
        await storage._bind_sandbox()
        assert await storage._sql("SELECT value FROM metadata WHERE key='sandbox_schema'") == (
            [("1",)],
            -1,
        )
        await storage._bind_sandbox(version=2)
    async with SQLiteStorage(path) as storage:
        rows, _ = await storage._sql("SELECT value FROM metadata WHERE key='sandbox_schema'")
        assert rows == [("2",)]
        await storage._bind_sandbox()  # Opening with Docker never downgrades v2.
        rows, _ = await storage._sql("SELECT value FROM metadata WHERE key='sandbox_schema'")
        assert rows == [("2",)]
    docker = SandboxLease(
        execution_key="a" * 32,
        owner=MemoryScope(tenant_id="t", user_id="u", agent_name="a"),
        run_id="r",
        operation_id="o",
        backend_name="code",
        backend_hash="b" * 64,
        code_hash="c" * 64,
    )
    assert "remote_id" not in json.loads(docker.model_dump_json())
    assert "remote_start_intent" not in json.loads(docker.model_dump_json())
