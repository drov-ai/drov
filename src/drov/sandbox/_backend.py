"""Owned single-container execution. Durable mutation barriers are provided by Runtime."""

import asyncio
import base64
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from drov._json import canonical_json, load_json
from drov.artifacts.types import ArtifactInput, ArtifactLimits, file_path
from drov.errors import ConfigurationError
from drov.sandbox._docker import ControlError, DockerPreflight, _settle
from drov.sandbox._engine import EngineError, UnixEngine
from drov.sandbox._leases import SandboxLease
from drov.sandbox._png import validate_png
from drov.sandbox._profile import INPUT_TMPFS, ExecutionProfile, configuration_hash, require
from drov.sandbox._runner import EXPORT, INSTALL, SUPERVISOR, WORKLOAD

FactWriter = Callable[[dict[str, Any]], Awaitable[SandboxLease]]


class DockerBackend(DockerPreflight):
    """Private until the Runtime descriptor and resource ownership are connected."""

    async def open(self) -> None:
        await super().open()
        assert self._binding is not None
        base = self.profile
        self._profile = ExecutionProfile(base.image_id, base.architecture, base.limits)
        self._binding["controller_version"] = 2
        self._binding["input_tmpfs"] = INPUT_TMPFS
        self._binding["runner_hashes"] = {
            name: hashlib.sha256(program.encode()).hexdigest()
            for name, program in (
                ("supervisor", SUPERVISOR),
                ("workload", WORKLOAD),
                ("export", EXPORT),
                ("install", INSTALL),
            )
        }

    async def revalidate(self) -> None:
        self._require_loop()
        async with type(self)(
            image=self._image, docker=self._executable, host=self._host, limits=self._limits
        ) as current:
            require(
                current.config_hash == self.config_hash, "Original Docker configuration changed"
            )

    def _labels(self, lease: SandboxLease) -> dict[str, str]:
        return {
            "dev.drov.execution": lease.execution_key,
            "dev.drov.owner": configuration_hash(lease.owner.model_dump(mode="json")),
            "dev.drov.config": lease.backend_hash,
        }

    async def resource(self, lease: SandboxLease) -> dict[str, Any] | None:
        require(lease.backend_hash == self.config_hash, "Original Docker configuration differs")
        control = self._control
        assert control is not None
        existing = await control.command(
            [
                "container",
                "ls",
                "--all",
                "--filter",
                "name=^/" + lease.name + "$",
                "--format",
                "{{.ID}}",
            ]
        )
        if not existing.strip():
            return None
        resource = await control.object(["container", "inspect", lease.name], singleton=True)
        require(
            resource.get("Name") == "/" + lease.name
            and resource.get("Image") == self.profile.image_id,
            "Original Docker resource identity differs",
        )
        require(
            resource["Config"].get("Labels") == self._labels(lease),
            "Original Docker resource ownership differs",
        )
        if lease.container_id is not None:
            require(
                resource.get("Id") == lease.container_id,
                "Original container acknowledgement differs",
            )
        return resource

    async def destroy(self, lease: SandboxLease, fact: FactWriter) -> SandboxLease:
        """Never starts code; absent resources after a lost create reply stay unknown."""
        async with asyncio.timeout(self.profile.limits.control_seconds):
            return await self._destroy(lease, fact)

    async def _destroy(self, lease: SandboxLease, fact: FactWriter) -> SandboxLease:
        if lease.destroyed:
            return lease
        control = self._control
        assert control is not None
        resource = await self.resource(lease)
        if resource is None:
            require(
                lease.container_id is not None or not lease.create_intent,
                "Container creation acknowledgement lost; absence does not prove cleanup",
            )
        else:
            await control.command(["container", "rm", "--force", resource["Id"]])
            require(await self.resource(lease) is None, "Container destruction is unconfirmed")
        return await fact({"destroyed": True})

    async def _copy_control(self, container_id: str) -> dict[str, Any] | None:
        control = self._control
        assert control is not None
        engine = UnixEngine(control.host, control_seconds=self.profile.limits.control_seconds)
        program = (
            "import pathlib\n"
            "try: data=pathlib.Path('/work/.drov-control.json').read_text()\n"
            "except FileNotFoundError: data='null'\n"
            "print(data)"
        )
        execution_id = await engine.create_exec(container_id, program, control=True)
        result = await engine.start_exec(
            execution_id, seconds=self.profile.limits.control_seconds, max_bytes=4096
        )
        state = await engine.inspect_exec(execution_id)
        require(
            state.get("ContainerID") == container_id
            and state.get("ID") == execution_id
            and state.get("Running") is False
            and state.get("ExitCode") == 0
            and result.termination == "exited",
            "Protected control observation unavailable",
        )
        value = load_json(result.stdout)
        if value is None:
            return None  # Supervisor startup is bounded below, not a workload retry.
        require(isinstance(value, dict), "Invalid effective Docker controls")
        return cast(dict[str, Any], value)

    async def execute(
        self,
        lease: SandboxLease,
        code: str,
        *,
        inputs: Sequence[ArtifactInput],
        file_limits: ArtifactLimits,
        fact: FactWriter,
        seconds: float,
    ) -> tuple[SandboxLease, list[ArtifactInput]]:
        require(
            type(code) is str and bool(code) and "\0" not in code,
            "Code must be a nonempty Python source string",
        )
        require(
            lease.create_intent and lease.active and lease.container_id is None,
            "Execution requires a new committed intent; old code must not be redispatched",
        )
        require(lease.backend_hash == self.config_hash, "Approved Docker configuration differs")
        require(
            hashlib.sha256(code.encode()).hexdigest() == lease.code_hash, "Approved code differs"
        )
        require(
            len(code.encode()) <= self.profile.limits.max_code_bytes,
            "Code exceeds the host ceiling",
        )
        require(
            len(inputs) == len(lease.inputs) and len(inputs) <= file_limits.max_input_files,
            "Approved input set differs",
        )
        require(
            sum(len(item.data) for item in inputs) <= file_limits.max_input_bytes,
            "Input bytes exceed the host ceiling",
        )
        require(
            sum(len(item.data) for item in inputs) <= 15 * 1024**2,
            "Inputs exceed the fixed private input area",
        )
        for item, grant in zip(inputs, lease.inputs, strict=True):
            require(
                item.name == grant.name
                and len(item.data) == grant.ref.size_bytes
                and hashlib.sha256(item.data).hexdigest() == grant.ref.sha256,
                "Approved input bytes differ",
            )
        require(len(lease.outputs) <= file_limits.max_output_files, "Too many declared outputs")
        require(all(file_path(name) == name for name in lease.outputs), "Unsafe output declaration")
        require(
            len({name.split("/")[-1] for name in lease.outputs}) == len(lease.outputs),
            "Output attachment names conflict",
        )
        control = self._control
        assert control is not None
        engine = UnixEngine(control.host, control_seconds=self.profile.limits.control_seconds)
        exported: list[ArtifactInput] = []
        started = asyncio.get_running_loop().time()
        try:
            container_id = (
                (
                    await control.command(
                        self.profile.create_arguments(
                            name=lease.name,
                            labels=self._labels(lease),
                            seccomp=str(control.directory / "seccomp.json"),
                        )
                        + ["-I", "-B", "-c", SUPERVISOR]
                    )
                )
                .decode("ascii")
                .strip()
            )
            resource = await control.object(["container", "inspect", container_id], singleton=True)
            self.profile.inspect_created(
                resource,
                name=lease.name,
                labels=self._labels(lease),
                seccomp=(control.directory / "seccomp.json").read_bytes(),
            )
            lease = await fact({"container_id": container_id})
            await control.command(["container", "start", container_id])
            if inputs:
                install_payload = canonical_json(
                    [
                        {
                            "id": grant.ref.artifact_id,
                            "name": item.name,
                            "data": base64.b64encode(item.data).decode("ascii"),
                        }
                        for item, grant in zip(inputs, lease.inputs, strict=True)
                    ]
                ).encode()
                installed = load_json(
                    await control.command(
                        [
                            "container",
                            "exec",
                            "--interactive",
                            "--user=65534:65534",
                            container_id,
                            "/usr/local/bin/python3",
                            "-I",
                            "-B",
                            "-c",
                            INSTALL,
                        ],
                        data=install_payload,
                    )
                )
                require(
                    isinstance(installed, dict)
                    and installed.get("uid") == 65534
                    and installed.get("mode") == 0o555,
                    "Protected input directory differs",
                )
                installed = cast(dict[str, Any], installed)
                require(
                    installed.get("files")
                    == [
                        {
                            "id": grant.ref.artifact_id,
                            "name": item.name,
                            "sha256": grant.ref.sha256,
                            "uid": 65534,
                            "mode": 0o444,
                            "size": len(item.data),
                        }
                        for item, grant in zip(inputs, lease.inputs, strict=True)
                    ],
                    "Protected input bytes or permissions differ",
                )
            end = asyncio.get_running_loop().time() + self.profile.limits.control_seconds
            while True:
                try:
                    observed = await self._copy_control(container_id)
                except ControlError:
                    if asyncio.get_running_loop().time() >= end:
                        raise
                    await asyncio.sleep(0.05)
                else:
                    if observed is None:
                        require(
                            asyncio.get_running_loop().time() < end,
                            "Protected supervisor startup deadline exceeded",
                        )
                        await asyncio.sleep(0.05)
                        continue
                    self.profile.inspect_effective(observed)
                    break
            lease = await fact({"exec_create_intent": True})
            execution_id = await engine.create_exec(container_id, WORKLOAD, argument=code)
            lease = await fact({"exec_id": execution_id})
            # The separate start marker is saved before the first possible code.
            lease = await fact({"exec_start_intent": True})
            output = await engine.start_exec(
                execution_id,
                seconds=max(0.001, seconds - (asyncio.get_running_loop().time() - started)),
                max_bytes=self.profile.limits.max_output_bytes,
            )
            state = await engine.inspect_exec(execution_id)
            require(
                state.get("ID") == execution_id and state.get("ContainerID") == container_id,
                "Original Engine exec identity differs",
            )
            process = state.get("ProcessConfig")
            require(
                isinstance(process, dict)
                and process.get("user") == "65533:65533"
                and process.get("privileged") is False
                and process.get("tty") is False,
                "Original workload process controls differ",
            )
            process = cast(dict[str, Any], process)
            require(
                process.get("entrypoint") == "/usr/local/bin/python3"
                and process.get("arguments") == ["-I", "-B", "-c", WORKLOAD, code],
                "Original workload program differs",
            )
            exit_code = state.get("ExitCode") if state.get("Running") is False else None
            if output.termination == "exited":
                require(type(exit_code) is int, "Workload exit observation is unavailable")
            artifact_status = "not_requested" if not lease.outputs else "rejected"
            error = None if not lease.outputs else "code_not_completed"
            if output.termination == "exited" and exit_code == 0 and lease.outputs:
                spec = canonical_json(
                    {
                        "names": lease.outputs,
                        "single": file_limits.max_artifact_bytes,
                        "total": file_limits.max_export_bytes,
                    }
                )
                exporter = await engine.create_exec(
                    container_id, EXPORT, argument=spec, control=True
                )
                files = await engine.start_exec(
                    exporter,
                    seconds=self.profile.limits.control_seconds,
                    max_bytes=((file_limits.max_export_bytes + 2) // 3 * 4) + 65536,
                )
                exported_state = await engine.inspect_exec(exporter)
                if (
                    files.termination == "exited"
                    and exported_state.get("Running") is False
                    and exported_state.get("ExitCode") == 0
                ):
                    payload = load_json(files.stdout)
                    if isinstance(payload, dict) and isinstance(payload.get("files"), list):
                        entries = cast(list[Any], payload["files"])
                        require(len(entries) == len(lease.outputs), "Declared output set differs")
                        for name, entry in zip(lease.outputs, entries, strict=True):
                            require(
                                isinstance(entry, dict) and entry.get("name") == name,
                                "Output declaration differs",
                            )
                            raw = base64.b64decode(entry["data"], validate=True)
                            require(
                                hashlib.sha256(raw).hexdigest() == entry.get("sha256"),
                                "Output transfer hash differs",
                            )
                            media = (
                                "text/csv" if name.endswith(".csv") else "application/octet-stream"
                            )
                            if name.lower().endswith(".png"):
                                checking = asyncio.create_task(asyncio.to_thread(validate_png, raw))
                                try:
                                    await asyncio.shield(checking)
                                except asyncio.CancelledError:
                                    await _settle(checking)
                                    raise
                                except ValueError:
                                    exported = []
                                    error = "invalid_png"
                                    break
                                media = "image/png"
                            exported.append(
                                ArtifactInput(
                                    name=name.split("/")[-1],
                                    data=raw,
                                    media_type=media,
                                )
                            )
                        else:
                            artifact_status, error = "complete", None
                    else:
                        error = "export_rejected"
                else:
                    error = "export_unavailable"
            lease = await fact(
                {
                    "observation": {
                        "execution_key": lease.execution_key,
                        "exit_code": exit_code,
                        "termination": output.termination,
                        "stdout": output.stdout.decode("utf-8", errors="replace"),
                        "stderr": output.stderr.decode("utf-8", errors="replace"),
                        "stdout_truncated": output.stdout_truncated,
                        "stderr_truncated": output.stderr_truncated,
                        "artifact_status": artifact_status,
                        "artifact_error": error,
                    }
                }
            )
            return lease, exported
        except asyncio.CancelledError:
            raise
        except (ValueError, TypeError, KeyError, EngineError) as exc:
            raise ConfigurationError("Sandbox result or control evidence is unavailable") from exc
        # Deliberately no successful cleanup/result fabrication here. Runtime
        # stages bytes first, then calls destroy under an independent deadline.
