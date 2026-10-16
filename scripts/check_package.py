"""Build and verify wheel/sdist artifacts in clean, non-editable environments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, timeout=300)


def one_file(directory: Path, pattern: str) -> Path:
    matches = list(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {pattern} in {directory}, found {len(matches)}")
    return matches[0]


def inspect_archives(wheel: Path, sdist: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert {"drov/__init__.py", "drov/py.typed"} <= names
        assert {"drov/server/static/index.html", "drov/server/static/drov-icon.svg"} <= names
        for file in (ROOT / "src/drov/server/static").rglob("*"):
            if file.is_file():
                assert archive.read(file.relative_to(ROOT / "src").as_posix()) == file.read_bytes()
        assert {"drov/sandbox/seccomp-v27.0.3.json", "drov/sandbox/LICENSE.moby"} <= names
        assert (
            archive.read("drov/sandbox/seccomp-v27.0.3.json")
            == (ROOT / "sandbox/docker/seccomp-v27.0.3.json").read_bytes()
        )
        assert (
            archive.read("drov/sandbox/LICENSE.moby")
            == (ROOT / "sandbox/docker/LICENSE.moby").read_bytes()
        )
        for suffix in ("/licenses/LICENSE", "/licenses/THIRD_PARTY.md", "/METADATA"):
            assert any(name.endswith(suffix) for name in names), suffix
        assert all(name.startswith(("drov/", "drov-")) for name in names)
        assert not any(name.endswith("entry_points.txt") for name in names)
    with tarfile.open(sdist) as archive:
        names = {"/".join(Path(name).parts[1:]) for name in archive.getnames()}
        assert {
            "src/drov/py.typed",
            "pyproject.toml",
            "uv.lock",
            "examples/research_offline.py",
            "examples/context_preview_offline.py",
            "examples/memory_offline.py",
            "examples/interactions_offline.py",
            "examples/operations_offline.py",
            "examples/item_service_fixture.py",
            "examples/artifact_inputs_offline.py",
            "examples/maintenance_offline.py",
            "examples/abandonment_offline.py",
            "examples/sandbox_offline.py",
            "docs/reference/storage-sandbox-schema-v1.sql",
            "workbench/package-lock.json",
            "workbench/src/main.ts",
            "LICENSE",
            "THIRD_PARTY.md",
        } <= names
        forbidden = {
            ".env",
            ".git",
            ".venv",
            ".drov",
            "drov-brand",
            "__pycache__",
            "node_modules",
            "test-results",
            "playwright-report",
        }
        assert not any(forbidden & set(Path(name).parts) for name in names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv", help="Path to the pinned uv executable")
    parser.add_argument(
        "--python", default=sys.executable, help="Interpreter for clean installations"
    )
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    options = parser.parse_args()
    uv_path = shutil.which(options.uv)
    if uv_path is None:
        parser.error("uv not found; supply --uv with the path to uv 0.13.0")
    uv = str(Path(uv_path).resolve())
    checker = ROOT / "scripts/check_installed.py"
    with tempfile.TemporaryDirectory(prefix="drov-package-") as directory:
        temporary = Path(directory)
        built, rebuilt = temporary / "built", temporary / "rebuilt"
        # Explicitly build a wheel from each source form, rather than testing only an editable tree.
        for target in ("--wheel", "--sdist"):
            run(
                [
                    uv,
                    "build",
                    target,
                    "--no-sources",
                    "--python",
                    options.python,
                    "--out-dir",
                    str(built),
                ],
                ROOT,
            )
        wheel, sdist = one_file(built, "*.whl"), one_file(built, "*.tar.gz")
        inspect_archives(wheel, sdist)
        run(
            [
                uv,
                "build",
                str(sdist),
                "--wheel",
                "--no-sources",
                "--python",
                options.python,
                "--out-dir",
                str(rebuilt),
            ],
            temporary,
        )
        rebuilt_wheel = one_file(rebuilt, "*.whl")
        # Compare payloads (not ZIP timestamps) to catch missing sdist inputs.
        with zipfile.ZipFile(wheel) as first, zipfile.ZipFile(rebuilt_wheel) as second:
            assert set(first.namelist()) == set(second.namelist())
            for name in first.namelist():
                assert first.read(name) == second.read(name), name

        requirements = temporary / "core.txt"
        run(
            [
                uv,
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
                "--output-file",
                str(requirements),
                "--quiet",
            ],
            ROOT,
        )
        for label, artifact in (("wheel", wheel), ("sdist-wheel", rebuilt_wheel)):
            environment = temporary / label
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
                    str(artifact),
                ],
                temporary,
            )
            run([uv, "pip", "check", "--python", str(python)], temporary)
            run([str(python), "-I", "-B", str(checker), "--installed", "--no-extras"], temporary)
            if os.name == "posix":
                run(
                    [str(python), "-I", "-B", str(ROOT / "scripts/check_artifact_store.py")],
                    temporary,
                )
            for example, example_args in (
                (name, arguments)
                for name in (
                    "research_offline.py",
                    "context_preview_offline.py",
                    "memory_offline.py",
                    "interactions_offline.py",
                    "operations_offline.py",
                    "maintenance_offline.py",
                    "abandonment_offline.py",
                    *(("artifact_inputs_offline.py",) if os.name == "posix" else ()),
                )
                for arguments in ([], ["--stream"])
            ):
                run(
                    [
                        str(python),
                        "-I",
                        "-B",
                        "-X",
                        "utf8",
                        str(ROOT / "examples" / example),
                        *example_args,
                    ],
                    temporary,
                )

        output = options.out_dir.resolve()
        output.mkdir(parents=True, exist_ok=True)
        for artifact in (wheel, sdist):
            shutil.copy2(artifact, output / artifact.name)
        report = {
            "scope": "package install/import and offline SDK/stream; no live model checks",
            "python": options.python,
            "checks": "passed",
            "artifacts": {
                artifact.name: hashlib.sha256(artifact.read_bytes()).hexdigest()
                for artifact in (wheel, sdist)
            },
        }
        (output / "package-check.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
