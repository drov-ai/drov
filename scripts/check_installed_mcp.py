"""Install the local wheel with the locked MCP extra and run loopback examples."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, timeout=300)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--wheel", type=Path, required=True)
    options = parser.parse_args()
    uv = shutil.which(options.uv)
    if uv is None:
        parser.error("Supply uv 0.13.0 using --uv")
    uv = str(Path(uv).resolve())
    wheel = options.wheel.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="drov-installed-mcp-") as directory:
        temporary = Path(directory)
        requirements = temporary / "requirements.txt"
        run(
            [
                uv,
                "export",
                "--locked",
                "--no-dev",
                "--extra",
                "mcp",
                "--no-emit-project",
                "--output-file",
                str(requirements),
                "--quiet",
            ],
            ROOT,
        )
        environment = temporary / "environment"
        run([uv, "venv", "--python", options.python, str(environment)], temporary)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--requirement",
                str(requirements),
                str(wheel),
            ],
            temporary,
        )
        run([uv, "pip", "check", "--python", str(python)], temporary)
        run(
            [str(python), "-I", "-B", str(ROOT / "scripts/check_installed.py"), "--installed"],
            temporary,
        )
        run(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "import importlib.metadata as m; print('official MCP client:', m.version('mcp'))",
            ],
            temporary,
        )
        example = temporary / "mcp_stdio_offline.py"
        shutil.copyfile(ROOT / "examples/mcp_stdio_offline.py", example)
        shutil.copyfile(ROOT / "examples/mcp_stdio_fixture.py", temporary / "mcp_stdio_fixture.py")
        for arguments in ([], ["--stream"]):
            run([str(python), "-I", "-B", str(example), *arguments], temporary)
        print(
            json.dumps(
                {
                    "python": options.python,
                    "wheel": str(wheel),
                    "checks": "passed",
                    "scope": "installed MCP extra; real local stdio only",
                }
            )
        )


if __name__ == "__main__":
    main()
