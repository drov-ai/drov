"""Immutable Docker profile and checks shared by preflight and execution controls."""

import hashlib
import re
from dataclasses import dataclass
from typing import Any, cast

from pydantic import Field, model_validator

from drov._json import canonical_json, load_json
from drov.errors import ConfigurationError
from drov.types import PositiveInt, PositiveSeconds, WireModel

PROFILE_SHA256 = "9c1025c88ccaa517b648da571961838744ea2137f176bfe6a48b21294cae9c76"
ENTRYPOINT = "/usr/local/bin/python3"
ENVIRONMENT = {
    "PATH": "/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C.UTF-8",
}


class SandboxLimits(WireModel):
    """Private until the corresponding Runtime tool and result contract is implemented."""

    execution_seconds: PositiveSeconds = 10.0
    control_seconds: PositiveSeconds = 10.0
    memory_bytes: PositiveInt = 512 * 1024 * 1024
    cpus: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    pids: PositiveInt = 64
    workspace_bytes: PositiveInt = 64 * 1024 * 1024
    max_code_bytes: PositiveInt = 64 * 1024
    max_output_bytes: PositiveInt = 64 * 1024
    concurrency: PositiveInt = 2

    @model_validator(mode="after")
    def docker_precision(self) -> "SandboxLimits":
        # Docker rounds CPU quotas to microseconds in a 100000us period. Do not
        # advertise an arbitrary float that cannot be represented by that quota.
        quota = self.cpus * 100000
        if quota < 1000 or quota > 2**31 - 1 or abs(quota - round(quota)) > 1e-7:
            raise ValueError("CPU quota must be an integral microsecond, at least 1000us")
        if self.memory_bytes < 6 * 1024 * 1024 or self.memory_bytes > 2**63 - 1:
            raise ValueError("Docker memory requires at least 6MiB and a signed 64-bit value")
        if self.workspace_bytes > 2**63 - 1 or self.pids > 2**31 - 1:
            raise ValueError("Docker limits exceed their controller representation")
        return self


DockerLimits = SandboxLimits


def image_reference(value: str) -> str:
    if type(value) is not str or not re.fullmatch(
        r"(?:sha256:[0-9a-f]{64}|[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64})", value
    ):
        raise ConfigurationError("An immutable image ID or repository digest is required")
    return value


def require(condition: object, message: str) -> None:
    if condition is not True:
        raise ConfigurationError(message)


def daemon_profile(info: dict[str, Any], version: dict[str, Any]) -> dict[str, Any]:
    require(info.get("OSType") == "linux" and version.get("Os") == "linux", "Linux Engine required")
    require(info.get("CgroupVersion") == "2", "Docker profile requires cgroup v2")
    for capability in ("MemoryLimit", "SwapLimit", "CpuCfsPeriod", "CpuCfsQuota", "PidsLimit"):
        require(info.get(capability) is True, "Required Docker resource control is unavailable")
    security = info.get("SecurityOptions")
    require(isinstance(security, list), "Missing Docker security capabilities")
    security = cast(list[Any], security)
    require(
        any(isinstance(item, str) and item.startswith("name=seccomp,") for item in security),
        "Engine seccomp capability is required",
    )
    # An explicit pinned profile will be supplied; an unconfined daemon default
    # is neither accepted for execution nor confused with absence of seccomp.
    require("name=cgroupns" in security, "Private cgroup namespace capability is required")
    api = version.get("ApiVersion")
    require(
        isinstance(api, str) and re.fullmatch(r"[0-9]+\.[0-9]+", api) is not None, "Invalid API"
    )
    api = cast(str, api)
    require(tuple(map(int, api.split("."))) >= (1, 41), "Docker API 1.41 or newer required")
    minimum = version.get("MinAPIVersion")
    require(
        isinstance(minimum, str) and re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}", minimum) is not None,
        "Minimum Docker API version is required",
    )
    require(
        tuple(map(int, cast(str, minimum).split("."))) <= (1, 41),
        "Engine must accept the fixed Docker API 1.41 subset",
    )
    architecture = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}
    arch = architecture.get(str(info.get("Architecture")))
    require(arch is not None and version.get("Arch") == arch, "Unsupported Engine architecture")
    require(isinstance(info.get("ID"), str) and bool(info["ID"]), "Engine identity required")
    require(
        isinstance(version.get("Version"), str) and bool(version["Version"]),
        "Engine version required",
    )
    return {
        "engine_id": info["ID"],
        "engine_version": version["Version"],
        "api_version": api,
        "architecture": arch,
        "cgroup_version": "2",
        "security_profile": PROFILE_SHA256,
    }


