"""Finite local Engine exec control; never retries an execution start request."""

import asyncio
import re
import struct
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from drov._json import canonical_json, load_json
from drov.errors import ConfigurationError
from drov.sandbox._docker import CONTROL_BYTES, _settle


class EngineError(ConfigurationError):
    """Transport/protocol failures are ambiguous after a mutating request was sent."""


@dataclass(frozen=True)
class ExecOutput:
    stdout: bytes
    stderr: bytes
    termination: Literal["exited", "timeout", "output_limit"]
    stdout_truncated: bool = False
    stderr_truncated: bool = False


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise EngineError("Invalid internal Docker execution identity")
    return value


class UnixEngine:
    """Trusted local daemon, fixed API subset, bounded bodies and multiplex frames."""

    def __init__(self, host: str, *, control_seconds: float) -> None:
        self.path = urlsplit(host).path
        self.control_seconds = control_seconds

    async def _connect(
        self, method: str, path: str, payload: dict[str, Any] | None, *, attach: bool = False
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, int, dict[str, str]]:
        reader, writer = await asyncio.open_unix_connection(self.path, limit=65536)
        try:
            body = canonical_json(payload).encode() if payload is not None else b""
            header = (
                f"{method} /v1.41{path} HTTP/1.1\r\nHost: docker\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                + ("Connection: Upgrade\r\nUpgrade: tcp\r\n" if attach else "Connection: close\r\n")
                + "\r\n"
            ).encode("ascii")
            writer.write(header + body)
            await writer.drain()
            raw = await reader.readuntil(b"\r\n\r\n")
            lines = raw.decode("ascii").split("\r\n")
            first = lines[0].split(" ", 2)
            if len(first) < 2 or first[0] != "HTTP/1.1" or not re.fullmatch(r"[0-9]{3}", first[1]):
                raise EngineError("Invalid Engine response status")
            headers: dict[str, str] = {}
            for line in lines[1:]:
                if not line:
                    continue
                key, separator, value = line.partition(":")
                key = key.lower()
                if not separator or key in headers or not re.fullmatch(r"[a-z0-9-]+", key):
                    raise EngineError("Invalid Engine response headers")
                headers[key] = value.strip()
            return reader, writer, int(first[1]), headers
        except BaseException:
            writer.close()
            await _settle(asyncio.create_task(_close(writer)))
            raise

    async def _body(self, reader: asyncio.StreamReader, headers: dict[str, str]) -> bytes:
        chunks: list[bytes] = []
        total = 0
        transfer = headers.get("transfer-encoding")
        length = headers.get("content-length")
        if transfer:
            if transfer != "chunked" or length is not None:
                raise EngineError("Unsupported Engine response framing")
            while True:
                line = await reader.readuntil(b"\r\n")
                if len(line) > 32 or not re.fullmatch(rb"[0-9a-fA-F]+\r\n", line):
                    raise EngineError("Invalid Engine chunk framing")
                size = int(line[:-2], 16)
                total += size
                if total > CONTROL_BYTES:
                    raise EngineError("Engine response exceeds its byte bound")
                if size == 0:
                    if await reader.readexactly(2) != b"\r\n":
                        raise EngineError("Unexpected Engine trailers")
                    break
                chunks.append(await reader.readexactly(size))
                if await reader.readexactly(2) != b"\r\n":
                    raise EngineError("Invalid Engine chunk terminator")
        elif length is not None:
            if not re.fullmatch(r"[0-9]+", length) or len(length) > 10:
                raise EngineError("Invalid Engine response length")
            size = int(length)
            if size > CONTROL_BYTES:
                raise EngineError("Engine response exceeds its byte bound")
            chunks.append(await reader.readexactly(size))
        else:
            while chunk := await reader.read(65536):
                total += len(chunk)
                if total > CONTROL_BYTES:
                    raise EngineError("Engine response exceeds its byte bound")
                chunks.append(chunk)
        return b"".join(chunks)

    async def _json(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(self.control_seconds):
                reader, writer, status, headers = await self._connect(method, path, payload)
                if status not in (200, 201):
                    raise EngineError("Engine exec control request failed")
                value = load_json(await self._body(reader, headers))
                if not isinstance(value, dict):
                    raise EngineError("Expected an Engine execution object")
                return cast(dict[str, Any], value)
        except (
            OSError,
            TimeoutError,
            ValueError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            raise EngineError("Engine exec control response unavailable") from None
        finally:
            if writer is not None:
                writer.close()
                await _settle(asyncio.create_task(_close(writer)))

    async def create_exec(
        self, container_id: str, code: str, *, argument: str | None = None, control: bool = False
    ) -> str:
        result = await self._json(
            "POST",
            "/containers/" + _identifier(container_id) + "/exec",
            {
                "AttachStdout": True,
                "AttachStderr": True,
                "AttachStdin": False,
                "Tty": False,
                "Privileged": False,
                "User": "65534:65534" if control else "65533:65533",
                "WorkingDir": "/work",
                "Env": [
                    "HOME=/work",
                    "MPLCONFIGDIR=/work/mpl",
                    "OPENBLAS_NUM_THREADS=1",
                    "OMP_NUM_THREADS=1",
                ],
                "Cmd": ["/usr/local/bin/python3", "-I", "-B", "-c", code]
                + ([argument] if argument is not None else []),
            },
        )
        identifier = result.get("Id")
        if not isinstance(identifier, str):
            raise EngineError("Engine exec create acknowledgement is missing")
        return _identifier(identifier)

    async def inspect_exec(self, execution_id: str) -> dict[str, Any]:
        return await self._json("GET", "/exec/" + _identifier(execution_id) + "/json")

    async def start_exec(self, execution_id: str, *, seconds: float, max_bytes: int) -> ExecOutput:
        """Closing this stream does NOT stop code. Owner must query/stop/destroy."""
        writer: asyncio.StreamWriter | None = None
        stdout, stderr = bytearray(), bytearray()
        channel = 1
        try:
            async with asyncio.timeout(seconds):
                reader, writer, status, headers = await self._connect(
                    "POST",
                    "/exec/" + _identifier(execution_id) + "/start",
                    {"Detach": False, "Tty": False},
                    attach=True,
                )
                if status != 101 or headers.get("upgrade", "").lower() != "tcp":
                    raise EngineError("Engine exec attach acknowledgement is unavailable")
                while True:
                    try:
                        frame = await reader.readexactly(8)
                    except asyncio.IncompleteReadError as exc:
                        if exc.partial:
                            raise EngineError("Incomplete Engine output frame") from None
                        break
                    channel, zero1, zero2, zero3, size = struct.unpack(">BBBBI", frame)
                    if channel not in (1, 2) or (zero1, zero2, zero3) != (0, 0, 0):
                        raise EngineError("Invalid Engine output stream")
                    remaining = max_bytes - len(stdout) - len(stderr)
                    destination = stdout if channel == 1 else stderr
                    # Never allocate the daemon's untrusted full frame size.
                    count = min(size, remaining)
                    while count:
                        chunk = await reader.read(min(count, 65536))
                        if not chunk:
                            raise EngineError("Incomplete Engine output frame")
                        destination.extend(chunk)
                        count -= len(chunk)
                    if size > remaining:
                        return ExecOutput(
                            bytes(stdout), bytes(stderr), "output_limit", channel == 1, channel == 2
                        )
                return ExecOutput(bytes(stdout), bytes(stderr), "exited")
        except TimeoutError:
            return ExecOutput(bytes(stdout), bytes(stderr), "timeout")
        except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            raise EngineError("Engine exec output unavailable") from None
        finally:
            if writer is not None:
                writer.close()
                await _settle(asyncio.create_task(_close(writer)))


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass
