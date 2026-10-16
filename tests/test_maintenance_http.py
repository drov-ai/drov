"""Gone projections use the existing authenticated HTTP boundary."""

import os
from pathlib import Path

import pytest

from drov import DrovApp, SQLiteStorage
from drov.artifacts import LocalArtifacts
from drov.models import ScriptedModel
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import SessionScope
from tests.test_agent_runs import final
from tests.test_http import ALICE, Auth, Policy, body, client, definition

pytest.importorskip("fastapi")


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [False, True])
async def test_http_gone_dedup_guess_and_revoked_read(tmp_path: Path, files: bool) -> None:
    if files and os.name != "posix":
        pytest.skip("POSIX artifact layer")
    path = tmp_path / "state.db"
    a = definition(ScriptedModel([final("secret answer")] * 4))
    policy = Policy()

    def app() -> DrovApp:
        return DrovApp(
            agents={"assistant": a},
            storage=SQLiteStorage(path),
            auth=Auth(),
            authorizer=policy,
            artifacts=LocalArtifacts(directory=tmp_path / "files") if files else None,
        )

    artifact_id = None
    async with client(app()) as connection:
        upload_ids = []
        if files:
            upload = await connection.post(
                "/v1/artifacts",
                params=dict(
                    agent_name="assistant", revision="1", session_id="s", name="secret.txt"
                ),
                content=b"secret bytes",
                headers={"Content-Type": "application/octet-stream"},
            )
            assert upload.status_code == 201, upload.text
            upload_ids.append(upload.json()["upload_id"])
        started = await connection.post(
            "/v1/runs", json=body(request_key="once", upload_ids=upload_ids)
        )
        assert started.status_code == 202
        run_id = started.json()["run_id"]
        # Wait via an authenticated SSE response so the host's own Runtime closes
        # only after this synthetic Run actually reached its terminal state.
        events = await connection.get(f"/v1/runs/{run_id}/events?include_content=true")
        assert events.status_code == 200 and "run.completed" in events.text
    if files:
        async with SQLiteStorage(path) as storage:
            artifact_id = (await storage.get_run(ALICE, run_id)).artifact_inputs[0].artifact_id
    scope = SessionScope(**ALICE.model_dump(), session_id="s")
    async with SQLiteMaintenance(
        path, artifacts=LocalArtifacts(directory=tmp_path / "files") if files else None
    ) as maintenance:
        await maintenance.delete_session(await maintenance.preview_session(scope))
    async with client(app()) as connection:
        for endpoint in [
            f"/v1/runs/{run_id}",
            f"/v1/runs/{run_id}/events?after={run_id}:1",
            "/v1/sessions/s",
        ]:
            gone = await connection.get(endpoint)
            assert gone.status_code == 410, gone.text
            assert gone.json()["error"]["code"] == (
                "cursor_expired" if "after=" in endpoint else "content_gone"
            )
            assert "secret" not in gone.text
        duplicate = await connection.post(
            "/v1/runs", json=body(request_key="once", upload_ids=upload_ids)
        )
        assert duplicate.status_code == 410
        for control in ["cancel", "recover"]:
            assert (
                await connection.post(f"/v1/runs/{run_id}/{control}", json={})
            ).status_code == 410
        assert (
            await connection.get(f"/v1/runs/{run_id}", headers={"Authorization": "Bearer bob"})
        ).status_code == 404
        if artifact_id is not None:
            for suffix in ["", "/content"]:
                result = await connection.get(f"/v1/artifacts/{artifact_id}{suffix}")
                assert result.status_code == 410, result.text
                hidden = await connection.get(
                    f"/v1/artifacts/{artifact_id}{suffix}", headers={"Authorization": "Bearer bob"}
                )
                assert hidden.status_code == 404
        policy.denied.add("read")
        assert (await connection.get(f"/v1/runs/{run_id}")).status_code == 404
        if artifact_id is not None:
            assert (await connection.get(f"/v1/artifacts/{artifact_id}")).status_code == 404
        policy.denied.clear()
        assert (await connection.post("/v1/maintenance/delete", json={})).status_code == 404
        fresh = await connection.post("/v1/runs", json=body(session="fresh"))
        assert fresh.status_code == 202
