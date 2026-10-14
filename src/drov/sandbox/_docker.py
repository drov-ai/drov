"""Bounded local Docker control and read-only preflight; no model-code entry point."""

import asyncio
import hashlib
import os
import signal
import stat
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any, Self, cast
from urllib.parse import urlsplit

from drov._json import load_json
from drov.errors import ConfigurationError
from drov.sandbox._profile import (
    PROFILE_SHA256,
    DockerLimits,
    DockerProfile,
    checked_image,
    configuration_hash,
    daemon_profile,
    image_reference,
    require,
)

CONTROL_BYTES = 2 * 1024 * 1024
CONTROL_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin"}


class ControlError(ConfigurationError):
    """Deliberately excludes CLI output, host endpoints and image environment values."""


async def _settle(task: asyncio.Task[Any]) -> Any:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass
    return task.result()


async def _reap(process: asyncio.subprocess.Process) -> None:
    # CLI exit is not workload destruction. These groups contain only control
    # processes; this helper must never be used as container cleanup evidence.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


class DockerControl:
    """Only trusted internal argv. Caller is responsible for durable mutation intent."""

    def __init__(self, executable: Path, host: str, directory: Path, seconds: float) -> None:
        self.executable, self.host, self.directory, self.seconds = (
            executable,
            host,
            directory,
            seconds,
        )

    async def command(self, arguments: list[str], *, data: bytes | None = None) -> bytes:
        # Process creation itself is owned even if the caller is cancelled while
        # asyncio is completing the spawn handshake.
        spawning = asyncio.create_task(
            asyncio.create_subprocess_exec(
                str(self.executable),
                "--config",
                str(self.directory),
                "--host",
                self.host,
                *arguments,
                stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=CONTROL_ENV,
                cwd=str(self.directory),
                start_new_session=True,
                limit=65536,
            )
        )
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            process = await _settle(spawning)
            await _settle(asyncio.create_task(_reap(process)))
            raise
        except OSError:
            raise ControlError("Docker control process could not start") from None
        total = 0

        async def read(stream: asyncio.StreamReader | None) -> bytes:
            nonlocal total
            assert stream is not None
            chunks = []
            while chunk := await stream.read(65536):
                total += len(chunk)
                if total > CONTROL_BYTES:
                    raise ControlError("Docker control response exceeds its byte bound")
                chunks.append(chunk)
            return b"".join(chunks)

        async def feed() -> None:
            if process.stdin is not None:
                try:
                    process.stdin.write(data or b"")
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()
                    try:
                        await process.stdin.wait_closed()
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        tasks = [
            asyncio.create_task(read(process.stdout)),
            asyncio.create_task(read(process.stderr)),
            asyncio.create_task(feed()),
            asyncio.create_task(process.wait()),
        ]
        try:
            async with asyncio.timeout(self.seconds):
                result = await asyncio.gather(*tasks)
            if process.returncode != 0:
                raise ControlError("Docker control command failed")
            return cast(bytes, result[0])
        except TimeoutError:
            raise ControlError("Docker control deadline exceeded") from None
        finally:
            # Also stop descendants that inherited a pipe after their CLI parent
            # exited. Do not leave a pipe reader or process outside ownership.
            for task in tasks:
                task.cancel()
            await _settle(asyncio.create_task(_reap(process)))
            await _settle(asyncio.create_task(_join(tasks)))

    async def object(self, arguments: list[str], *, singleton: bool = False) -> dict[str, Any]:
        data = await self.command(arguments)
        try:
            value = load_json(data)
            if singleton:
                require(isinstance(value, list) and len(value) == 1, "Expected one Docker resource")
                value = cast(list[Any], value)[0]
            require(isinstance(value, dict), "Expected a Docker object")
        except (ValueError, TypeError, KeyError, IndexError):
            raise ControlError("Invalid Docker control response") from None
        return cast(dict[str, Any], value)


async def _join(tasks: list[asyncio.Task[Any]]) -> None:
    await asyncio.gather(*tasks, return_exceptions=True)


def _local_paths(executable: Path, host: str) -> Path:
    require(os.name == "posix", "Local Docker controls require POSIX")
    require(executable.is_absolute(), "Explicit absolute trusted Docker CLI required")
    try:
        resolved = executable.resolve(strict=True)
        mode = resolved.stat().st_mode
        require(
            stat.S_ISREG(mode) and os.access(resolved, os.X_OK),
            "Trusted Docker CLI is not executable",
        )
        require(mode & 0o022 == 0, "Trusted Docker CLI is writable by other users")
        parsed = urlsplit(host)
        require(
            parsed.scheme == "unix"
            and not parsed.netloc
            and not parsed.query
            and not parsed.fragment,
            "Only an explicit local Unix Docker socket is supported",
        )
        require(
            host == "unix://" + parsed.path and Path(parsed.path).is_absolute(),
            "Invalid local Docker socket",
        )
        socket = Path(parsed.path)
        require(stat.S_ISSOCK(socket.stat().st_mode), "Local Docker socket is unavailable")
    except OSError:
        raise ConfigurationError("Local Docker CLI or socket is unavailable") from None
    return resolved


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


