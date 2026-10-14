"""Actual trusted CSV/PNG image and byte-store probe; NOT an Agent sandbox executor."""

import argparse
import asyncio
import base64
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.storage import SQLiteStorage
from drov.storage.records import MemoryScope

ROOT = Path(__file__).resolve().parents[1]
CSV = b"value\n12\n18\n30\n"
PROBE = r"""
import os
os.environ.update(MPLCONFIGDIR='/work/mpl',HOME='/work',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
import base64,hashlib,io,json,pathlib,sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image
data=sys.stdin.buffer.read(100)
frame=pd.read_csv(io.BytesIO(data))
values=frame['value'].to_numpy()
summary=pd.DataFrame([{'count':len(values),'sum':int(np.sum(values)),'mean':float(np.mean(values))}])
summary.to_csv('/work/summary.csv',index=False)
figure,axis=plt.subplots(figsize=(4,3),dpi=100)
axis.bar(range(len(values)),values)
axis.set_ylabel('value')
figure.tight_layout()
figure.savefig('/work/chart.png')
plt.close(figure)
with Image.open('/work/chart.png') as image:
    dimensions=image.size
    image.verify()
status=pathlib.Path('/proc/self/status').read_text().splitlines()
controls={k:next(x.split(':',1)[1].strip() for x in status if x.startswith(k+':'))
          for k in ['Seccomp','NoNewPrivs','CapEff']}
for name in ['memory.max','memory.swap.max','cpu.max','pids.max']:
    controls[name]=pathlib.Path('/sys/fs/cgroup',name).read_text().strip()
files={name:base64.b64encode(pathlib.Path('/work',name).read_bytes()).decode('ascii')
       for name in ['summary.csv','chart.png']}
print(json.dumps({'python':sys.version.split()[0],'uid':os.geteuid(),'numpy':np.__version__,'pandas':pd.__version__,
'matplotlib':matplotlib.__version__,'dimensions':dimensions,'controls':controls,'files':files}))
"""