def checked_image(image: dict[str, Any], reference: str, architecture: str) -> str:
    identifier = image.get("Id")
    require(
        isinstance(identifier, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", identifier) is not None,
        "Invalid installed image identity",
    )
    require(
        image.get("Os") == "linux" and image.get("Architecture") == architecture,
        "Image platform differs",
    )
    if reference.startswith("sha256:"):
        require(identifier == reference, "Installed image differs from its fixed ID")
    else:
        digests = image.get("RepoDigests")
        require(isinstance(digests, list), "Installed image has no repository digest")
        require(
            any(
                isinstance(item, str) and item.endswith("@" + reference.split("@", 1)[1])
                for item in cast(list[Any], digests)
            ),
            "Installed image differs from its repository digest",
        )
    # Repository aliases (including docker.io/library prefixes) may differ in
    # inspect. The resolved fixed config ID is subsequently used, never the alias.
    config = image.get("Config")
    require(isinstance(config, dict), "Missing image configuration")
    config = cast(dict[str, Any], config)
    require(not config.get("Volumes"), "Image automatic volumes are forbidden")
    environment = config.get("Env")
    require(isinstance(environment, list), "Image environment must be explicit")
    environment = cast(list[Any], environment)
    seen: set[str] = set()
    for entry in environment:
        require(isinstance(entry, str) and "=" in entry, "Invalid image environment")
        name, value = entry.split("=", 1)
        require(name not in seen, "Duplicate image environment")
        seen.add(name)
        if name in ENVIRONMENT:
            require(value == ENVIRONMENT[name], "Image environment differs from the fixed profile")
        elif name == "GPG_KEY":
            require(
                re.fullmatch(r"[0-9A-F]{40}", value) is not None, "Invalid image build metadata"
            )
        elif name == "PYTHON_VERSION":
            require(
                re.fullmatch(r"3\.11\.[0-9]+", value) is not None, "CPython 3.11 image required"
            )
        elif name == "PYTHON_SHA256":
            require(
                re.fullmatch(r"[0-9a-f]{64}", value) is not None, "Invalid image build metadata"
            )
        else:
            raise ConfigurationError("Unexpected image environment name")
    require(set(ENVIRONMENT) <= seen, "Fixed image environment is missing")
    return cast(str, identifier)


@dataclass(frozen=True)
class DockerProfile:
    image_id: str
    architecture: str
    limits: DockerLimits

    def create_arguments(self, *, name: str, labels: dict[str, str], seccomp: str) -> list[str]:
        """Only fixed controls; the dispatch owner must persist intent before using this."""
        require(
            re.fullmatch(r"drov-[a-z0-9-]{1,100}", name) is not None,
            "Invalid internal resource name",
        )
        require(
            all(
                re.fullmatch(r"dev\.drov\.[a-z_]+", key)
                and re.fullmatch(r"[a-z0-9-]{1,128}", value)
                for key, value in labels.items()
            )
            and bool(labels),
            "Invalid internal ownership labels",
        )
        args = [
            "container",
            "create",
            "--pull=never",
            "--name",
            name,
            "--platform=linux/" + self.architecture,
            "--network=none",
            "--read-only",
            "--user=65534:65534",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--security-opt=seccomp=" + seccomp,
            "--cgroupns=private",
            "--ipc=private",
            "--memory=" + str(self.limits.memory_bytes),
            "--memory-swap=" + str(self.limits.memory_bytes),
            "--cpu-period=100000",
            "--cpu-quota=" + str(round(self.limits.cpus * 100000)),
            "--pids-limit=" + str(self.limits.pids),
            "--tmpfs=/work:rw,noexec,nosuid,nodev,size="
            + str(self.limits.workspace_bytes)
            + ",mode=1777",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777",
            "--shm-size=16777216",
            "--workdir=/work",
            "--log-driver=none",
            "--no-healthcheck",
            "--restart=no",
            "--stop-timeout=0",
            "--interactive",
            "--entrypoint=" + ENTRYPOINT,
        ]
        for key, value in sorted(labels.items()):
            args += ["--label", key + "=" + value]
        return args + [self.image_id]

    def inspect_created(
        self, resource: dict[str, Any], *, name: str, labels: dict[str, str], seccomp: bytes
    ) -> None:
        """Fail before starting code on any mismatched resource or weakened setting."""
        config, host, state = (resource.get(key) for key in ("Config", "HostConfig", "State"))
        require(
            all(isinstance(value, dict) for value in (config, host, state)),
            "Invalid container inspection",
        )
        config, host, state = (cast(dict[str, Any], item) for item in (config, host, state))
        checked_image(
            {
                "Id": self.image_id,
                "Os": "linux",
                "Architecture": self.architecture,
                "Config": config,
            },
            self.image_id,
            self.architecture,
        )
        require(
            resource.get("Name") == "/" + name and resource.get("Image") == self.image_id,
            "Container identity differs",
        )
        require(config.get("Labels") == labels, "Container ownership differs")
        require(
            state.get("Status") == "created" and state.get("Running") is False,
            "Code resource already started",
        )
        require(
            config.get("User") == "65534:65534" and config.get("WorkingDir") == "/work",
            "Container user/workdir differs",
        )
        require(
            config.get("Entrypoint") == [ENTRYPOINT] and config.get("Tty") is False,
            "Container entrypoint/TTY differs",
        )
        healthcheck = config.get("Healthcheck")
        require(
            not healthcheck
            or isinstance(healthcheck, dict)
            and healthcheck.get("Test") == ["NONE"],
            "Healthcheck is active",
        )
        require(config.get("OpenStdin") is True, "Missing input pipe")
        expected = {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "Memory": self.limits.memory_bytes,
            "MemorySwap": self.limits.memory_bytes,
            "CpuPeriod": 100000,
            "CpuQuota": round(self.limits.cpus * 100000),
            "PidsLimit": self.limits.pids,
            "ShmSize": 16777216,
            "CgroupnsMode": "private",
            "IpcMode": "private",
            "PublishAllPorts": False,
        }
        for key, value in expected.items():
            require(
                host.get(key) == value and type(host.get(key)) is type(value),
                "Container control differs: " + key,
            )
        for key in (
            "Binds",
            "Devices",
            "DeviceRequests",
            "PortBindings",
            "VolumesFrom",
            "CapAdd",
            "GroupAdd",
            "Links",
        ):
            require(not host.get(key), "Container has forbidden access: " + key)
        require(
            not resource.get("Mounts") and not config.get("Volumes"), "Unexpected container mount"
        )
        require(
            not host.get("PidMode") and not host.get("UTSMode") and not host.get("UsernsMode"),
            "Host namespace override",
        )
        require(host.get("CapDrop") == ["ALL"], "Capabilities not dropped")
        security = host.get("SecurityOpt")
        require(
            isinstance(security, list) and len(security) == 2 and "no-new-privileges" in security,
            "Security options differ",
        )
        security = cast(list[Any], security)
        entries = [
            item for item in security if isinstance(item, str) and item.startswith("seccomp=")
        ]
        require(len(entries) == 1, "Explicit seccomp profile is missing")
        require(
            hashlib.sha256(seccomp).hexdigest() == PROFILE_SHA256, "Trusted seccomp profile differs"
        )
        try:
            # Docker stores the expanded profile, not the CLI file path. Compare
            # the exact JSON meaning against the independently pinned bytes.
            require(
                canonical_json(load_json(entries[0][8:])) == canonical_json(load_json(seccomp)),
                "Container seccomp profile differs",
            )
        except (ValueError, TypeError):
            raise ConfigurationError("Container seccomp profile differs") from None
        require(host.get("OomKillDisable") is False, "OOM killer is disabled")
        require(host.get("LogConfig") == {"Type": "none", "Config": {}}, "Unbounded container log")
        require(
            host.get("RestartPolicy") == {"Name": "no", "MaximumRetryCount": 0},
            "Automatic restart is forbidden",
        )
        require(
            host.get("Tmpfs")
            == {
                "/work": "rw,noexec,nosuid,nodev,size="
                + str(self.limits.workspace_bytes)
                + ",mode=1777",
                "/tmp": "rw,noexec,nosuid,nodev,size=16777216,mode=1777",
            },
            "Container tmpfs differs",
        )

    def inspect_effective(self, observation: dict[str, Any]) -> None:
        expected = {
            "uid": 65534,
            "Seccomp": "2",
            "NoNewPrivs": "1",
            "CapEff": "0000000000000000",
            "memory.max": str(self.limits.memory_bytes),
            "memory.swap.max": "0",
            "cpu.max": str(round(self.limits.cpus * 100000)) + " 100000",
            "pids.max": str(self.limits.pids),
            "root_readonly": True,
        }
        for key, value in expected.items():
            require(
                observation.get(key) == value and type(observation.get(key)) is type(value),
                "Effective container control differs: " + key,
            )


def configuration_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


INPUT_TMPFS = "rw,noexec,nosuid,nodev,size=16777216,uid=65534,gid=65534,mode=0700"


@dataclass(frozen=True)
class ExecutionProfile(DockerProfile):
    """Bounded input area owned by the trusted UID, separate from the workload UID."""

    def create_arguments(self, *, name: str, labels: dict[str, str], seccomp: str) -> list[str]:
        arguments = super().create_arguments(name=name, labels=labels, seccomp=seccomp)
        return arguments[:-1] + ["--tmpfs=/work/inputs:" + INPUT_TMPFS, arguments[-1]]

    def inspect_created(
        self, resource: dict[str, Any], *, name: str, labels: dict[str, str], seccomp: bytes
    ) -> None:
        host = resource.get("HostConfig")
        require(isinstance(host, dict), "Missing execution container controls")
        host = cast(dict[str, Any], host)
        tmpfs = host.get("Tmpfs")
        require(
            isinstance(tmpfs, dict) and tmpfs.get("/work/inputs") == INPUT_TMPFS,
            "Input area controls differ",
        )
        checked = dict(resource)
        checked["HostConfig"] = host | {
            "Tmpfs": {
                key: value
                for key, value in cast(dict[str, Any], tmpfs).items()
                if key != "/work/inputs"
            }
        }
        super().inspect_created(checked, name=name, labels=labels, seccomp=seccomp)
