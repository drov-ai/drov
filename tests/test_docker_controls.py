"""Private backend preflight/control contracts. Real containers use the explicit probe."""

import asyncio
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from drov.errors import ConfigurationError
from drov.sandbox._docker import CONTROL_BYTES, ControlError, DockerControl, DockerPreflight
from drov.sandbox._profile import (
    ENVIRONMENT,
    PROFILE_SHA256,
    DockerLimits,
    DockerProfile,
    checked_image,
    daemon_profile,
    image_reference,
)

IMAGE_ID = "sha256:" + "a" * 64
REFERENCE = "docker.io/library/python@sha256:" + "b" * 64
NAME = "drov-test-original"
LABELS = {"dev.drov.execution": "1234-abcd"}


def image() -> dict[str, Any]:
    return {
        "Id": IMAGE_ID,
        "Os": "linux",
        "Architecture": "arm64",
        "RepoDigests": [REFERENCE],
        "Config": {
            "Env": [key + "=" + value for key, value in ENVIRONMENT.items()],
            "Volumes": None,
        },
    }


def info() -> dict[str, Any]:
    return {
        "ID": "fixture-engine",
        "OSType": "linux",
        "Architecture": "aarch64",
        "CgroupVersion": "2",
        "MemoryLimit": True,
        "SwapLimit": True,
        "CpuCfsPeriod": True,
        "CpuCfsQuota": True,
        "PidsLimit": True,
        "SecurityOptions": ["name=seccomp,profile=unconfined", "name=cgroupns"],
    }


def version() -> dict[str, Any]:
    return {"Os": "linux", "Arch": "arm64", "Version": "27.0.3", "ApiVersion": "1.46"}


def created() -> dict[str, Any]:
    seccomp = files("drov.sandbox").joinpath("seccomp-v27.0.3.json").read_text()
    return {
        "Name": "/" + NAME,
        "Image": IMAGE_ID,
        "Mounts": [],
        "State": {"Status": "created", "Running": False},
        "Config": image()["Config"]
        | {
            "Labels": LABELS,
            "User": "65534:65534",
            "WorkingDir": "/work",
            "Entrypoint": ["/usr/local/bin/python3"],
            "Tty": False,
            "OpenStdin": True,
            "Healthcheck": {"Test": ["NONE"]},
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "Memory": 536870912,
            "MemorySwap": 536870912,
            "CpuPeriod": 100000,
            "CpuQuota": 100000,
            "PidsLimit": 64,
            "ShmSize": 16777216,
            "CgroupnsMode": "private",
            "IpcMode": "private",
            "PublishAllPorts": False,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges", "seccomp=" + seccomp],
            "OomKillDisable": False,
            "LogConfig": {"Type": "none", "Config": {}},
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "Tmpfs": {
                "/work": "rw,noexec,nosuid,nodev,size=67108864,mode=1777",
                "/tmp": "rw,noexec,nosuid,nodev,size=16777216,mode=1777",
            },
        },
    }