async def stage_probe(work: Path, files: dict[str, bytes]) -> list[dict[str, Any]]:
    scope = MemoryScope(tenant_id="fixture", user_id="fixture", agent_name="image-probe")
    store = LocalArtifacts(directory=work / "artifact-bytes")
    async with SQLiteStorage(work / "artifacts.db") as storage:
        await store.open(storage=storage)
        try:
            reservation = await store._reserve(
                owner=scope,
                run_id="image-probe",
                operation_id="csv",
                purpose="output",
                size_bytes=sum(len(data) for data in files.values()),
            )
            stage = await store._stage(
                reservation.stage_id,
                [
                    ArtifactInput(
                        name=name,
                        data=data,
                        media_type="text/csv" if name.endswith(".csv") else "image/png",
                    )
                    for name, data in files.items()
                ],
            )
        finally:
            await store.aclose()
    # New SQLite/store instances demonstrate persistence after container removal.
    async with SQLiteStorage(work / "artifacts.db") as storage:
        await store.open(storage=storage)
        try:
            refs = []
            for entry in stage.files:
                data = await store._read_staged(scope, stage.stage_id, entry.ref)
                assert data == files[entry.name]
                assert hashlib.sha256(data).hexdigest() == entry.ref.sha256
                refs.append(entry.ref.model_dump(mode="json"))
            return refs
        finally:
            await store.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--image-record", type=Path, required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    args = parser.parse_args()
    if not args.docker.is_absolute() or not args.host.startswith("unix:///"):
        raise ValueError("Explicit trusted CLI and local Unix socket required")
    image_record = json.loads(args.image_record.read_text())
    image_id = image_record["image_id"]
    if not image_id.startswith("sha256:") or len(image_id) != 71:
        raise ValueError("Expected an immutable local image ID")
    work = args.work_directory.absolute()
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    cli = work / "cli"
    cli.mkdir(mode=0o700, exist_ok=True)
    (cli / "config.json").write_text('{"auths":{},"proxies":{}}\n')
    base = [str(args.docker), "--config", str(cli), "--host", args.host]

    def command(arguments: list[str], *, data: bytes | None = None) -> bytes:
        return subprocess.run(
            base + arguments,
            input=data,
            capture_output=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            check=True,
            timeout=40,
        ).stdout

    image = json.loads(command(["image", "inspect", image_id]))[0]
    assert image["Id"] == image_id and image["Os"] == "linux" and image["Architecture"] == "arm64"
    assert not image["Config"].get("Volumes") and not image["Config"].get("Healthcheck")
    assert {item.split("=", 1)[0] for item in image["Config"]["Env"]} <= {
        "PATH",
        "LANG",
        "GPG_KEY",
        "PYTHON_VERSION",
        "PYTHON_SHA256",
    }
    name = "drov-image-probe-" + uuid4().hex
    label = "t10a-analysis-image-probe"
    container_id = None
    try:
        container_id = (
            command(
                [
                    "create",
                    "--name",
                    name,
                    "--label",
                    "dev.drov.fixture=" + label,
                    "--pull=never",
                    "--platform=linux/arm64",
                    "--network=none",
                    "--read-only",
                    "--user=65534:65534",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--security-opt",
                    "seccomp=" + str(ROOT / "sandbox/docker/seccomp-v27.0.3.json"),
                    "--memory=512m",
                    "--memory-swap=512m",
                    "--cpus=1",
                    "--pids-limit=64",
                    "--tmpfs",
                    "/work:rw,noexec,nosuid,nodev,size=64m,mode=1777",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                    "--shm-size=16m",
                    "--workdir=/work",
                    "--log-driver=none",
                    "--no-healthcheck",
                    "--interactive",
                    "--entrypoint=/usr/local/bin/python3",
                    image_id,
                    "-I",
                    "-B",
                    "-c",
                    PROBE,
                ]
            )
            .decode()
            .strip()
        )
        inspection = json.loads(command(["inspect", container_id]))[0]
        host = inspection["HostConfig"]
        assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"]
        assert not host["Binds"] and not host["Devices"] and not inspection["Mounts"]
        assert not host["PortBindings"] and not host["Privileged"]
        assert host["Memory"] == host["MemorySwap"] == 536870912
        assert host["NanoCpus"] == 1000000000 and host["PidsLimit"] == 64
        assert host["LogConfig"]["Type"] == "none"
        result = json.loads(command(["start", "--attach", "--interactive", container_id], data=CSV))
        state = json.loads(command(["inspect", container_id]))[0]["State"]
        assert state["Status"] == "exited" and state["ExitCode"] == 0 and not state["OOMKilled"]
        assert result["uid"] == 65534 and result["dimensions"] == [400, 300]
        assert result["controls"] == {
            "Seccomp": "2",
            "NoNewPrivs": "1",
            "CapEff": "0000000000000000",
            "memory.max": "536870912",
            "memory.swap.max": "0",
            "cpu.max": "100000 100000",
            "pids.max": "64",
        }
        expected = json.loads((ROOT / "sandbox/analysis/wheels-linux-arm64.json").read_text())
        for package in expected["packages"]:
            if package["name"] in ("numpy", "pandas", "matplotlib"):
                assert result[package["name"]] == package["version"]
        files = {
            key: base64.b64decode(value, validate=True)
            for key, value in result.pop("files").items()
        }
        assert files["summary.csv"] == b"count,sum,mean\n3,60,20.0\n"
        assert files["chart.png"].startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        # Only the fixture's matching resource can be removed; never stop the daemon.
        if container_id is not None:
            check = json.loads(command(["inspect", container_id]))[0]
            assert (
                check["Name"] == "/" + name
                and check["Config"]["Labels"]["dev.drov.fixture"] == label
            )
            command(["rm", "--force", container_id])
            assert not command(
                ["ps", "--all", "--filter", "id=" + container_id, "--format", "{{.ID}}"]
            )
    result["container_destroyed"] = True
    result["staged_refs"] = asyncio.run(stage_probe(work, files))
    result["input_sha256"] = hashlib.sha256(CSV).hexdigest()
    result["image_id"] = image_id
    result["scope"] = (
        "trusted image/store probe; no Agent loop, Run grant, approval, "
        "ready publication or authorized download"
    )
    (work / "probe.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
