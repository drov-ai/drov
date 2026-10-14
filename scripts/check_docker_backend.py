"""Explicit real Docker profile acceptance with trusted code; no Agent executor."""

import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from drov.errors import ConfigurationError
from drov.sandbox._docker import DockerPreflight
from drov.sandbox._profile import require

PROBE = r"""
import json,os,pathlib,sys
status=pathlib.Path('/proc/self/status').read_text().splitlines()
result={k:next(x.split(':',1)[1].strip() for x in status if x.startswith(k+':'))
        for k in ['Seccomp','NoNewPrivs','CapEff']}
for name in ['memory.max','memory.swap.max','cpu.max','pids.max']:
    result[name]=pathlib.Path('/sys/fs/cgroup',name).read_text().strip()
result.update(uid=os.geteuid(),root_readonly=bool(os.statvfs('/').f_flag & os.ST_RDONLY),
              python=sys.version.split()[0],sum=sum(range(101)),
              previous_file=pathlib.Path('/work/sentinel').exists(),
              environment_names=sorted(os.environ))
pathlib.Path('/work/sentinel').write_text('first operation only')
print(json.dumps(result))
"""


def record(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def check(args: argparse.Namespace) -> None:
    directory: Path = args.work_directory.absolute()
    backend = DockerPreflight(image=args.image, docker=args.docker, host=args.host)
    async with backend:
        control = backend._control
        assert control is not None
        label = uuid4().hex
        labels = {"dev.drov.fixture": label}
        selector = [
            "container",
            "ls",
            "--all",
            "--filter",
            "label=dev.drov.fixture=" + label,
            "--format",
            "{{.ID}}",
        ]
        require(not await control.command(selector), "Unexpected fixture resource before create")
        profile = backend.profile
        seccomp = str(control.directory / "seccomp.json")
        results = []
        for index in range(2):
            name = "drov-control-probe-" + label + "-" + str(index)
            path = directory / ("lease-" + str(index) + ".json")
            intent = {
                "name": name,
                "labels": labels,
                "image_id": profile.image_id,
                "config_hash": backend.config_hash,
                "status": "intent",
                "scope": "trusted fixture only; not a Runtime execution lease",
            }
            record(path, intent)
            create_acknowledged = False
            try:
                raw = await control.command(
                    profile.create_arguments(name=name, labels=labels, seccomp=seccomp)
                    + ["-I", "-B", "-c", PROBE]
                )
                container_id = raw.decode("ascii").strip()
                require(
                    re.fullmatch(r"[0-9a-f]{64}", container_id) is not None,
                    "Invalid create response",
                )
                create_acknowledged = True
                created = await control.object(
                    ["container", "inspect", container_id], singleton=True
                )
                record(
                    path,
                    intent
                    | {
                        "status": "created",
                        "container_id": container_id,
                        "security_options": created["HostConfig"]["SecurityOpt"],
                    },
                )
                profile.inspect_created(
                    created,
                    name=name,
                    labels=labels,
                    seccomp=(control.directory / "seccomp.json").read_bytes(),
                )
                record(path, intent | {"status": "created", "container_id": container_id})
                observed = json.loads(
                    await control.command(["container", "start", "--attach", container_id])
                )
                record(
                    path,
                    intent
                    | {"status": "observed", "container_id": container_id, "observation": observed},
                )
                profile.inspect_effective(observed)
                require(
                    observed["sum"] == 5050 and observed["previous_file"] is False,
                    "Independent computation failed",
                )
                require(
                    set(observed["environment_names"])
                    <= {
                        "PATH",
                        "LANG",
                        "GPG_KEY",
                        "PYTHON_VERSION",
                        "PYTHON_SHA256",
                        "HOME",
                        "HOSTNAME",
                    },
                    "Unexpected effective environment name",
                )
                exited = await control.object(
                    ["container", "inspect", container_id], singleton=True
                )
                require(
                    exited["State"]["Status"] == "exited" and exited["State"]["ExitCode"] == 0,
                    "Probe did not exit normally",
                )
                results.append(observed)
                record(
                    path,
                    intent
                    | {"status": "exited", "container_id": container_id, "observation": observed},
                )
            finally:
                # Query the original name if the create response was lost, never
                # create a replacement and never select by a shared image alone.
                named = [
                    "container",
                    "ls",
                    "--all",
                    "--filter",
                    "name=^/" + name + "$",
                    "--format",
                    "{{.ID}}",
                ]
                existing = (await control.command(named)).decode("ascii").strip()
                if existing:
                    original = await control.object(["container", "inspect", name], singleton=True)
                    require(
                        original["Name"] == "/" + name
                        and original["Config"]["Labels"] == labels
                        and original["Image"] == profile.image_id,
                        "Cleanup ownership differs",
                    )
                    await control.command(["container", "rm", "--force", original["Id"]])
                elif not create_acknowledged:
                    record(path, json.loads(path.read_text()) | {"cleanup_unknown": True})
                    raise ConfigurationError(
                        "Create acknowledgement lost and resource absent; retain fixture intent"
                    )
                require(not await control.command(named), "Probe resource removal is unconfirmed")
                record(path, json.loads(path.read_text()) | {"destroyed": True})
        require(not await control.command(selector), "Fixture resources remain")
        report = {
            "config_hash": backend.config_hash,
            "image_id": profile.image_id,
            "architecture": profile.architecture,
            "preflight_create_count": 0,
            "trusted_probe_create_count": 2,
            "results": results,
            "probe_code_sha256": hashlib.sha256(PROBE.encode()).hexdigest(),
            "destroyed": True,
            "scope": (
                "private backend controls only; no model, approval, "
                "Runtime execution or Artifact output"
            ),
        }
        record(directory / "report.json", report)
        print(json.dumps(report, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    args = parser.parse_args()
    args.work_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    asyncio.run(check(args))


if __name__ == "__main__":
    main()
