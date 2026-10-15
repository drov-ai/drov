"""Explicit cloud acceptance with synthetic data. Never runs in ordinary CI."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from _e2b_host import load_key

from drov.integrations.e2b import E2BLimits, E2BSandbox

ROOT = Path(__file__).resolve().parents[1]


async def check(args: argparse.Namespace) -> None:
    # Import only the deterministic local oracle. No live model is involved.
    sys.path.insert(0, str(ROOT))
    from examples.sandbox_offline import check as example

    config = json.loads(args.template_record.read_text())
    if config["status"] != "ready":
        raise ValueError("Ready fixed template record required")
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    backend = E2BSandbox(
        template_id=config["build"]["template_id"],
        build_id=config["build"]["build_id"],
        credential_ref="host-e2b-acceptance",
        resolve_credential=lambda _: load_key(args.env_file),
        region=config["project_region_declared"],
        cloud_data_policy="allow",
        budget_id="t10c-acceptance-v1",
        max_cost_micro_usd=4_000_000,
        rate_micro_usd_per_second=100,
        limits=E2BLimits(execution_seconds=20, control_seconds=40),
    )
    args.chart = True
    await example(args, configured_backend=backend)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--template-record", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(check(args))
    except Exception as exc:
        raise SystemExit("E2B acceptance failed: " + type(exc).__name__) from None


if __name__ == "__main__":
    main()
