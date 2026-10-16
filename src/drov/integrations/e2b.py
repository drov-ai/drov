"""Experimental, opt-in E2B single-execution backend. Construction performs no I/O."""

import asyncio
import base64
import hashlib
import importlib
import importlib.metadata
import os
import re
import shlex
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Self, TypeVar, cast

from pydantic import Field, model_validator

from drov._json import canonical_json, load_json
from drov.artifacts.types import ArtifactInput, ArtifactLimits, file_path
from drov.errors import CapacityExceeded, ConfigurationError, ShutdownIncomplete
from drov.sandbox._e2b_runner import RUN, SETUP
from drov.sandbox._interface import FactWriter, SandboxBackend
from drov.sandbox._leases import SandboxLease
from drov.sandbox._png import validate_png
from drov.sandbox._profile import configuration_hash, require
from drov.sandbox._runner import EXPORT
from drov.storage.sqlite import SQLiteDriver
from drov.storage.transaction import Transaction
from drov.types import WireModel

T = TypeVar("T")
SDK_VERSION = "2.56.0"
PACKAGES = {"numpy": "2.4.6", "pandas": "3.0.6", "matplotlib": "3.11.2"}


class E2BLimits(WireModel):
    execution_seconds: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    control_seconds: float = Field(default=30, gt=0, le=60, allow_inf_nan=False)
    lifetime_seconds: int = Field(default=180, ge=120, le=300)
    max_code_bytes: int = Field(default=64 * 1024, ge=1, le=64 * 1024)
    max_output_bytes: int = Field(default=64 * 1024, ge=1, le=64 * 1024)
    concurrency: Literal[1] = 1

    @model_validator(mode="after")
    def deadlines(self) -> Self:
        if self.execution_seconds + 10 > self.control_seconds:
            raise ValueError("Control deadline must cover workload and monitor cleanup")
        return self


@dataclass(frozen=True)
class _Profile:
    limits: E2BLimits


