"""Explicitly enabled cloud fixtures. Only synthetic inputs and owned resources."""

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import portalocker
import pytest

from drov.artifacts import ArtifactInput, ArtifactRef
from drov.artifacts.records import ArtifactGrant
from drov.integrations.e2b import E2BLimits, E2BSandbox
from drov.sandbox._leases import SandboxLease
from tests.test_e2b_contracts import lease_for

ROOT = Path(__file__).resolve().parents[1]


def configuration() -> dict[str, Any]:
    location = os.environ.get("DROV_E2B_TEST_CONFIG")
    if not location:
        pytest.skip("Explicit E2B credentials, synthetic upload permission and budget required")
    return cast(dict[str, Any], json.loads(Path(location).read_text()))


def configured(config: dict[str, Any], **changes: Any) -> E2BSandbox:
    sys.path.insert(0, str(ROOT / "scripts"))
    from _e2b_host import load_key

    template = json.loads(Path(config["template_record"]).read_text())
    options: dict[str, Any] = dict(
        template_id=template["build"]["template_id"],
        build_id=template["build"]["build_id"],
        credential_ref="host-e2b-acceptance",
        resolve_credential=lambda _: load_key(Path(config["env_file"])),
        region=template["project_region_declared"],
        cloud_data_policy="allow",
        budget_id="t10c-acceptance-v1",
        max_cost_micro_usd=4_000_000,
        rate_micro_usd_per_second=100,
        limits=E2BLimits(execution_seconds=20, control_seconds=40),
    )
    return E2BSandbox(**(options | changes))


def reserve(config: dict[str, Any], key: str, reservation: int) -> None:
    path = Path(config["cost_ledger"])
    # Separate batch guard shared by every test process/database. This only
    # accounts conservative quotes; it is never described as an E2B invoice.
    with portalocker.Lock(str(path) + ".lock", timeout=5):
        record = json.loads(path.read_text())
        assert sum(i["upper_micro_usd"] for i in record["reservations"]) + reservation <= 5_000_000
        assert key not in {i["key"] for i in record["reservations"]}
        record["reservations"].append({"key": key, "upper_micro_usd": reservation})
        path.write_text(json.dumps(record, indent=2) + "\n")


class Execution:
    def __init__(
        self,
        config: dict[str, Any],
        backend: E2BSandbox,
        code: str,
        inputs: list[ArtifactInput] | None = None,
        outputs: list[str] | None = None,
    ) -> None:
        self.config, self.backend, self.code = config, backend, code
        self.inputs = inputs or []
        self.lease = lease_for(backend, code).model_copy(update={"outputs": outputs or []})
        grants = [
            ArtifactGrant(
                run_id=self.lease.run_id,
                position=n,
                name=i.name,
                ref=ArtifactRef(
                    artifact_id="art_" + uuid4().hex,
                    sha256=hashlib.sha256(i.data).hexdigest(),
                    size_bytes=len(i.data),
                    media_type=i.media_type,
                ),
            )
            for n, i in enumerate(self.inputs)
        ]
        self.lease = self.lease.model_copy(update={"inputs": grants})
        self.directory = Path(config["record_dir"]) / self.lease.execution_key
        self.directory.mkdir(mode=0o700, parents=True)
        reserve(config, self.lease.execution_key, backend.reservation_micro_usd)

    async def fact(self, patch: dict[str, Any]) -> SandboxLease:
        self.lease = SandboxLease.model_validate(
            self.lease.model_dump() | patch | {"version": self.lease.version + 1}
        )
        (self.directory / "lease.json").write_text(self.lease.model_dump_json(indent=2) + "\n")
        return self.lease

    async def execute(self) -> list[ArtifactInput]:
        from drov.artifacts import ArtifactLimits

        await self.fact({})
        self.lease, files = await self.backend.execute(
            self.lease,
            self.code,
            inputs=self.inputs,
            file_limits=ArtifactLimits(),
            fact=self.fact,
            seconds=20,
        )
        return files

    async def cleanup(self) -> None:
        self.lease = await self.backend.destroy(self.lease, self.fact)
        assert self.lease.destroyed and await self.backend.resource(self.lease) is None