@pytest.mark.parametrize(
    "change",
    [
        {"execution_seconds": 0.0},
        {"control_seconds": float("inf")},
        {"concurrency": True},
        {"memory_bytes": 1024},
        {"workspace_bytes": 2**63},
        {"pids": 2**32},
        {"cpus": float("nan")},
        {"cpus": 0.001},
        {"cpus": 1.000001},
        {"unknown": 1},
    ],
)
def test_limits_reject_unrepresentable_or_nonfinite_controls(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DockerLimits.model_validate(change)


@pytest.mark.parametrize(
    "reference",
    [
        "python:3.11",
        "python:latest",
        "-x@sha256:" + "b" * 64,
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
    ],
)
def test_image_requires_an_immutable_identity(reference: str) -> None:
    with pytest.raises(ConfigurationError):
        image_reference(reference)


@pytest.mark.parametrize(
    "field,value",
    [
        ("OSType", "windows"),
        ("CgroupVersion", "1"),
        ("SwapLimit", False),
        ("MemoryLimit", 1),
        ("CpuCfsPeriod", False),
        ("CpuCfsQuota", False),
        ("PidsLimit", False),
        ("SecurityOptions", []),
        ("Architecture", "riscv64"),
        ("ID", ""),
    ],
)
def test_preflight_rejects_missing_engine_controls(field: str, value: Any) -> None:
    data = info()
    data[field] = value
    with pytest.raises(ConfigurationError):
        daemon_profile(data, version())


def test_pinned_explicit_profile_does_not_accept_unconfined_execution() -> None:
    assert daemon_profile(info(), version())["security_profile"] == PROFILE_SHA256
    data = created()
    data["HostConfig"]["SecurityOpt"] = ["no-new-privileges", "seccomp=unconfined"]
    with pytest.raises(ConfigurationError):
        DockerProfile(IMAGE_ID, "arm64", DockerLimits()).inspect_created(
            data,
            name=NAME,
            labels=LABELS,
            seccomp=files("drov.sandbox").joinpath("seccomp-v27.0.3.json").read_bytes(),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"Os": "windows"},
        {"Architecture": "amd64"},
        {"Id": "sha256:" + "c" * 64},
        {"Config": {"Env": ["AWS_SECRET_ACCESS_KEY=synthetic-only"]}},
        {"Config": {"Env": ["PATH=/tmp", "LANG=C.UTF-8"]}},
        {"Config": image()["Config"] | {"Volumes": {"/work": {}}}},
        {
            "Config": {
                "Env": [
                    "PATH=" + ENVIRONMENT["PATH"],
                    "PATH=" + ENVIRONMENT["PATH"],
                    "LANG=C.UTF-8",
                ]
            }
        },
    ],
)
def test_image_rejects_platform_automatic_volumes_and_environment(change: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError):
        checked_image(image() | change, IMAGE_ID, "arm64")


def test_resolved_repository_digest_is_bound_to_config_id() -> None:
    assert checked_image(image(), REFERENCE, "arm64") == IMAGE_ID
    with pytest.raises(ConfigurationError):
        checked_image(image() | {"RepoDigests": []}, REFERENCE, "arm64")


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("HostConfig", "Binds", ["/host:/host"]),
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "MemorySwap", -1),
        ("HostConfig", "CpuQuota", 0),
        ("HostConfig", "PidsLimit", 0),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "Devices", [{}]),
        ("HostConfig", "LogConfig", {"Type": "json-file", "Config": {}}),
        ("HostConfig", "CgroupnsMode", "host"),
        ("HostConfig", "OomKillDisable", True),
        ("HostConfig", "Tmpfs", {}),
        ("Config", "User", "0"),
        ("Config", "Labels", {"dev.drov.execution": "other"}),
        ("Config", "Tty", True),
        ("Config", "Healthcheck", {"Test": ["CMD", "false"]}),
        ("State", "Status", "exited"),
    ],
)
def test_code_cannot_start_with_weakened_or_foreign_resource(
    section: str, field: str, value: Any
) -> None:
    resource = created()
    resource[section][field] = value
    with pytest.raises(ConfigurationError):
        DockerProfile(IMAGE_ID, "arm64", DockerLimits()).inspect_created(
            resource,
            name=NAME,
            labels=LABELS,
            seccomp=files("drov.sandbox").joinpath("seccomp-v27.0.3.json").read_bytes(),
        )


def test_container_seccomp_compaction_is_not_a_weaker_profile() -> None:
    pinned = files("drov.sandbox").joinpath("seccomp-v27.0.3.json").read_bytes()
    assert hashlib.sha256(pinned).hexdigest() == PROFILE_SHA256
    resource = created()
    resource["HostConfig"]["SecurityOpt"][1] = "seccomp=" + json.dumps(
        json.loads(pinned), separators=(",", ":")
    )
    profile = DockerProfile(IMAGE_ID, "arm64", DockerLimits())
    profile.inspect_created(resource, name=NAME, labels=LABELS, seccomp=pinned)
    changed = json.loads(pinned)
    changed["defaultAction"] = "SCMP_ACT_ALLOW"
    resource["HostConfig"]["SecurityOpt"][1] = "seccomp=" + json.dumps(changed)
    with pytest.raises(ConfigurationError):
        profile.inspect_created(resource, name=NAME, labels=LABELS, seccomp=pinned)


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", 0),
        ("Seccomp", "0"),
        ("NoNewPrivs", "0"),
        ("CapEff", "0000000000000001"),
        ("memory.max", "max"),
        ("memory.swap.max", "536870912"),
        ("cpu.max", "max 100000"),
        ("pids.max", "max"),
        ("root_readonly", False),
    ],
)
def test_effective_kernel_controls_must_match_configuration(field: str, value: Any) -> None:
    observation = {
        "uid": 65534,
        "Seccomp": "2",
        "NoNewPrivs": "1",
        "CapEff": "0000000000000000",
        "memory.max": "536870912",
        "memory.swap.max": "0",
        "cpu.max": "100000 100000",
        "pids.max": "64",
        "root_readonly": True,
    }
    profile = DockerProfile(IMAGE_ID, "arm64", DockerLimits())
    profile.inspect_effective(observation)
    with pytest.raises(ConfigurationError):
        profile.inspect_effective(observation | {field: value})


