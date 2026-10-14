"""Finite Unix Engine protocol; start errors never trigger a second request."""

import asyncio
import json
import os
import struct
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from drov.sandbox._engine import EngineError, UnixEngine

EXEC_ID = "a" * 64
CONTAINER_ID = "b" * 64


class Fixture:
    def __init__(self, host: str) -> None:
        self.host = host
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.payload = b""
        self.response = (
            b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n"
        )
        self.sleep = False
        self.closed = asyncio.Event()
        self.received = asyncio.Event()
        self.clients: set[asyncio.Task[Any]] = set()

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.clients.add(task)
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.startswith(b"Content-Length:")
            )
            body = await reader.readexactly(length)
            self.requests.append(
                (headers.split(b"\r\n")[0].decode(), json.loads(body) if body else {})
            )
            self.received.set()
            writer.write(self.response + self.payload)
            await writer.drain()
            if self.sleep:
                await reader.read()
            writer.close()
            await writer.wait_closed()
        finally:
            writer.close()
            self.closed.set()
            self.clients.discard(task)


@pytest_asyncio.fixture
async def engine_fixture() -> AsyncIterator[Fixture]:
    if os.name != "posix":
        pytest.skip("Unix socket Engine profile")
    with tempfile.TemporaryDirectory(prefix="drov-api-") as directory:
        path = Path(directory) / "s"
        fixture = Fixture("unix://" + str(path))
        async with await asyncio.start_unix_server(fixture.serve, path=str(path)) as server:
            yield fixture
            server.close()
            await server.wait_closed()
            for task in list(fixture.clients):
                task.cancel()
            await asyncio.gather(*fixture.clients, return_exceptions=True)


def frame(channel: int, data: bytes) -> bytes:
    return struct.pack(">BBBBI", channel, 0, 0, 0, len(data)) + data


@pytest.mark.asyncio
async def test_exec_create_pins_identity_and_does_not_start_code(engine_fixture: Fixture) -> None:
    fixture = engine_fixture
    payload = json.dumps({"Id": EXEC_ID}).encode()
    fixture.response = (
        b"HTTP/1.1 201 Created\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n"
    )
    fixture.payload = payload
    engine = UnixEngine(fixture.host, control_seconds=1.0)
    assert await engine.create_exec(CONTAINER_ID, "print('original')") == EXEC_ID
    assert len(fixture.requests) == 1
    path, body = fixture.requests[0]
    assert path == "POST /v1.41/containers/" + CONTAINER_ID + "/exec HTTP/1.1"
    assert body["User"] == "65533:65533" and body["Privileged"] is False and body["Tty"] is False
    assert body["Cmd"] == ["/usr/local/bin/python3", "-I", "-B", "-c", "print('original')"]


@pytest.mark.asyncio
async def test_raw_stdout_stderr_and_invalid_utf8_are_preserved(engine_fixture: Fixture) -> None:
    fixture = engine_fixture
    fixture.payload = frame(1, b"\xe4\xb8\xad\xff") + frame(2, b"actual stderr")
    result = await UnixEngine(fixture.host, control_seconds=1.0).start_exec(
        EXEC_ID, seconds=1.0, max_bytes=64
    )
    assert result.stdout == b"\xe4\xb8\xad\xff" and result.stderr == b"actual stderr"
    assert result.termination == "exited" and not result.stdout_truncated
    assert len(fixture.requests) == 1


@pytest.mark.asyncio
async def test_combined_limit_reads_only_the_bounded_prefix(engine_fixture: Fixture) -> None:
    fixture = engine_fixture
    fixture.payload = frame(1, b"first") + frame(2, b"second")
    result = await UnixEngine(fixture.host, control_seconds=1.0).start_exec(
        EXEC_ID, seconds=1.0, max_bytes=8
    )
    assert result.stdout == b"first" and result.stderr == b"sec"
    assert result.termination == "output_limit" and result.stderr_truncated
    assert not result.stdout_truncated and len(fixture.requests) == 1


@pytest.mark.asyncio
async def test_advertised_huge_frame_does_not_require_full_allocation(
    engine_fixture: Fixture,
) -> None:
    fixture = engine_fixture
    fixture.payload = struct.pack(">BBBBI", 1, 0, 0, 0, 2**32 - 1) + b"bounded"
    fixture.sleep = True
    result = await UnixEngine(fixture.host, control_seconds=1.0).start_exec(
        EXEC_ID, seconds=1.0, max_bytes=7
    )
    assert result.stdout == b"bounded" and result.termination == "output_limit"
    await asyncio.wait_for(fixture.closed.wait(), 1.0)


@pytest.mark.asyncio
async def test_timeout_retains_received_frame_prefix_and_closes_stream(
    engine_fixture: Fixture,
) -> None:
    fixture = engine_fixture
    fixture.payload = struct.pack(">BBBBI", 1, 0, 0, 0, 20) + b"partial"
    fixture.sleep = True
    result = await UnixEngine(fixture.host, control_seconds=1.0).start_exec(
        EXEC_ID, seconds=0.1, max_bytes=64
    )
    assert result.stdout == b"partial" and result.termination == "timeout"
    await asyncio.wait_for(fixture.closed.wait(), 1.0)
    assert len(fixture.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response,payload",
    [
        (b"HTTP/1.1 500 Error\r\nContent-Length: 0\r\n\r\n", b""),
        (b"HTTP/1.1 101 OK\r\nUpgrade: tcp\r\n\r\n", b"x"),
        (b"HTTP/1.1 101 OK\r\nUpgrade: tcp\r\n\r\n", frame(3, b"daemon-error")),
        (b"HTTP/1.1 101 OK\r\nUpgrade: tcp\r\nUpgrade: tcp\r\n\r\n", b""),
    ],
)
async def test_protocol_failure_never_retries_start(
    engine_fixture: Fixture, response: bytes, payload: bytes
) -> None:
    fixture = engine_fixture
    fixture.response, fixture.payload = response, payload
    with pytest.raises(EngineError):
        await UnixEngine(fixture.host, control_seconds=1.0).start_exec(
            EXEC_ID, seconds=1.0, max_bytes=64
        )
    assert len(fixture.requests) == 1


@pytest.mark.asyncio
async def test_cancellation_closes_socket_without_another_start(engine_fixture: Fixture) -> None:
    fixture = engine_fixture
    fixture.sleep = True
    task = asyncio.create_task(
        UnixEngine(fixture.host, control_seconds=1.0).start_exec(EXEC_ID, seconds=5.0, max_bytes=64)
    )
    await asyncio.wait_for(fixture.received.wait(), 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(fixture.closed.wait(), 1.0)
    assert len(fixture.requests) == 1