class E2BSandbox(SandboxBackend):
    """Host resolves credentials; only explicitly allowed code/files go to E2B.

    Budget is a conservative cumulative reservation per SQLite database/budget_id,
    not an account balance or invoice. A reservation is never refunded. Region is
    a host declaration about the project; this SDK cannot attest placement.
    """

    lease_schema_version = 2

    def __init__(
        self,
        *,
        template_id: str,
        build_id: str,
        credential_ref: str,
        resolve_credential: Callable[[str], str],
        region: str,
        cloud_data_policy: Literal["allow"],
        budget_id: str,
        max_cost_micro_usd: int,
        rate_micro_usd_per_second: int,
        limits: E2BLimits | None = None,
        require_region_attestation: bool = False,
    ) -> None:
        require(
            bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", template_id)), "Fixed template ID required"
        )
        require(
            bool(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", build_id)),
            "Immutable E2B build UUID required; mutable tags are not accepted",
        )
        for value in (credential_ref, region, budget_id):
            require(
                type(value) is str and bool(re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", value)),
                "Explicit non-secret credential, region and budget references required",
            )
        require(cloud_data_policy == "allow", "Explicit cloud data upload permission required")
        require(callable(resolve_credential), "Host credential resolver required")
        require(not require_region_attestation, "E2B SDK cannot attest or enforce project region")
        require(
            type(max_cost_micro_usd) is int and max_cost_micro_usd > 0,
            "Finite independent E2B cost budget required",
        )
        require(
            type(rate_micro_usd_per_second) is int and rate_micro_usd_per_second >= 37,
            "An upper rate quote covering 2 vCPU and 2 GiB is required",
        )
        self._limits = E2BLimits.from_json((limits or E2BLimits()).model_dump_json())
        self._template, self._build = template_id, build_id
        self._credential_ref, self._resolve = credential_ref, resolve_credential
        self._region, self._budget = region, budget_id
        self._ceiling, self._rate = max_cost_micro_usd, rate_micro_usd_per_second
        self._state = "new"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._binding: dict[str, Any] | None = None
        self._sdk: Any = None
        self._active: set[str] = set()

    def _require_loop(self) -> None:
        loop = asyncio.get_running_loop()
        require(self._loop is None or self._loop is loop, "E2B backend belongs to another loop")
        self._loop = loop

    @property
    def is_open(self) -> bool:
        return self._state == "open"

    @property
    def profile(self) -> _Profile:
        require(self.is_open, "E2B backend is not open")
        return _Profile(self._limits)

    @property
    def binding(self) -> dict[str, Any]:
        require(self.is_open and self._binding is not None, "E2B backend is not open")
        return cast(dict[str, Any], load_json(canonical_json(self._binding)))

    @property
    def config_hash(self) -> str:
        return configuration_hash(self.binding)

    @property
    def reservation_micro_usd(self) -> int:
        # Covers the entire server lifetime, including failed execution/cleanup;
        # 1 second rounding plus a control/start allowance. Build is separate.
        return (self._limits.lifetime_seconds + 1) * self._rate

    def _opts(self) -> dict[str, Any]:
        require(not os.environ.get("E2B_SANDBOX_URL"), "Sandbox URL overrides are forbidden")
        try:
            key = self._resolve(self._credential_ref)
        except Exception:
            raise ConfigurationError("Host E2B credential resolution failed") from None
        require(type(key) is str and bool(key.strip()), "Host E2B credential is unavailable")
        return {
            "api_key": key,
            "api_url": "https://api.e2b.dev",
            "domain": "e2b.dev",
            "debug": False,
            "request_timeout": self._limits.control_seconds,
            "retries": 0,
            "http_version": "1.1",
        }

    async def _call(self, call: Awaitable[T]) -> T:
        try:
            async with asyncio.timeout(self._limits.control_seconds):
                return await call
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ConfigurationError("E2B control evidence is unavailable") from None

    async def open(self) -> None:
        self._require_loop()
        require(self._state == "new", "E2B backend is single-use")
        self._state = "opening"
        try:
            try:
                version = importlib.metadata.version("e2b")
            except importlib.metadata.PackageNotFoundError:
                raise ConfigurationError("Install the drov[e2b] extra to use E2B") from None
            require(version == SDK_VERSION, "Untested E2B SDK version")
            self._sdk = importlib.import_module("e2b")
            await self._check_build()
            require(self._state == "opening", "E2B backend closed during startup")
            self._binding = {
                "backend": "e2b",
                "controller_version": 1,
                "sdk_version": SDK_VERSION,
                "template_id": self._template,
                "build_id": self._build,
                "credential_ref": self._credential_ref,
                "region_declared": self._region,
                "region_attested": False,
                "cloud_data_policy": "allow",
                "limits": self._limits.model_dump(mode="json"),
                "cpu_count": 2,
                "memory_mb": 2048,
                "workspace_bytes": 64 * 1024**2,
                "workload_memory_bytes": 512 * 1024**2,
                "workload_pids": 64,
                "max_file_bytes": 8 * 1024**2,
                "allow_internet_access": False,
                "allow_public_traffic": False,
                "budget_id": self._budget,
                "max_cost_micro_usd": self._ceiling,
                "reservation_micro_usd": self.reservation_micro_usd,
                "rate_micro_usd_per_second": self._rate,
                "program_hashes": {
                    n: hashlib.sha256(p.encode()).hexdigest()
                    for n, p in (("setup", SETUP), ("run", RUN), ("export", EXPORT))
                },
            }
            self._state = "open"
        except BaseException:
            self._state = "closed"
            self._sdk = None
            raise

    async def _check_build(self) -> None:
        build_type = importlib.import_module("e2b.template.types").BuildInfo
        info = build_type(
            template_id=self._template,
            build_id=self._build,
            name=self._template,
            alias=self._template,
        )
        status = await self._call(self._sdk.AsyncTemplate.get_build_status(info, **self._opts()))
        require(
            status.status == "ready"
            and status.template_id == self._template
            and status.build_id == self._build,
            "Original E2B template build is not ready",
        )

    async def revalidate(self) -> None:
        self._require_loop()
        require(self.is_open, "E2B backend is not open")
        await self._check_build()

    async def reserve_in(self, tx: Transaction, lease: SandboxLease) -> None:
        require(
            isinstance(tx._driver, SQLiteDriver), "E2B requires the durable SQLite lease ledger"
        )
        driver = cast(SQLiteDriver, tx._driver)
        from drov.storage._retention import cost_key, retained_cost

        retained, _ = await driver.execute(
            "SELECT value FROM metadata WHERE key=?", (cost_key(tx._secret, self._budget),)
        )
        rows, _ = await driver.execute("SELECT payload FROM sandbox_leases WHERE active IN (0,1)")
        total = self.reservation_micro_usd + (retained_cost(retained[0][0]) if retained else 0)
        for (payload,) in rows:
            previous = SandboxLease.from_json(cast(str, payload))
            config = previous.backend_config
            if (
                previous.create_intent
                and config.get("backend") == "e2b"
                and config.get("budget_id") == self._budget
            ):
                reservation = config.get("reservation_micro_usd")
                require(type(reservation) is int and reservation > 0, "Invalid E2B cost ledger")
                total += cast(int, reservation)
        if total > self._ceiling:
            raise CapacityExceeded("Conservative E2B lifetime budget exhausted")

    def _metadata(self, lease: SandboxLease) -> dict[str, str]:
        require(lease.backend_hash == self.config_hash, "Original E2B configuration differs")
        return {
            "drov_execution": lease.execution_key,
            "drov_owner": configuration_hash(lease.owner.model_dump(mode="json")),
            "drov_config": lease.backend_hash,
        }

    def _check_info(self, info: Any, lease: SandboxLease, *, controls: bool = True) -> None:
        require(
            info.metadata == self._metadata(lease) and info.template_id == self._template,
            "Original E2B resource ownership differs",
        )
        if lease.remote_id is not None:
            require(info.sandbox_id == lease.remote_id, "Original E2B resource ID differs")
        if not controls:
            return  # Cleanup authenticates ownership, even when execution controls are invalid.
        require(
            info.cpu_count == 2
            and info.memory_mb == 2048
            and info.allow_internet_access is False
            and info.network == {"allow_public_traffic": False}
            and info.lifecycle == {"on_timeout": "kill", "auto_resume": False}
            and not info.volume_mounts,
            "Required E2B VM/network/lifecycle controls differ",
        )
        require(
            (info.end_at - info.started_at).total_seconds() <= self._limits.lifetime_seconds + 1,
            "E2B resource lifetime exceeds its reservation",
        )

    async def resource(self, lease: SandboxLease) -> dict[str, Any] | None:
        self._require_loop()
        metadata = self._metadata(lease)
        if lease.remote_id is not None:
            try:
                info = await self._sdk.AsyncSandbox.get_info(lease.remote_id, **self._opts())
            except self._sdk.SandboxNotFoundException:
                return None
            except Exception:
                raise ConfigurationError("Original E2B query is unavailable") from None
            self._check_info(info, lease, controls=False)
            return {"id": info.sandbox_id}
        paginator = self._sdk.AsyncSandbox.list(
            query=self._sdk.SandboxQuery(metadata=metadata), limit=100, **self._opts()
        )
        found = []
        async with asyncio.timeout(self._limits.control_seconds):
            for _ in range(10):
                if not paginator.has_next:
                    break
                found.extend(await self._call(paginator.next_items()))
            else:
                raise ConfigurationError("E2B resource lookup exceeds its page bound")
        require(len(found) <= 1, "Original E2B execution key has conflicting resources")
        if not found:
            return None
        info = await self._call(
            self._sdk.AsyncSandbox.get_info(found[0].sandbox_id, **self._opts())
        )
        self._check_info(info, lease, controls=False)
        return {"id": info.sandbox_id}

    async def destroy(self, lease: SandboxLease, fact: FactWriter) -> SandboxLease:
        if lease.destroyed:
            self._active.discard(lease.execution_key)
            return lease
        async with asyncio.timeout(self._limits.control_seconds):
            resource = await self.resource(lease)
            if resource is None:
                require(
                    lease.remote_id is not None or not lease.create_intent,
                    "Lost E2B create acknowledgement; absence does not prove cleanup",
                )
            else:
                if lease.remote_id is None:
                    lease = await fact({"remote_id": resource["id"]})
                await self._call(self._sdk.AsyncSandbox.kill(resource["id"], **self._opts()))
                require(await self.resource(lease) is None, "E2B destruction is unconfirmed")
            lease = await fact({"destroyed": True})
            self._active.discard(lease.execution_key)
            return lease

    async def _program(self, sandbox: Any, program: str, *, argument: str | None = None) -> str:
        command = "python3 -I -B -c " + shlex.quote(program)
        if argument is not None:
            command += " " + shlex.quote(argument)
        result = await self._call(
            sandbox.commands.run(
                command,
                user="root",
                cwd="/work",
                timeout=self._limits.control_seconds,
                request_timeout=self._limits.control_seconds,
            )
        )
        require(result.exit_code == 0, "Trusted E2B control program failed")
        return cast(str, result.stdout)

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
        self._require_loop()
        require(
            lease.active
            and lease.create_intent
            and lease.remote_id is None
            and not lease.remote_start_intent
            and lease.observation is None,
            "A fresh committed E2B intent is required; never redispatch old code",
        )
        metadata = self._metadata(lease)
        require(
            type(code) is str
            and bool(code)
            and "\0" not in code
            and len(code.encode()) <= self._limits.max_code_bytes
            and hashlib.sha256(code.encode()).hexdigest() == lease.code_hash,
            "Approved E2B code differs",
        )
        require(
            len(inputs) == len(lease.inputs)
            and len(inputs) <= file_limits.max_input_files
            and sum(len(i.data) for i in inputs) <= min(file_limits.max_input_bytes, 15 * 1024**2),
            "Approved E2B input set exceeds its bound",
        )
        for item, grant in zip(inputs, lease.inputs, strict=True):
            require(
                item.name == grant.name
                and len(item.data) == grant.ref.size_bytes
                and hashlib.sha256(item.data).hexdigest() == grant.ref.sha256,
                "Approved E2B input bytes differ",
            )
        require(
            len(lease.outputs) <= file_limits.max_output_files
            and all(file_path(n) == n for n in lease.outputs)
            and len({n.split("/")[-1] for n in lease.outputs}) == len(lease.outputs),
            "Approved E2B output declarations differ",
        )
        started = asyncio.get_running_loop().time()
        self._active.add(lease.execution_key)
        # No retry or second create after any failure, including lost replies.
        sandbox = await self._call(
            self._sdk.AsyncSandbox.create(
                self._template + ":" + self._build,
                timeout=self._limits.lifetime_seconds,
                metadata=metadata,
                envs={},
                allow_internet_access=False,
                network={"allow_public_traffic": False},
                lifecycle={"on_timeout": "kill", "auto_resume": False},
                **self._opts(),
            )
        )
        lease = await fact({"remote_id": sandbox.sandbox_id})
        info = await self._call(sandbox.get_info(**self._opts()))
        self._check_info(info, lease)
        setup = load_json(await self._program(sandbox, SETUP))
        require(
            isinstance(setup, dict)
            and setup.get("uid") == 0
            and setup.get("workspace_bytes") == 64 * 1024**2
            and setup.get("cpu_count") == 2
            and setup.get("packages") == PACKAGES,
            "E2B guest profile differs",
        )
        remaining = seconds - (asyncio.get_running_loop().time() - started)
        require(remaining > 0, "E2B execution deadline exhausted before start")
        request = canonical_json(
            {
                "code": code,
                "seconds": min(remaining, self._limits.execution_seconds),
                "max_output_bytes": self._limits.max_output_bytes,
                "inputs": [
                    {
                        "id": g.ref.artifact_id,
                        "name": i.name,
                        "sha256": g.ref.sha256,
                        "data": base64.b64encode(i.data).decode("ascii"),
                    }
                    for i, g in zip(inputs, lease.inputs, strict=True)
                ],
            }
        )
        await self._call(sandbox.files.write("/opt/drov-job/request.json", request, user="root"))
        # Permission is rechecked by Runtime at this durable start barrier.
        lease = await fact({"remote_start_intent": True})
        remaining = seconds - (asyncio.get_running_loop().time() - started)
        require(remaining > 0, "E2B execution deadline exhausted before dispatch")
        raw = await self._program(sandbox, RUN, argument=str(remaining))
        require(
            len(raw.encode()) <= self._limits.max_output_bytes * 6 + 4096, "E2B result too large"
        )
        observation = load_json(raw)
        require(isinstance(observation, dict), "Invalid E2B workload observation")
        observation = cast(dict[str, Any], observation)
        exported: list[ArtifactInput] = []
        status, error = (
            ("not_requested", None) if not lease.outputs else ("rejected", "code_not_completed")
        )
        if (
            observation.get("termination") == "exited"
            and observation.get("exit_code") == 0
            and lease.outputs
        ):
            spec = canonical_json(
                {
                    "names": lease.outputs,
                    "single": min(file_limits.max_artifact_bytes, 8 * 1024**2),
                    "total": min(file_limits.max_export_bytes, 16 * 1024**2),
                }
            )
            raw = await self._program(sandbox, EXPORT, argument=spec)
            require(len(raw.encode()) <= 24 * 1024**2, "E2B export too large")
            payload = load_json(raw)
            if isinstance(payload, dict) and isinstance(payload.get("files"), list):
                entries = cast(list[Any], payload["files"])
                require(len(entries) == len(lease.outputs), "E2B export set differs")
                for name, entry in zip(lease.outputs, entries, strict=True):
                    require(
                        isinstance(entry, dict) and entry.get("name") == name,
                        "E2B export name differs",
                    )
                    data = base64.b64decode(entry["data"], validate=True)
                    require(
                        hashlib.sha256(data).hexdigest() == entry.get("sha256"),
                        "E2B export hash differs",
                    )
                    require(
                        len(data) <= min(file_limits.max_artifact_bytes, 8 * 1024**2),
                        "E2B file bound exceeded",
                    )
                    media = (
                        "text/csv" if name.lower().endswith(".csv") else "application/octet-stream"
                    )
                    if name.lower().endswith(".png"):
                        try:
                            validate_png(data)
                        except ValueError:
                            exported = []
                            error = "invalid_png"
                            break
                        media = "image/png"
                    exported.append(
                        ArtifactInput(name=name.split("/")[-1], data=data, media_type=media)
                    )
                else:
                    require(
                        sum(len(i.data) for i in exported)
                        <= min(file_limits.max_export_bytes, 16 * 1024**2),
                        "E2B total export bound exceeded",
                    )
                    status, error = "complete", None
            else:
                error = "export_rejected"
        observation.update(
            execution_key=lease.execution_key, artifact_status=status, artifact_error=error
        )
        lease = await fact({"observation": observation})
        return lease, exported

    async def aclose(self) -> None:
        self._require_loop()
        if self._active:
            raise ShutdownIncomplete("Unconfirmed E2B resources retain backend ownership")
        self._state = "closed"
        self._sdk = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()


__all__ = ["E2BLimits", "E2BSandbox"]
