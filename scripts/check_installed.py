"""Check package metadata and import behavior in a fresh Python process."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.abc
import importlib.metadata
import importlib.resources
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

OPTIONAL_MODULES = {"openai", "e2b", "mcp", "fastapi", "uvicorn"}
MUTATIONS = {
    "os.mkdir",
    "os.remove",
    "os.rename",
    "os.rmdir",
    "os.chmod",
    "os.chown",
    "os.link",
    "os.symlink",
    "os.truncate",
    "os.utime",
    "os.system",
    "subprocess.Popen",
}


class BlockOptionalImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if fullname.split(".", 1)[0] in OPTIONAL_MODULES:
            raise AssertionError(f"Core import attempted to load optional module: {fullname}")


def deny_side_effect(event: str, args: tuple[object, ...]) -> None:
    if event.startswith("socket.") or event in MUTATIONS:
        raise AssertionError(f"Import side effect: {event}")
    if event == "open":
        mode, flags = args[1:3]
        writing_mode = isinstance(mode, str) and any(c in mode for c in "wax+")
        writing_flags = isinstance(flags, int) and flags & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        )
        if writing_mode or writing_flags:
            raise AssertionError("Import attempted a file write")


def deny_runtime_resource(*args: object, **kwargs: object) -> None:
    raise AssertionError("Import attempted to create a thread or event loop")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Reject editable/source imports")
    parser.add_argument("--no-extras", action="store_true", help="Require core-only installation")
    options = parser.parse_args()
    assert "drov" not in sys.modules, "Must run in a fresh process"
    assert sys.dont_write_bytecode, "Run with -B to exclude Python's own bytecode cache writes"
    if options.no_extras:
        for name in OPTIONAL_MODULES:
            try:
                importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                continue
            raise AssertionError(f"Unexpected optional distribution installed: {name}")

    sys.meta_path.insert(0, BlockOptionalImports())
    # The hook lasts for this check process only; it is not a security sandbox.
    sys.addaudithook(deny_side_effect)
    with (
        patch("_thread.start_new_thread", deny_runtime_resource),
        patch("threading.Thread.start", deny_runtime_resource),
        patch.object(asyncio, "new_event_loop", deny_runtime_resource),
        patch.object(asyncio, "set_event_loop", deny_runtime_resource),
        patch("asyncio.events.new_event_loop", deny_runtime_resource),
        patch("asyncio.events.set_event_loop", deny_runtime_resource),
    ):
        import drov
        import drov.artifacts
        import drov.sandbox
        import drov.server
        from drov.config import ModelCapabilities
        from drov.integrations.e2b import E2BLimits, E2BSandbox
        from drov.models.openai import OpenAIResponses
        from drov.sandbox import DockerSandbox, PythonSandbox, SandboxLimits, SandboxResult
        from drov.sandbox._docker import DockerPreflight
        from drov.sandbox._profile import PROFILE_SHA256, DockerLimits
        from drov.storage.maintenance import SQLiteMaintenance

        assert "LocalArtifacts" in drov.artifacts.__all__
        adapter = OpenAIResponses(model="offline-import", capabilities=ModelCapabilities(text=True))
        assert adapter._client is None
        cloud = E2BSandbox(
            template_id="inert-template",
            build_id="347a1104-f90d-4644-8024-12fc30619da0",
            credential_ref="inert-host-ref",
            resolve_credential=lambda _: "never-read",
            region="project-default",
            cloud_data_policy="allow",
            budget_id="inert-budget",
            max_cost_micro_usd=100000,
            rate_micro_usd_per_second=100,
            limits=E2BLimits(),
        )
        assert not cloud.is_open
        maintenance = SQLiteMaintenance(Path(sys.prefix) / "inert-maintenance.db")
        assert not maintenance._opened
        # Even private backend construction is inert in a core-only installation.
        preflight = DockerPreflight(
            image="sha256:" + "a" * 64,
            docker=Path(sys.executable),
            host="unix:///synthetic/not-running.sock",
            limits=DockerLimits(),
        )
        from drov.integrations.mcp import MCPStdio

        mcp_server = MCPStdio(namespace="inert", revision="1", command=sys.executable)
        assert not mcp_server.is_open and mcp_server.tools == ()
        assert not preflight.is_open
        backend = DockerSandbox(
            image="sha256:" + "a" * 64,
            docker=Path(sys.executable),
            host="unix:///synthetic/not-running.sock",
            limits=SandboxLimits(),
        )
        assert not backend.is_open
        tool = PythonSandbox(backend="code")
        assert tool.name == "sandbox.run_python" and tool.effect == "write"
        assert {"DockerSandbox", "PythonSandbox", "SandboxLimits", "SandboxResult"} <= set(
            drov.sandbox.__all__
        )
        assert SandboxResult.model_json_schema()["type"] == "object"

    metadata = importlib.metadata.metadata("drov")
    assert metadata["Name"] == "drov"
    assert metadata["Version"] == drov.__version__
    assert metadata["License-Expression"] == "Apache-2.0"
    assert importlib.resources.files("drov").joinpath("py.typed").is_file()
    sandbox_resources = importlib.resources.files("drov.sandbox")
    assert (
        hashlib.sha256(sandbox_resources.joinpath("seccomp-v27.0.3.json").read_bytes()).hexdigest()
        == PROFILE_SHA256
    )
    assert sandbox_resources.joinpath("LICENSE.moby").is_file()
    assert drov.__file__ is not None
    module_path = Path(drov.__file__).resolve()
    if options.installed:
        assert module_path.is_relative_to(Path(sys.prefix).resolve()), module_path
    assert not (OPTIONAL_MODULES & sys.modules.keys())
    print(json.dumps({"version": drov.__version__, "module": str(module_path), "checks": "passed"}))


if __name__ == "__main__":
    main()