FAKE = r"""
import json,os,pathlib,subprocess,sys,time
root=pathlib.Path(__file__).parent
args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as stream:
    stream.write(json.dumps({'args':args,'names':sorted(os.environ),'cwd':os.getcwd()})+'\n')
mode=json.loads((root/'responses.json').read_text())
if mode.get('sleep'):
    (root/'entered').write_text(str(os.getpid()))
    time.sleep(30)
if mode.get('child'):
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
    (root/'child').write_text(str(child.pid))
    sys.exit(0)
if mode.get('large'):
    os.write(1,b'x'*mode['large'])
elif mode.get('bad'):
    print('not json')
elif mode.get('error'):
    print('synthetic-private-diagnostic',file=sys.stderr)
    sys.exit(1)
elif 'info' in args:
    print(json.dumps(mode['info']))
elif 'version' in args:
    print(json.dumps(mode['version']))
else:
    print(json.dumps([mode['image']]))
"""


@pytest.fixture
def local_cli(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    if os.name != "posix":
        pytest.skip("POSIX process groups / Unix socket; core import remains portable")
    executable = tmp_path / "docker-fixture"
    executable.write_text("#!" + sys.executable + "\n" + FAKE)
    executable.chmod(0o700)
    (tmp_path / "responses.json").write_text(
        json.dumps({"info": info(), "version": version(), "image": image()})
    )
    # macOS sun_path is short; put the socket in a separate short temporary directory.
    import tempfile

    with (
        tempfile.TemporaryDirectory(prefix="drov-sock-") as directory,
        socket.socket(socket.AF_UNIX) as connection,
    ):
        path = Path(directory) / "s"
        connection.bind(str(path))
        yield executable, "unix://" + str(path)


def responses(executable: Path, value: dict[str, Any]) -> None:
    (executable.parent / "responses.json").write_text(json.dumps(value))


async def entered(executable: Path, name: str = "entered") -> int:
    for _ in range(300):
        path = executable.parent / name
        if path.exists():
            return int(path.read_text())
        await asyncio.sleep(0.01)
    raise AssertionError("Fixture did not enter the real subprocess")


@pytest.mark.asyncio
async def test_construct_is_inert_and_open_uses_only_readonly_commands(
    local_cli: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, host = local_cli
    monkeypatch.setenv("SYNTHETIC_PRIVATE_KEY", "fixture-not-a-real-key")
    monkeypatch.setenv("DOCKER_HOST", "tcp://synthetic-unwanted:1234")
    backend = DockerPreflight(image=IMAGE_ID, docker=executable, host=host)
    assert not (executable.parent / "calls.jsonl").exists()
    with pytest.raises(ConfigurationError):
        _ = backend.config_hash
    async with backend:
        fingerprint = backend.config_hash
        control = backend._control
        assert control is not None
        directory = control.directory
        assert json.loads((directory / "config.json").read_text()) == {"auths": {}, "proxies": {}}
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / "seccomp.json").stat().st_mode & 0o777 == 0o600
        calls = [
            json.loads(line)
            for line in (executable.parent / "calls.jsonl").read_text().splitlines()
        ]
        assert len(calls) == 3
        assert [call["args"][4:6] for call in calls] == [
            ["system", "info"],
            ["version", "--format"],
            ["image", "inspect"],
        ]
        automatic = {"__CF_USER_TEXT_ENCODING"} if sys.platform == "darwin" else set()
        assert all(set(call["names"]) <= {"PATH", "LC_CTYPE"} | automatic for call in calls), [
            call["names"] for call in calls
        ]
        expected_directory = await asyncio.to_thread(directory.resolve)
        for call in calls:
            assert await asyncio.to_thread(Path(call["cwd"]).resolve) == expected_directory
        with pytest.raises(ConfigurationError):
            await backend.open()
    assert not directory.exists()
    with pytest.raises(ConfigurationError):
        _ = backend.profile
    # Random private config directories are intentionally excluded from fingerprints.
    async with DockerPreflight(image=IMAGE_ID, docker=executable, host=host) as reopened:
        assert reopened.config_hash == fingerprint
    altered = copy.deepcopy(info())
    altered["ID"] = "different-engine"
    responses(executable, {"info": altered, "version": version(), "image": image()})
    async with DockerPreflight(image=IMAGE_ID, docker=executable, host=host) as changed:
        assert changed.config_hash != fingerprint


@pytest.mark.asyncio
async def test_failed_preflight_does_not_inspect_or_create_image_resource(
    local_cli: tuple[Path, str],
) -> None:
    executable, host = local_cli
    responses(
        executable,
        {"info": info() | {"MemoryLimit": False}, "version": version(), "image": image()},
    )
    backend = DockerPreflight(image=IMAGE_ID, docker=executable, host=host)
    with pytest.raises(ConfigurationError):
        await backend.open()
    assert not backend.is_open and backend._directory is None
    calls = (executable.parent / "calls.jsonl").read_text()
    assert '"create"' not in calls and '"inspect"' not in calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,message",
    [
        ({"large": CONTROL_BYTES + 65536}, "byte bound"),
        ({"bad": True}, "Invalid Docker"),
        ({"error": True}, "command failed"),
        ({"sleep": True}, "deadline"),
    ],
)
async def test_control_is_bounded_and_does_not_expose_cli_diagnostics(
    local_cli: tuple[Path, str], mode: dict[str, Any], message: str
) -> None:
    executable, host = local_cli
    responses(executable, mode)
    # Immediate response classification does not assert interpreter startup
    # latency. Keep the short ceiling only for the deliberate hang case.
    control = DockerControl(executable, host, executable.parent, 0.5 if mode.get("sleep") else 5.0)
    with pytest.raises(ControlError, match=message) as caught:
        await control.object(["system", "info"])
    assert "synthetic-private-diagnostic" not in str(caught.value)


