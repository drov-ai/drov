"""Explicit real Docker normal/stream acceptance from a clean core-only wheel installation."""

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--analysis-record", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    uv = shutil.which(args.uv)
    if uv is None:
        raise ValueError("Pinned uv is required")
    config = json.loads(args.config.read_text())
    analysis = json.loads(args.analysis_record.read_text())
    output = args.directory.absolute()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="drov-installed-docker-") as scratch:
        work = Path(scratch)
        environment = work / "core"
        subprocess.run(
            [uv, "venv", "--python", args.python, str(environment)], check=True, timeout=60
        )
        python = environment / "bin/python"
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), str(args.wheel.absolute())],
            check=True,
            timeout=180,
        )
        subprocess.run(
            [
                str(python),
                "-I",
                "-B",
                str(ROOT / "scripts/check_installed.py"),
                "--installed",
                "--no-extras",
            ],
            cwd=work,
            check=True,
            timeout=60,
        )
        for image, reference in (("stdlib", config["image"]), ("analysis", analysis["image_id"])):
            for stream in (False, True):
                directory = output / (image + ("-stream" if stream else "-sdk"))
                command = [
                    str(python),
                    "-I",
                    "-B",
                    str(ROOT / "examples/sandbox_offline.py"),
                    "--docker",
                    config["docker_cli"],
                    "--host",
                    config["docker_host"],
                    "--image",
                    reference,
                    "--directory",
                    str(directory),
                ]
                if image == "analysis":
                    command.append("--chart")
                if stream:
                    command.append("--stream")
                subprocess.run(command, cwd=work, check=True, timeout=60)
                report = json.loads((directory / "report.json").read_text())
                assert report["status"] == "completed"
    print("Clean core-only wheel: stdlib/analysis x SDK/stream Docker acceptance passed")


if __name__ == "__main__":
    main()
