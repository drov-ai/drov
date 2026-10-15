"""Probe the installed workbench over actual loopback HTTP; no browser or Node required."""

import asyncio
import http.client
import re
import socket
from typing import Any

from drov import Agent, DrovApp
from drov.models import ScriptedModel
from drov.server.workbench import POLICY, assets


def request(port: int, path: str, token: str = "") -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", path, headers={"Authorization": "Bearer " + token})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


async def check() -> None:
    import uvicorn

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    port = listener.getsockname()[1]
    agent: Agent[str] = Agent(name="installed", revision="1", model=ScriptedModel([]))
    app = DrovApp(agents={"installed": agent}, local=True, port=port)
    server = uvicorn.Server(
        uvicorn.Config(app, access_log=False, proxy_headers=False, log_config=None)
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:  # noqa: ASYNC110 -- bounded real listener observation
                if task.done():
                    await task
                    raise AssertionError("Installed server failed")
                await asyncio.sleep(0.01)
        assert app.local_token is not None
        status, _, _ = await asyncio.to_thread(request, port, "/v1/agents")
        assert status == 401
        status, _, catalog = await asyncio.to_thread(request, port, "/v1/agents", app.local_token)
        import json

        assert status == 200 and json.loads(catalog) == {
            "items": [{"name": "installed", "revision": "1"}]
        }
        status, _, html = await asyncio.to_thread(request, port, "/workbench/")
        assert status == 200
        for path, (expected, media) in assets().items():
            status, headers, content = await asyncio.to_thread(request, port, path)
            assert status == 200 and content == expected
            assert headers["content-type"].startswith(media)
            assert headers["content-security-policy"] == POLICY
        assert assets() and all(
            value in assets() for value in re.findall(r'(?:src|href)="([^"]+)"', html.decode())
        )
        report: dict[str, Any] = {"installed_workbench": "passed", "assets": len(assets())}
        print(json.dumps(report))
    finally:
        server.should_exit = True
        async with asyncio.timeout(10):
            await task
        listener.close()


if __name__ == "__main__":
    asyncio.run(check())
