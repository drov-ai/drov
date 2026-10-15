"""Clean wheel + E2B extra import/preflight. Cloud calls need explicit --live-config."""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = r"""
import asyncio,importlib.metadata,pathlib,sys
from unittest.mock import patch
from types import SimpleNamespace
from drov.integrations.e2b import E2BSandbox
import drov,e2b
assert pathlib.Path(drov.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix))
assert importlib.metadata.version('e2b')=='2.56.0'
async def ready(info,**opts):
    assert opts['api_key']=='synthetic-installed-credential' and opts['retries']==0
    return SimpleNamespace(status='ready',template_id=info.template_id,build_id=info.build_id)
async def main():
    backend=E2BSandbox(template_id='fixture',build_id='347a1104-f90d-4644-8024-12fc30619da0',
        credential_ref='fixture',resolve_credential=lambda _:'synthetic-installed-credential',
        region='project-default',cloud_data_policy='allow',budget_id='fixture',
        max_cost_micro_usd=100000,rate_micro_usd_per_second=100)
    with patch.object(e2b.AsyncTemplate,'get_build_status',ready):
        async with backend:
            await backend.revalidate()
            assert backend.binding['allow_public_traffic'] is False
asyncio.run(main())
print('installed E2B import/preflight passed; real_cloud=false')
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--python", default="3.11")
    parser.add_argument("--live-config", type=Path)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    uv = shutil.which(args.uv)
    if uv is None:
        raise ValueError("Pinned uv is required")
    uv = str(Path(uv).resolve())
    with tempfile.TemporaryDirectory(prefix="drov-installed-e2b-") as scratch:
        work = Path(scratch)
        env = work / "environment"
        subprocess.run([uv, "venv", "--python", args.python, str(env)], check=True, timeout=60)
        python = env / ("Scripts/python.exe" if __import__("os").name == "nt" else "bin/python")
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), str(args.wheel.resolve()) + "[e2b]"],
            check=True,
            timeout=180,
        )
        subprocess.run(
            [str(python), "-I", "-B", str(ROOT / "scripts/check_installed.py"), "--installed"],
            cwd=work,
            check=True,
            timeout=60,
        )
        probe = work / "probe.py"
        probe.write_text(PROBE)
        subprocess.run([str(python), "-I", "-B", str(probe)], cwd=work, check=True, timeout=60)
        if args.live_config:
            if args.directory is None:
                raise ValueError("Live acceptance requires an output directory")
            config = json.loads(args.live_config.read_text())
            # The parent reserves these two calls in the separate batch ledger.
            for stream in (False, True):
                command = [
                    str(python),
                    "-B",
                    str(ROOT / "scripts/check_e2b_live.py"),
                    "--env-file",
                    config["env_file"],
                    "--template-record",
                    config["template_record"],
                    "--directory",
                    str(args.directory.resolve() / ("stream" if stream else "sdk")),
                ]
                if stream:
                    command.append("--stream")
                subprocess.run(command, cwd=work, check=True, timeout=120)


if __name__ == "__main__":
    main()
