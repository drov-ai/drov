"""Real loopback sockets plus blocked ASGI sends; no external model or deployment."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from drov import DrovApp, Limits, Runtime
from drov.models import ScriptedModel, ScriptedStep
from drov.models.protocol import ModelComplete, ModelDelta
from drov.server import ServerLimits
from tests.test_agent_runs import GateModel, final
from tests.test_http import ALICE, Auth, Policy, body, client, definition, service

httpx = pytest.importorskip("httpx")
uvicorn = pytest.importorskip("uvicorn")


async def until(check: Any) -> None:
    async with asyncio.timeout(5):
        while not check():  # noqa: ASYNC110 -- observe real OS/server state with a deadline
            await asyncio.sleep(0.01)


@asynccontextmanager
async def network(app: DrovApp) -> AsyncIterator[Any]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    address = listener.getsockname()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            lifespan="on",
            log_config=None,
            log_level="critical",
            access_log=False,
            proxy_headers=False,
            timeout_graceful_shutdown=1,
        )
    )
    running = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        await until(lambda: server.started or running.done())
        if running.done():
            await running
            raise AssertionError("Server did not start")
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{address[1]}",
            headers={"Host": "127.0.0.1:8000", "Authorization": "Bearer alice"},
            timeout=5,
            trust_env=False,
        ) as connection:
            yield connection
    finally:
        server.should_exit = True
        async with asyncio.timeout(10):
            await running
        listener.close()


@pytest.mark.asyncio
async def test_actual_socket_disconnect_reconnect_and_explicit_cancel() -> None:
    model = GateModel()
    app = service(definition(model))
    async with network(app) as connection:
        ref = await connection.post("/v1/runs", json=body())
        assert ref.status_code == 202, (ref.status_code, ref.text, dict(ref.headers))
        run_id = ref.json()["run_id"]
        await model.entered.wait()
        async with connection.stream("GET", f"/v1/runs/{run_id}/events") as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("id: "):
                    cursor = line[4:]
                    break
        await until(lambda: not app.runtime._runs.subscribers)
        assert app.runtime.active_slots == 1 and not model.release.is_set()
        model.release.set()
        done = await app.runtime.wait(run_id, context=ALICE)
        assert done.status == "completed"
        replay = await connection.get(
            f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": cursor}
        )
        assert replay.status_code == 200 and "event: run.completed" in replay.text
        assert f"id: {cursor}\n" not in replay.text
    assert app.runtime._state == "closed"
    # A separate run verifies explicit HTTP cancellation stops owned work.
    gate = GateModel()
    second = service(definition(gate))
    async with network(second) as connection:
        ref = await connection.post("/v1/runs", json=body())
        await gate.entered.wait()
        cancelled = await connection.post(f"/v1/runs/{ref.json()['run_id']}/cancel", json={})
        assert cancelled.status_code == 202
        assert (
            await second.runtime.wait(ref.json()["run_id"], context=ALICE)
        ).status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_auth", [False, True])
async def test_socket_revocation_stops_future_body_without_durable_error(revoke_auth: bool) -> None:
    gate = GateModel(final("PRIVATE result"))
    auth, authorizer = Auth(), Policy()
    app = DrovApp(agents={"assistant": definition(gate)}, auth=auth, authorizer=authorizer)
    async with network(app) as connection:
        ref = await connection.post("/v1/runs", json=body())
        assert ref.status_code == 202, (ref.status_code, ref.text, dict(ref.headers))
        run_id = ref.json()["run_id"]
        async with connection.stream(
            "GET", f"/v1/runs/{run_id}/events?include_content=true"
        ) as response:
            lines = response.aiter_lines()
            async for line in lines:
                if line.startswith("id: "):
                    break
            if revoke_auth:
                auth.revoked = True
            else:
                authorizer.denied.add("view_content")
            gate.release.set()
            collected = "\n".join([line async for line in lines])
            assert "event: stream.error" in collected and "PRIVATE result" not in collected
            error = collected.split("event: stream.error", 1)[1]
            assert "id: " not in error
        auth.revoked = False
        authorizer.denied.clear()
        assert (await app.runtime.wait(run_id, context=ALICE)).status == "completed"


async def asgi_events(
    app: DrovApp, run_id: str, send: Any, disconnect: asyncio.Event, version: str = "2.0"
) -> None:
    request_sent = False

    async def receive() -> dict[str, Any]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": version},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": f"/v1/runs/{run_id}/events",
            "query_string": b"include_content=true",
            "root_path": "",
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "headers": [(b"host", b"127.0.0.1:8000"), (b"authorization", b"Bearer alice")],
        },
        receive,
        send,
    )


@pytest.mark.asyncio
async def test_blocked_send_has_bounded_transient_queue_and_durable_replay() -> None:
    gate = GateModel()
    app = service(
        definition(gate),
        limits=Limits(subscription_buffer=2),
        server_limits=ServerLimits(send_seconds=1),
    )
    async with client(app) as connection:
        ref = await connection.post("/v1/runs", json=body())
        assert ref.status_code == 202, (ref.status_code, ref.text, dict(ref.headers))
        run_id = ref.json()["run_id"]
        entered, release, disconnect = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                entered.set()
                await release.wait()

        request = asyncio.create_task(asgi_events(app, run_id, send, disconnect))
        await entered.wait()
        sub = next(iter(app.runtime._runs.subscribers[run_id]))
        for _ in range(1000):
            sub.delta("one")
        assert len(sub._deltas) == 2 and sub._dropped == 998
        assert len(sub._durable) <= app.runtime.limits.page_size
        disconnect.set()
        await request
        assert not app.runtime._runs.subscribers
        assert app.runtime.active_slots == 1
        gate.release.set()
        await app.runtime.wait(run_id, context=ALICE)
        replay = await connection.get(f"/v1/runs/{run_id}/events")
        assert "event: run.completed" in replay.text
        ids = [line for line in replay.text.splitlines() if line.startswith("id: ")]
        assert len(ids) == (await app.runtime.storage.get_run(ALICE, run_id)).last_seq


@pytest.mark.asyncio
async def test_send_timeout_closes_subscription_and_retains_execution() -> None:
    gate = GateModel()
    app = service(definition(gate), server_limits=ServerLimits(send_seconds=0.05))
    async with client(app) as connection:
        ref = await connection.post("/v1/runs", json=body())
        assert ref.status_code == 202, (ref.status_code, ref.text, dict(ref.headers))
        run_id = ref.json()["run_id"]

        async def blocked(message: Any) -> None:
            if message["type"] == "http.response.body":
                await asyncio.Event().wait()

        await asgi_events(app, run_id, blocked, asyncio.Event(), version="2.4")
        assert not app.runtime._runs.subscribers and app.runtime.active_slots == 1
        gate.release.set()
        assert (await app.runtime.wait(run_id, context=ALICE)).status == "completed"


@pytest.mark.asyncio
async def test_stream_deltas_have_no_cursor_and_gap_does_not_drop_durable_events() -> None:
    model = ScriptedModel(
        [
            ScriptedStep(
                events=(
                    ModelDelta(text="one"),
                    ModelDelta(text="two"),
                    ModelDelta(text="three"),
                    ModelComplete(response=final()),
                )
            )
        ]
    )
    app = service(definition(model))
    async with network(app) as connection:
        ref = await connection.post("/v1/runs", json=body())
        response = await connection.get(
            f"/v1/runs/{ref.json()['run_id']}/events?include_content=true"
        )
        assert response.status_code == 200
        for frame in response.text.split("\n\n"):
            if "event: token.delta" in frame or "event: stream.gap" in frame:
                assert "id: " not in frame


@pytest.mark.asyncio
async def test_shutdown_closes_stream_borrowed_runtime_remains_open() -> None:
    a = definition(GateModel())
    async with Runtime(agents=[a]) as runtime:
        app = DrovApp(runtime=runtime, auth=Auth())
        context = app._app.router.lifespan_context(app._app)
        await context.__aenter__()
        run_id = (await runtime.start(a, "hello", session_id="s", context=ALICE)).run_id
        event = runtime.events(run_id, context=ALICE)
        await event.__aenter__()
        app._streams.add(event)
        await context.__aexit__(None, None, None)
        assert event._closed and not runtime._runs.subscribers
        runtime._require_open()
        await runtime.cancel(run_id, context=ALICE)
        assert (await runtime.wait(run_id, context=ALICE)).status == "cancelled"
