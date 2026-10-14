"""Prepare a pinned Linux ARM64/AMD64 analysis image locally; never publish or run model code."""

import argparse
import email
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "sandbox" / "analysis"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(package: dict[str, Any], target: Path, curl: Path | None = None) -> None:
    parsed = urllib.parse.urlparse(package["url"])
    if parsed.scheme != "https" or parsed.hostname != "files.pythonhosted.org":
        raise ValueError("Only locked PyPI wheel URLs are permitted")
    if target.exists() and digest(target) == package["sha256"]:
        return
    partial = target.with_suffix(".part")
    if curl is not None:
        try:
            subprocess.run(
                [
                    str(curl),
                    "--disable",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--proto",
                    "=https",
                    "--max-filesize",
                    str(64 * 1024**2),
                    "--connect-timeout",
                    "15",
                    "--max-time",
                    "120",
                    "--retry",
                    "5",
                    "--retry-all-errors",
                    "--retry-delay",
                    "1",
                    "--output",
                    str(partial),
                    package["url"],
                ],
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key
                    in {
                        "PATH",
                        "HTTP_PROXY",
                        "HTTPS_PROXY",
                        "ALL_PROXY",
                        "NO_PROXY",
                        "http_proxy",
                        "https_proxy",
                        "all_proxy",
                        "no_proxy",
                        "SSL_CERT_FILE",
                        "SSL_CERT_DIR",
                    }
                },
                check=True,
                timeout=740,
            )
            if digest(partial) != package["sha256"]:
                raise ValueError("Wheel hash differs from the lock")
            partial.replace(target)
            return
        finally:
            partial.unlink(missing_ok=True)
    for attempt in range(3):
        try:
            size = 0
            checksum = hashlib.sha256()
            with urllib.request.urlopen(package["url"], timeout=30) as source:
                if urllib.parse.urlparse(source.url).hostname != "files.pythonhosted.org":
                    raise ValueError("Unexpected wheel download redirect")
                with partial.open("wb") as output:
                    while chunk := source.read(64 * 1024):
                        size += len(chunk)
                        if size > 64 * 1024**2:
                            raise ValueError("Wheel exceeds the preparation size bound")
                        checksum.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            if checksum.hexdigest() != package["sha256"]:
                raise ValueError("Wheel hash differs from the lock")
            partial.replace(target)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
        finally:
            partial.unlink(missing_ok=True)


