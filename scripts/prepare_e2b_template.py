"""Opt-in private E2B template preparation; uploads only a fixed public recipe."""

import argparse
import asyncio
import base64
import importlib.metadata
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from _e2b_host import load_key

ROOT = Path(__file__).resolve().parents[1]


async def prepare(options: argparse.Namespace) -> None:
    from e2b import AsyncTemplate
    from e2b.template.types import BuildInfo

    if importlib.metadata.version("e2b") != "2.56.0":
        raise ValueError("Template preparation requires the locked E2B SDK")
    key = load_key(options.env_file)
    opts: dict[str, Any] = {
        "api_key": key,
        "retries": 0,
        "request_timeout": 30,
        "api_url": "https://api.e2b.dev",
        "domain": "e2b.dev",
        "debug": False,
        "http_version": "1.1",
    }
    if options.status:
        record = json.loads(options.out.read_text())
        build = BuildInfo(**record["build"])
        status = await AsyncTemplate.get_build_status(build, **opts)
        record["status"] = status.status.value
        options.out.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"status": record["status"], "build": record["build"]}))
        return
    if options.out.exists():
        raise ValueError("Build record already exists; use --status, never silently rebuild")
    manifest = json.loads((ROOT / "sandbox/analysis/wheels-linux-amd64.json").read_text())
    requirements = (ROOT / "sandbox/analysis/requirements-linux-amd64.lock").read_bytes()
    encoded = base64.b64encode(requirements).decode("ascii")
    # No directory/context copy: credentials, repository data and brand assets
    # cannot enter this template. Package hashes/notices reuse the Docker recipe.
    template = (
        AsyncTemplate()
        .from_image(manifest["base_image"])
        .set_user("root")
        .run_cmd(
            "mkdir -p /opt/drov && printf %s "
            + encoded
            + " | base64 -d > /opt/drov/requirements.lock && "
            "python3 -m pip install --disable-pip-version-check --no-cache-dir "
            "--require-hashes -r /opt/drov/requirements.lock"
        )
        .run_cmd(
            "groupadd -g 65533 drov-code && "
            "useradd -u 65533 -g 65533 -M -s /usr/sbin/nologin drov-code && "
            "mkdir -p /work && chmod 755 /work"
        )
        .set_user("root")
        .set_workdir("/work")
    )
    build = await AsyncTemplate.build_in_background(
        template, "drov-python-analysis-v1", cpu_count=2, memory_mb=2048, **opts
    )
    record = {
        "schema_version": 1,
        "build": asdict(build),
        "status": "building",
        "base_image": manifest["base_image"],
        "sdk_version": "2.56.0",
        "cpu_count": 2,
        "memory_mb": 2048,
        "project_region_declared": "us-west-1",
        "region_verified": False,
    }
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"status": "building", "build": record["build"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--status", action="store_true")
    options = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(prepare(options))
    except Exception as exc:
        # SDK errors can contain response bodies and connection details.
        raise SystemExit("E2B template operation failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    main()