class DockerPreflight:
    """Concrete private backend resource, pending Runtime dispatch integration.

    Construction is inert. open() uses info/version/image-inspect only and binds
    the actual Engine, immutable image, pinned profile and finite controls.
    No create, pull, build or workload execution occurs here.
    """

    def __init__(
        self, *, image: str, docker: str | Path, host: str, limits: DockerLimits | None = None
    ) -> None:
        self._image = image_reference(image)
        self._executable = Path(docker)
        require(self._executable.is_absolute(), "Explicit absolute trusted Docker CLI required")
        require(
            type(host) is str and host.startswith("unix:///"),
            "Only a local Unix socket is supported",
        )
        self._host = host
        self._limits = (
            DockerLimits() if limits is None else DockerLimits.from_json(limits.model_dump_json())
        )
        self._state = "new"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._control: DockerControl | None = None
        self._profile: DockerProfile | None = None
        self._binding: dict[str, Any] | None = None
        self._opening: asyncio.Task[None] | None = None
        self._closing = False

    def _require_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ConfigurationError("Docker preflight belongs to another event loop")
        self._loop = loop

    @property
    def is_open(self) -> bool:
        return self._state == "open"

    @property
    def profile(self) -> DockerProfile:
        if not self.is_open or self._profile is None:
            raise ConfigurationError("Docker preflight is not open")
        return self._profile

    @property
    def config_hash(self) -> str:
        if not self.is_open or self._binding is None:
            raise ConfigurationError("Docker preflight is not open")
        return configuration_hash(self._binding)

    async def open(self) -> None:
        self._require_loop()
        require(self._state == "new", "Docker preflight is single-use")
        self._state = "opening"
        self._opening = asyncio.create_task(self._open())
        try:
            await asyncio.shield(self._opening)
        except asyncio.CancelledError:
            try:
                await _settle(self._opening)
            finally:
                self._discard()
            raise
        if self._closing:
            raise ConfigurationError("Docker preflight was closed during startup")

    async def _open(self) -> None:
        try:
            executable = await asyncio.to_thread(_local_paths, self._executable, self._host)
            profile = files("drov.sandbox").joinpath("seccomp-v27.0.3.json").read_bytes()
            require(
                hashlib.sha256(profile).hexdigest() == PROFILE_SHA256,
                "Pinned seccomp profile differs",
            )
            self._directory = tempfile.TemporaryDirectory(prefix="drov-docker-")
            directory = Path(self._directory.name)
            (directory / "config.json").write_text('{"auths":{},"proxies":{}}\n', encoding="utf-8")
            (directory / "config.json").chmod(0o600)
            (directory / "seccomp.json").write_bytes(profile)
            (directory / "seccomp.json").chmod(0o600)
            self._control = DockerControl(
                executable, self._host, directory, self._limits.control_seconds
            )
            info = await self._control.object(["system", "info", "--format", "{{json .}}"])
            version = await self._control.object(["version", "--format", "{{json .Server}}"])
            engine = daemon_profile(info, version)
            image = await self._control.object(["image", "inspect", self._image], singleton=True)
            identifier = checked_image(image, self._image, engine["architecture"])
            self._profile = DockerProfile(identifier, engine["architecture"], self._limits)
            self._binding = {
                "binding_version": 1,
                "controller_version": 1,
                "host": self._host,
                "cli": str(executable),
                "cli_sha256": await asyncio.to_thread(_hash_file, executable),
                "engine": engine,
                "image_reference": self._image,
                "image_id": identifier,
                "limits": self._limits.model_dump(mode="json"),
                "fixed_controls_version": 1,
            }
            self._state = "open"
        except BaseException:
            self._discard()
            raise

    async def aclose(self) -> None:
        self._require_loop()
        self._closing = True
        if self._opening is not None and not self._opening.done():
            try:
                await _settle(self._opening)
            finally:
                self._discard()
        else:
            self._discard()

    def _discard(self) -> None:
        self._state = "closed"
        self._control = None
        self._profile = None
        self._binding = None
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()