def inventory(package: dict[str, Any], path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as wheel:
        metadata_path = next(
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = email.message_from_bytes(wheel.read(metadata_path))
        license_files = [
            name
            for name in wheel.namelist()
            if ".dist-info/" in name
            and ("license" in name.lower() or name.lower().endswith("/copying"))
            and not name.endswith("/")
        ]
        return {
            "name": str(metadata["Name"]),
            "version": str(metadata["Version"]),
            "wheel_sha256": package["sha256"],
            "license_expression": (
                str(metadata["License-Expression"]) if metadata["License-Expression"] else None
            ),
            "license_classifiers": [
                str(value)
                for value in metadata.get_all("Classifier", [])
                if value.startswith("License ::")
            ],
            "license_metadata": str(metadata.get("License", "")),
            "license_files": {
                name: hashlib.sha256(wheel.read(name)).hexdigest() for name in license_files
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", required=True, type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--work-directory", required=True, type=Path)
    parser.add_argument(
        "--curl", type=Path, help="Optional absolute trusted curl for wheel transfer"
    )
    parser.add_argument("--platform", choices=["linux/arm64", "linux/amd64"], default="linux/arm64")
    args = parser.parse_args()
    architecture = args.platform.split("/", 1)[1]
    if not args.docker.is_absolute() or not args.docker.is_file():
        raise ValueError("Provide the absolute trusted Docker CLI path")
    if args.curl is not None and (not args.curl.is_absolute() or not args.curl.is_file()):
        raise ValueError("Provide the absolute trusted curl path")
    if not args.host.startswith("unix:///"):
        raise ValueError("Only an explicit local Unix Docker socket is supported")
    work = args.work_directory.absolute()
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = work / "cli"
    config.mkdir(mode=0o700, exist_ok=True)
    (config / "config.json").write_text('{"auths":{},"proxies":{}}\n')
    manifest = json.loads((RECIPE / f"wheels-linux-{architecture}.json").read_text())
    context = work / "context"
    wheels = context / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    expected_lock = "\n".join(
        f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}" for p in manifest["packages"]
    )
    lock = (RECIPE / f"requirements-linux-{architecture}.lock").read_text()
    if (
        "\n".join(line for line in lock.splitlines() if line and not line.startswith("#"))
        != expected_lock
    ):
        raise ValueError("Requirement and wheel locks disagree")
    shutil.copyfile(
        RECIPE / f"requirements-linux-{architecture}.lock", context / "requirements.lock"
    )
    licenses = []
    for package in manifest["packages"]:
        filename = urllib.parse.urlparse(package["url"]).path.split("/")[-1]
        if not filename.endswith(".whl") or not filename or "/" in filename:
            raise ValueError("Expected a locked wheel file")
        target = wheels / filename
        download(package, target, args.curl)
        licenses.append(inventory(package, target))
        print(f"Verified {package['name']} {package['version']}", flush=True)
    (work / "license-inventory.json").write_text(json.dumps(licenses, indent=2) + "\n")
    # The inspected Engine 27.0.3 provides the legacy builder; the isolated CLI
    # config has no buildx plugin. This trusted offline build is preparation only.
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "DOCKER_BUILDKIT": "0"}
    base = [str(args.docker), "--config", str(config), "--host", args.host]
    server = subprocess.run(
        base + ["version", "--format", "{{json .Server}}"],
        env=env,
        capture_output=True,
        check=True,
        timeout=20,
    )
    if json.loads(server.stdout)["Os"] != "linux":
        raise ValueError("Linux Docker is required")
    image_info = subprocess.run(
        base + ["image", "inspect", manifest["base_image"]],
        env=env,
        capture_output=True,
        check=True,
        timeout=20,
    )
    if json.loads(image_info.stdout)[0]["Architecture"] != architecture:
        raise ValueError(
            "This lock requires a locally installed base image of the selected platform"
        )
    iid = work / "image-id.txt"
    with (work / "build.log").open("wb") as log:
        subprocess.run(
            base
            + [
                "build",
                "--network=none",
                "--pull=false",
                "--platform=" + args.platform,
                "--build-arg",
                "BASE_IMAGE=" + manifest["base_image"],
                "--iidfile",
                str(iid),
                "--file",
                str(RECIPE / "Dockerfile"),
                str(context),
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=600,
        )
    image_id = iid.read_text().strip()
    actual = subprocess.run(
        base + ["image", "inspect", image_id], env=env, capture_output=True, check=True, timeout=20
    )
    image = json.loads(actual.stdout)[0]
    if (
        image["Os"] != "linux"
        or image["Architecture"] != architecture
        or image["Config"].get("Volumes")
    ):
        raise ValueError("Unexpected analysis image configuration")
    result = {
        "scope": "local image preparation only; separate Runtime acceptance required",
        "image_id": image_id,
        "repo_digests": image.get("RepoDigests", []),
        "platform": args.platform,
        "base_image": manifest["base_image"],
        "docker_server": json.loads(server.stdout),
        "requirements_sha256": digest(RECIPE / f"requirements-linux-{architecture}.lock"),
        "dockerfile_sha256": digest(RECIPE / "Dockerfile"),
        "license_inventory_sha256": digest(work / "license-inventory.json"),
    }
    (work / "image.json").write_text(json.dumps(result, indent=2) + "\n")
    print("Built local analysis image " + image_id)


if __name__ == "__main__":
    main()