@pytest.mark.asyncio
async def test_caller_cancellation_reaps_actual_control_process(
    local_cli: tuple[Path, str],
) -> None:
    executable, host = local_cli
    responses(executable, {"sleep": True})
    control = DockerControl(executable, host, executable.parent, 5.0)
    task = asyncio.create_task(control.command(["system", "info"]))
    pid = await entered(executable)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_exited_cli_is_not_a_finished_pipe_or_live_descendant(
    local_cli: tuple[Path, str],
) -> None:
    executable, host = local_cli
    responses(executable, {"child": True})
    control = DockerControl(executable, host, executable.parent, 0.5)
    with pytest.raises(ControlError, match="deadline"):
        await control.command(["system", "info"])
    pid = await entered(executable, "child")
    result = await asyncio.to_thread(
        subprocess.run, ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, check=False
    )
    assert not result.stdout.strip() or result.stdout.strip().startswith(b"Z")


@pytest.mark.asyncio
async def test_close_during_open_keeps_control_ownership(local_cli: tuple[Path, str]) -> None:
    executable, host = local_cli
    responses(executable, {"sleep": True})
    backend = DockerPreflight(
        image=IMAGE_ID, docker=executable, host=host, limits=DockerLimits(control_seconds=0.5)
    )
    opening = asyncio.create_task(backend.open())
    pid = await entered(executable)
    with pytest.raises(ControlError, match="deadline"):
        await backend.aclose()
    with pytest.raises(ControlError, match="deadline"):
        await opening
    assert not backend.is_open and backend._directory is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_untrusted_cli_permissions_reject_before_any_command(
    local_cli: tuple[Path, str],
) -> None:
    executable, host = local_cli
    executable.chmod(0o722)
    backend = DockerPreflight(image=IMAGE_ID, docker=executable, host=host)
    with pytest.raises(ConfigurationError, match="writable"):
        await backend.open()
    assert not (executable.parent / "calls.jsonl").exists()


@pytest.mark.parametrize(
    "host", ["tcp://127.0.0.1:2375", "ssh://fixture", "unix://remote/tmp/socket"]
)
def test_remote_endpoint_is_not_an_implicit_fallback(host: str) -> None:
    with pytest.raises(ConfigurationError):
        DockerPreflight(image=IMAGE_ID, docker=Path(sys.executable), host=host)
