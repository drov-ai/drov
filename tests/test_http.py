"""HTTP identity, shared Runtime controls and real ASGI transport boundaries."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from drov import (
    Agent,
    DrovApp,
    HumanInput,
    RunContext,
    Runtime,
    SQLiteStorage,
    tool,
)
from drov.artifacts import LocalArtifacts
from drov.auth import Action, OwnerAuthorizer, Resource
from drov.errors import ConfigurationError, PersistenceUnavailable
from drov.models import ScriptedModel
from drov.models.protocol import ModelResponse, ToolCall
from drov.server import ServerLimits
from drov.types import Completed
from tests.test_agent_runs import GateModel, final

pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

ALICE = RunContext(tenant_id="test", user_id="alice")
BOB = RunContext(tenant_id="test", user_id="bob")
OTHER = RunContext(tenant_id="other", user_id="alice")


class Auth:
    def __init__(self) -> None:
        self.revoked = False

    async def authenticate(self, credential: str) -> RunContext:
        if self.revoked or credential not in ("alice", "bob", "other"):
            raise ValueError("PRIVATE auth detail")
        return {"alice": ALICE, "bob": BOB, "other": OTHER}[credential]


class Policy(OwnerAuthorizer):
    def __init__(self) -> None:
        self.denied: set[str] = set()

    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
        return action not in self.denied and await super().authorize(actor, action, resource)


def definition(model: ScriptedModel | None = None, **kwargs: Any) -> Agent[Any]:
    return Agent(
        name="assistant", revision="1", model=model or ScriptedModel([final()] * 100), **kwargs
    )


def body(session: str = "s", **kwargs: Any) -> dict[str, Any]:
    return dict(agent_name="assistant", revision="1", session_id=session, input="hello", **kwargs)


@asynccontextmanager
async def client(service: DrovApp) -> AsyncIterator[Any]:
    async with service._app.router.lifespan_context(service._app):
        headers = {
            "Authorization": "Bearer " + ((service.local_token or "") if service.local else "alice")
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=service),
            base_url="http://127.0.0.1:8000",
            headers=headers,
        ) as connection:
            yield connection


def service(a: Agent[Any] | None = None, **kwargs: Any) -> DrovApp:
    selected = a or definition()
    return DrovApp(agents={"assistant": selected}, auth=Auth(), **kwargs)


@pytest.mark.asyncio
async def test_start_inspect_events_context_sessions_openapi() -> None:
    app = service()
    async with client(app) as connection:
        accepted = await connection.post("/v1/runs", json=body(request_key="once"))
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()["run_id"]
        done = await app.runtime.wait(run_id, context=ALICE)
        assert isinstance(done, Completed)
        duplicate = await connection.post("/v1/runs", json=body(request_key="once"))
        assert duplicate.json() == accepted.json()
        hidden = await connection.get(f"/v1/runs/{run_id}")
        assert hidden.status_code == 200 and not hidden.json()["snapshot"]["content_visible"]
        content = await connection.get(f"/v1/runs/{run_id}?include_content=true&limit=2")
        assert content.json()["snapshot"]["outcome"]["output"] == "answer"
        assert content.json()["has_more"]
        after = content.json()["next_cursor"]
        next_page = await connection.get(f"/v1/runs/{run_id}", params={"after": after})
        assert next_page.json()["events"][0]["seq"] > 2
        stream = await connection.get(f"/v1/runs/{run_id}/events?include_content=true")
        assert stream.status_code == 200 and "event: run.completed" in stream.text
        ids = [line[4:] for line in stream.text.splitlines() if line.startswith("id: ")]
        assert ids == [f"{run_id}:{i}" for i in range(1, len(ids) + 1)]
        reconnect = await connection.get(
            f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": ids[-1]}
        )
        assert reconnect.status_code == 200 and reconnect.text == ""
        model = app.runtime._agents[("assistant", "1")].model
        assert isinstance(model, ScriptedModel)
        call = model.calls[0]
        manifest = await connection.get(f"/v1/runs/{run_id}/contexts/{call.model_call_id}")
        assert manifest.status_code == 200 and not manifest.json()["content_visible"]
        sessions = await connection.get("/v1/sessions")
        assert sessions.json()["items"][0]["session_id"] == "s"
        view = await connection.get("/v1/sessions/s")
        assert view.json()["runs"][0]["run_id"] == run_id
        schema = await connection.get("/openapi.json")
        assert schema.status_code == 200 and "/v1/artifacts" in schema.json()["paths"]
        assert "user_id" not in schema.json()["components"]["schemas"]["StartBody"]["properties"]
    assert (
        not app.runtime.storage.is_open
        if app.runtime._state == "open"
        else app.runtime._state == "closed"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,status",
    [
        ({"Authorization": ""}, 401),
        ({"Authorization": "Basic alice"}, 401),
        ({"Authorization": "Bearer unknown"}, 401),
        ({"Host": "evil.test"}, 400),
        ({"Origin": "https://evil.test"}, 403),
        ({"Origin": "null"}, 403),
    ],
)
async def test_identity_host_origin(headers: dict[str, str], status: int) -> None:
    app = service()
    async with client(app) as connection:
        response = await connection.post("/v1/runs", json=body(), headers=headers)
        assert response.status_code == status
        assert set(response.json()["error"]) == {"code", "message", "request_id"}
        assert "PRIVATE" not in response.text
        model = app.runtime._agents[("assistant", "1")].model
        assert isinstance(model, ScriptedModel)
        assert not model.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", "bob"),
        ("tenant_id", "other"),
        ("image", "secret"),
        ("dependencies", {}),
        ("model", {}),
        ("tools", []),
        ("mounts", []),
        ("api_key", "synthetic-secret"),
    ],
)
async def test_body_cannot_override_host_configuration(field: str, value: Any) -> None:
    app = service()
    async with client(app) as connection:
        response = await connection.post("/v1/runs", json={**body(), field: value})
        assert response.status_code == 422 and str(value) not in response.text
        assert app.runtime.active_slots == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["bob", "other"])
async def test_guessed_run_context_event_session_ids_are_hidden(credential: str) -> None:
    app = service()
    async with client(app) as connection:
        ref = await connection.post("/v1/runs", json=body())
        run_id = ref.json()["run_id"]
        await app.runtime.wait(run_id, context=ALICE)
        for suffix in ("", "/events", "/contexts/unknown"):
            denied = await connection.get(
                f"/v1/runs/{run_id}{suffix}", headers={"Authorization": f"Bearer {credential}"}
            )
            assert denied.status_code == 404
        assert (
            await connection.get(
                "/v1/sessions/s", headers={"Authorization": f"Bearer {credential}"}
            )
        ).status_code == 404
        page = await connection.get(
            "/v1/sessions", headers={"Authorization": f"Bearer {credential}"}
        )
        assert page.json()["items"] == []
        # Arbitrary headers never replace authenticated identity.
        own = await connection.get(
            f"/v1/runs/{run_id}", headers={"X-User-Id": "bob", "X-Tenant-Id": "other"}
        )
        assert own.status_code == 200


@pytest.mark.asyncio
async def test_cursor_validation_conflict_failed_snapshot_and_cancel() -> None:
    app = service()
    async with client(app) as connection:
        ref = await connection.post("/v1/runs", json=body(request_key="key"))
        run_id = ref.json()["run_id"]
        await app.runtime.wait(run_id, context=ALICE)
        conflict = await connection.post(
            "/v1/runs", json={**body(request_key="key"), "input": "changed"}
        )
        assert conflict.status_code == 409
        for cursor in ("bad", "other:1", f"{run_id}:99999"):
            response = await connection.get(f"/v1/runs/{run_id}/events", params={"after": cursor})
            assert response.status_code == 400
        response = await connection.get(
            f"/v1/runs/{run_id}/events",
            params={"after": f"{run_id}:1"},
            headers={"Last-Event-ID": f"{run_id}:2"},
        )
        assert response.status_code == 400
        cancel = await connection.post(f"/v1/runs/{run_id}/cancel", json={})
        assert cancel.status_code == 202 and cancel.json()["status"] == "completed"
        recover = await connection.post(f"/v1/runs/{run_id}/recover", json={})
        assert recover.status_code == 409
        # Execution failure is a successful retrieval of a failed snapshot.
        broken = await connection.post("/v1/runs", json={**body("broken"), "input": "x" * 100000})
        assert broken.status_code == 422
        with patch.object(
            app.runtime._runs, "inspect", side_effect=PersistenceUnavailable("PRIVATE")
        ):
            unavailable = await connection.get(f"/v1/runs/{run_id}")
            assert unavailable.status_code == 503 and "PRIVATE" not in unavailable.text


@pytest.mark.asyncio
async def test_local_token_and_lifecycle_borrowed_runtime() -> None:
    a = definition()
    app = DrovApp(agents={"assistant": a}, local=True)
    assert app.local_token is None
    async with client(app) as connection:
        assert app.local_token and len(app.local_token) >= 40
        response = await connection.get("/v1/sessions")
        assert response.status_code == 200
        assert app.local_token not in response.text
        assert (await connection.get("/v1/sessions?token=synthetic")).status_code == 400
    assert app.local_token is None and app.runtime._state == "closed"
    async with Runtime(agents=[a]) as runtime:
        borrowed = DrovApp(runtime=runtime, auth=Auth())
        async with client(borrowed):
            pass
        runtime._require_open()
        assert await runtime.sessions(context=ALICE) is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"local": True, "host": "0.0.0.0"},
        {"local": True, "auth": Auth()},
        {"auth": Auth(), "host": "0.0.0.0"},
        {"local": True, "allowed_hosts": ["*"]},
        {"local": True, "allowed_origins": ["https://evil.test"]},
    ],
)
def test_fail_closed_server_configuration(kwargs: dict[str, Any]) -> None:
    a = definition()
    with pytest.raises(ConfigurationError):
        DrovApp(agents={"assistant": a}, **kwargs)


@pytest.mark.asyncio
async def test_json_and_transport_limits() -> None:
    app = service(server_limits=ServerLimits(max_json_bytes=160))
    async with client(app) as connection:
        for data, status in [
            (b"{" + b"x" * 160, 413),
            (b'{"input":1,"input":2}', 422),
            (b'{"x":NaN}', 422),
            (b"{", 422),
        ]:
            response = await connection.post(
                "/v1/runs", content=data, headers={"Content-Type": "application/json"}
            )
            assert response.status_code == status
        response = await connection.post("/v1/runs", content=b"raw")
        assert response.status_code == 415
        response = await connection.get("/v1/sessions?access_token=synthetic")
        assert response.status_code == 400
        response = await connection.post("/unknown", json={})
        assert response.status_code == 404 and "error" in response.json()
        app._accepting = False
        assert (await connection.get("/v1/sessions")).status_code == 503


@pytest.mark.asyncio
async def test_actual_approval_input_receipts_and_dynamic_revocation() -> None:
    count = []

    @tool(name="write", effect="write")
    async def write(value: int) -> str:
        count.append(value)
        return "written"

    model = ScriptedModel(
        [
            ModelResponse(
                finish_reason="tool_calls",
                tool_calls=[ToolCall(call_id="c", name="ask", arguments={"prompt": "choose"})],
            ),
            ModelResponse(
                finish_reason="tool_calls",
                tool_calls=[ToolCall(call_id="w", name="write", arguments={"value": 7})],
            ),
            final(),
        ]
    )
    policy = Policy()
    app = service(
        definition(model, tools=[HumanInput(name="ask", input_type=str), write]), authorizer=policy
    )
    async with client(app) as connection:
        response = await connection.post("/v1/runs", json=body())
        run_id = response.json()["run_id"]
        paused = await app.runtime.wait(run_id, context=ALICE)
        assert paused.status == "suspended" and paused.reason == "input"
        assert paused.pending is not None
        req = {
            "request_id": paused.pending.request_id,
            "response": {"kind": "input", "value": "yes"},
        }
        invalid = await connection.post(
            f"/v1/runs/{run_id}/responses", json={**req, "response": {"kind": "input", "value": 1}}
        )
        assert invalid.status_code == 422
        first = await connection.post(f"/v1/runs/{run_id}/responses", json=req)
        assert first.status_code == 202
        repeat = await connection.post(f"/v1/runs/{run_id}/responses", json=req)
        assert repeat.json() == first.json()
        approval = await app.runtime.wait(run_id, context=ALICE)
        assert approval.status == "suspended" and approval.reason == "approval" and count == []
        assert approval.pending is not None
        policy.denied.add("respond")
        req = {
            "request_id": approval.pending.request_id,
            "response": {"kind": "approval", "approved": True},
        }
        denied = await connection.post(f"/v1/runs/{run_id}/responses", json=req)
        assert denied.status_code == 403 and count == []
        policy.denied.remove("respond")
        policy.denied.add("view_content")
        assert (await connection.get(f"/v1/runs/{run_id}?include_content=true")).status_code == 403
        assert (await connection.get(f"/v1/runs/{run_id}")).status_code == 200
        # Respond-only authorization does not require reading the body.
        approved = await connection.post(f"/v1/runs/{run_id}/responses", json=req)
        assert approved.status_code == 202
        policy.denied.clear()
        assert (await app.runtime.wait(run_id, context=ALICE)).status == "completed"
        assert count == [7]


@pytest.mark.asyncio
async def test_sdk_session_pages_signed_and_bound_to_query_and_identity() -> None:
    a = definition()
    async with Runtime(agents=[a]) as runtime:
        for session in ("a", "b", "c"):
            await runtime.run(a, "hello", session_id=session, context=ALICE)
        page = await runtime.sessions(context=ALICE, limit=1)
        assert page.has_more and len(page.items) == 1 and page.next_cursor
        second = await runtime.sessions(context=ALICE, limit=1, after=page.next_cursor)
        assert second.items[0].session_id != page.items[0].session_id
        from drov.errors import InvalidCursor

        for value, identity in ((page.next_cursor + "x", ALICE), (page.next_cursor, BOB)):
            with pytest.raises(InvalidCursor):
                await runtime.sessions(context=identity, after=value)
        with pytest.raises(InvalidCursor):
            await runtime.session("a", context=ALICE, after=page.next_cursor)


@pytest.mark.asyncio
async def test_start_disconnection_does_not_cancel_owned_execution() -> None:
    model = GateModel()
    app = service(definition(model))
    async with client(app) as connection:
        response = await connection.post("/v1/runs", json=body())
        run_id = response.json()["run_id"]
        await model.entered.wait()
        assert app.runtime.active_slots == 1
        waiter = asyncio.create_task(app.runtime.wait(run_id, context=ALICE))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        model.release.set()
        assert (await app.runtime.wait(run_id, context=ALICE)).status == "completed"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX artifact byte store")
async def test_upload_atomic_consumption_scope_dedup_and_authorized_download(
    tmp_path: Path,
) -> None:
    policy = Policy()
    app = service(
        storage=SQLiteStorage(tmp_path / "state.db"),
        artifacts=LocalArtifacts(directory=tmp_path / "files"),
        authorizer=policy,
    )
    async with client(app) as connection:
        target = dict(
            agent_name="assistant",
            revision="1",
            session_id="s",
            name="numbers.csv",
            media_type="text/csv",
        )
        uploaded = await connection.post(
            "/v1/artifacts",
            params=target,
            content=b"value\n7\n",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert uploaded.status_code == 201, uploaded.text
        upload_id = uploaded.json()["upload_id"]
        assert (await connection.get(f"/v1/artifacts/{upload_id}")).status_code == 404
        for change, token in (({"session_id": "other"}, "alice"), ({}, "bob"), ({}, "other")):
            denied = await connection.post(
                "/v1/runs",
                json={**body(upload_ids=[upload_id]), **change},
                headers={"Authorization": "Bearer " + token},
            )
            assert denied.status_code == 404
        accepted = await connection.post(
            "/v1/runs", json=body(upload_ids=[upload_id], request_key="once")
        )
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()["run_id"]
        await app.runtime.wait(run_id, context=ALICE)
        same = await connection.post(
            "/v1/runs", json=body(upload_ids=[upload_id], request_key="once")
        )
        assert same.json() == accepted.json()
        conflict = await connection.post("/v1/runs", json=body(upload_ids=[upload_id]))
        assert conflict.status_code == 409
        record = await app.runtime.storage.get_run(ALICE, run_id)
        ref = record.artifact_inputs[0]
        hidden = await connection.get(f"/v1/artifacts/{ref.artifact_id}")
        assert hidden.status_code == 200 and hidden.json()["name"] is None
        download = await connection.get(f"/v1/artifacts/{ref.artifact_id}/content")
        assert (
            download.content == b"value\n7\n"
            and "attachment" in download.headers["content-disposition"]
        )
        assert download.headers["x-content-type-options"] == "nosniff"
        assert (
            await connection.get(
                f"/v1/artifacts/{ref.artifact_id}/content", headers={"Authorization": "Bearer bob"}
            )
        ).status_code == 404
        policy.denied.add("view_content")
        assert (await connection.get(f"/v1/artifacts/{ref.artifact_id}/content")).status_code == 403
        policy.denied.clear()
        assert (
            await connection.post("/v1/runs", json=body("reuse", artifact_ids=[ref.artifact_id]))
        ).status_code == 202
        await app.runtime.wait(
            (await app.runtime.session("reuse", context=ALICE)).active_run_id
            or (await app.runtime.session("reuse", context=ALICE)).runs[0].run_id,
            context=ALICE,
        )
    # Upload ledger and consumed facts survive an actual SQLite/byte-store reopen.
    reopened = service(
        storage=SQLiteStorage(tmp_path / "state.db"),
        artifacts=LocalArtifacts(directory=tmp_path / "files"),
    )
    async with client(reopened) as connection:
        same = await connection.post(
            "/v1/runs", json=body(upload_ids=[upload_id], request_key="once")
        )
        assert same.json() == accepted.json()
        assert (
            await connection.get(f"/v1/artifacts/{ref.artifact_id}/content")
        ).content == b"value\n7\n"


@pytest.mark.asyncio
async def test_http_verify_uses_original_operation_and_response_does_not_repeat_write() -> None:
    from drov import ToolContext
    from drov.types import ReconciliationDecision

    writes: list[str] = []

    async def verifier(context: ToolContext) -> ReconciliationDecision:
        assert context.operation_id == writes[0] and context.user_id == "alice"
        return ReconciliationDecision(
            resolution="applied", result="written", evidence="independent fixture record"
        )

    @tool(name="write", effect="write", verifier=verifier, verifier_revision="1")
    async def write(context: ToolContext) -> str:
        writes.append(context.operation_id)
        raise RuntimeError("PRIVATE lost reply")

    model = ScriptedModel(
        [
            ModelResponse(
                finish_reason="tool_calls",
                tool_calls=[ToolCall(call_id="w", name="write", arguments={})],
            ),
            final(),
        ]
    )
    policy = Policy()
    app = service(definition(model, tools=[write]), authorizer=policy)
    async with client(app) as connection:
        accepted = await connection.post("/v1/runs", json=body())
        run_id = accepted.json()["run_id"]
        paused = await app.runtime.wait(run_id, context=ALICE)
        assert paused.status == "suspended" and paused.pending
        await connection.post(
            f"/v1/runs/{run_id}/responses",
            json={
                "request_id": paused.pending.request_id,
                "response": {"kind": "approval", "approved": True},
            },
        )
        unknown = await app.runtime.wait(run_id, context=ALICE)
        assert (
            unknown.status == "suspended" and unknown.reason == "reconciliation" and unknown.pending
        )
        policy.denied.add("verify")
        denied = await connection.post(
            f"/v1/runs/{run_id}/verify", json={"request_id": unknown.pending.request_id}
        )
        assert denied.status_code == 403
        policy.denied.clear()
        verified = await connection.post(
            f"/v1/runs/{run_id}/verify", json={"request_id": unknown.pending.request_id}
        )
        assert verified.status_code == 200 and verified.json()["resolution"] == "applied"
        assert len(writes) == 1
        original = (await app.runtime.inspect(run_id, context=ALICE, include_content=True)).snapshot
        assert (
            original.status == "suspended"
            and original.pending
            and original.pending.request_id == unknown.pending.request_id
        )
        response = await connection.post(
            f"/v1/runs/{run_id}/responses",
            json={"request_id": unknown.pending.request_id, "response": verified.json()},
        )
        assert response.status_code == 202
        assert (await app.runtime.wait(run_id, context=ALICE)).status == "completed"
        assert len(writes) == 1


@pytest.mark.asyncio
async def test_http_recover_original_saved_final_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "recovery.db"
    app = service(storage=SQLiteStorage(path))
    async with client(app) as connection:
        with patch.object(
            app.runtime._runs,
            "finish_response",
            side_effect=PersistenceUnavailable("injected final gap"),
        ):
            accepted = await connection.post("/v1/runs", json=body())
            run_id = accepted.json()["run_id"]
            with pytest.raises(PersistenceUnavailable):
                await app.runtime.wait(run_id, context=ALICE)
    model = ScriptedModel([])
    restored = service(definition(model), storage=SQLiteStorage(path))
    async with client(restored) as connection:
        response = await connection.post(f"/v1/runs/{run_id}/recover", json={})
        assert response.status_code == 202
        assert (await restored.runtime.wait(run_id, context=ALICE)).status == "completed"
        assert model.calls == []
        snapshot = await connection.get(f"/v1/runs/{run_id}?include_content=true")
        assert snapshot.json()["snapshot"]["outcome"]["output"] == "answer"


@pytest.mark.asyncio
async def test_failed_outcome_is_http_200_and_unexpected_errors_are_redacted() -> None:
    from drov.models import ScriptedStep

    app = service(
        definition(ScriptedModel([ScriptedStep(error=ValueError("PRIVATE model failure"))]))
    )
    async with client(app) as connection:
        accepted = await connection.post("/v1/runs", json=body())
        run_id = accepted.json()["run_id"]
        assert (await app.runtime.wait(run_id, context=ALICE)).status == "failed"
        snapshot = await connection.get(f"/v1/runs/{run_id}?include_content=true")
        assert snapshot.status_code == 200 and snapshot.json()["snapshot"]["status"] == "failed"
        assert "PRIVATE" not in snapshot.text
        with patch.object(
            app.runtime, "inspect", side_effect=RuntimeError("PRIVATE internal failure")
        ):
            response = await connection.get(f"/v1/runs/{run_id}")
            assert (
                response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
            )
            assert "PRIVATE" not in response.text
