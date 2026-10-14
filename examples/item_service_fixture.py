"""Owned loopback-only synthetic service; independent writes, counts and result evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any


class LocalHTTPServer(HTTPServer):
    def server_bind(self) -> None:
        # Literal loopback fixtures do not need reverse DNS during startup.
        TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


class ItemService:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.port = 0
        self.process: asyncio.subprocess.Process | None = None
        self.script = Path(__file__).resolve()
        self.mode = "normal"
        self.delay = 0.0

    async def open(self) -> None:
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-B",
            "-X",
            "utf8",
            str(self.script),
            "--state",
            str(self.directory / "items.json"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self.process.stdout is not None
        async with asyncio.timeout(5):
            line = await self.process.stdout.readline()
        self.port = json.loads(line)["port"]

    async def request(self, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode()
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            writer.write(
                (
                    f"POST /{route} HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                    f"Content-Length: {len(data)}\r\n\r\n"
                ).encode()
                + data
            )
            await writer.drain()
            async with asyncio.timeout(5):
                header = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                if not 0 <= length <= 1048576:
                    raise ValueError("Fixture response exceeds the limit")
                body = await reader.readexactly(length)
            result: dict[str, Any] = json.loads(body)
            if "error" in result:
                raise ConnectionError("Synthetic service returned a known test error")
            return result
        finally:
            writer.close()
            await writer.wait_closed()

    async def apply(
        self, operation_id: str, tenant: str, user: str, item: str, value: str
    ) -> dict[str, str]:
        result = await self.request(
            "apply",
            {
                "operation_id": operation_id,
                "tenant": tenant,
                "user": user,
                "item": item,
                "value": value,
                "mode": self.mode,
                "delay": self.delay,
            },
        )
        return result

    async def query(self, operation_id: str, tenant: str, user: str) -> dict[str, Any]:
        return await self.request(
            "query", {"operation_id": operation_id, "tenant": tenant, "user": user}
        )

    async def stats(self) -> dict[str, Any]:
        return await self.request("stats", {})

    async def aclose(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.returncode is None and self.port:
                await self.request("stop", {})
                async with asyncio.timeout(5):
                    await self.process.wait()
        finally:
            if self.process.returncode is None:
                self.process.terminate()
                await self.process.wait()
            self.process = None


def serve(path: Path) -> None:
    state: dict[str, Any] = {"writes": 0, "attempts": 0, "operations": {}}
    stopped = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            nonlocal stopped
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 65536:
                self.send_error(413)
                return
            payload = json.loads(self.rfile.read(length))
            response: dict[str, Any]
            if self.path == "/apply":
                state["attempts"] += 1
                identity = json.dumps([payload["tenant"], payload["user"], payload["operation_id"]])
                if payload["mode"] == "not_applied":
                    response = {"error": "rejected without executing"}
                    state["operations"][identity] = {
                        "resolution": "not_applied",
                        "evidence": "Serial service completed the request without writing",
                    }
                else:
                    time.sleep(min(1, max(0, payload.get("delay", 0))))
                    state["writes"] += (
                        1  # Deliberately non-idempotent; every duplicate write is visible.
                    )
                    response = {"item_id": payload["item"], "status": payload["value"]}
                    state["operations"][identity] = {
                        "resolution": "applied",
                        "evidence": "Independent service write ledger confirms this operation",
                        "result": response,
                    }
                path.write_text(json.dumps(state), encoding="utf-8")
                if payload["mode"] == "barrier_reply":
                    # Test-only barrier: the independent write is durable before the SDK reply.
                    print(
                        json.dumps({"boundary": "write_before_reply", "writes": state["writes"]}),
                        flush=True,
                    )
                    sys.stdin.readline()
                if payload["mode"] == "reply_lost":
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if payload["mode"] == "invalid_result":
                    response = {"item_id": 7, "status": False}
            elif self.path == "/query":
                identity = json.dumps([payload["tenant"], payload["user"], payload["operation_id"]])
                response = state["operations"].get(
                    identity,
                    {
                        "resolution": "not_applied",
                        "evidence": "Serial ledger confirms no operation or active earlier request",
                    },
                )
            elif self.path == "/stats":
                response = state
            elif self.path == "/stop":
                stopped, response = True, {"stopped": True}
            else:
                self.send_error(404)
                return
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    with LocalHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        print(json.dumps({"port": server.server_port}), flush=True)
        while not stopped:
            server.handle_request()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    serve(parser.parse_args().state)
