"""Upload rejection/expiry/transaction faults and private attachment boundaries."""

import asyncio
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from drov import SQLiteStorage
from drov.artifacts import ArtifactInput, ArtifactLimits, LocalArtifacts
from drov.artifacts.uploads import Uploads, UploadTarget
from drov.errors import Conflict, PersistenceUnavailable
from tests.test_http import ALICE, body, client, service

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private file store")
pytest.importorskip("fastapi")

TARGET = dict(
    agent_name="assistant", revision="1", session_id="s", name="file.csv", media_type="text/csv"
)
HEADERS = {"Content-Type": "application/octet-stream"}


def build(tmp_path: Path, **kwargs: Any) -> Any:
    return service(
        storage=SQLiteStorage(tmp_path / "state.db"),
        artifacts=LocalArtifacts(directory=tmp_path / "files", **kwargs),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "../escape"),
        ("name", "x\r\nInjected"),
        ("media_type", "text/html; bad=1"),
        ("host_path", "/tmp/private"),
        ("backend", "cloud"),
    ],
)
async def test_upload_rejects_untrusted_names_and_host_configuration(
    tmp_path: Path, field: str, value: str
) -> None:
    app = build(tmp_path)
    async with client(app) as connection:
        response = await connection.post(
            "/v1/artifacts", params={**TARGET, field: value}, content=b"data", headers=HEADERS
        )
        assert response.status_code == 422
        assert (await app.runtime._files.store()._pending()) == []


@pytest.mark.asyncio
async def test_upload_length_and_content_type_and_quota_are_bounded(tmp_path: Path) -> None:
    app = build(
        tmp_path,
        limits=ArtifactLimits(max_input_bytes=16, max_owner_bytes=20000, max_store_bytes=20000),
    )
    async with client(app) as connection:
        too_big = await connection.post(
            "/v1/artifacts", params=TARGET, content=b"x" * 17, headers=HEADERS
        )
        assert too_big.status_code == 413
        no_type = await connection.post("/v1/artifacts", params=TARGET, content=b"x")
        assert no_type.status_code == 415
        first = await connection.post("/v1/artifacts", params=TARGET, content=b"x", headers=HEADERS)
        assert first.status_code == 201
        second = await connection.post(
            "/v1/artifacts", params=TARGET, content=b"x", headers=HEADERS
        )
        assert second.status_code == 429
        assert len(await app.runtime._files.store()._pending()) == 1


@pytest.mark.asyncio
async def test_unfinished_expired_and_consumed_uploads_cannot_start_new_run(tmp_path: Path) -> None:
    app = build(tmp_path)
    async with client(app) as connection:
        uploads = Uploads(app.runtime)
        reserved = await uploads.reserve(ALICE, UploadTarget(**TARGET), 4)
        unfinished = await connection.post("/v1/runs", json=body(upload_ids=[reserved.upload_id]))
        assert unfinished.status_code == 409
        await uploads.stage(ALICE, reserved, b"data")
        with pytest.raises(Conflict):
            await uploads.stage(ALICE, reserved, b"data")
        async with app.runtime.storage.transaction() as tx:
            record = await uploads.get(tx, ALICE, reserved.upload_id)
            await uploads.replace(
                tx,
                record,
                record.model_copy(
                    update={
                        "expires_at": tx._now() - timedelta(seconds=1),
                        "version": record.version + 1,
                    }
                ),
            )
        expired = await connection.post("/v1/runs", json=body(upload_ids=[reserved.upload_id]))
        assert expired.status_code == 410
        assert (await app.runtime.sessions(context=ALICE)).items == []


@pytest.mark.asyncio
async def test_consumption_rollback_and_parallel_request_key_reuse(tmp_path: Path) -> None:
    app = build(tmp_path)
    async with client(app) as connection:
        upload = await connection.post(
            "/v1/artifacts", params=TARGET, content=b"data", headers=HEADERS
        )
        upload_id = upload.json()["upload_id"]
        from drov.storage.transaction import Transaction

        with patch.object(Transaction, "start", side_effect=PersistenceUnavailable("PRIVATE")):
            failed = await connection.post(
                "/v1/runs", json=body(upload_ids=[upload_id], request_key="same")
            )
        assert failed.status_code == 503 and "PRIVATE" not in failed.text
        async with app.runtime.storage.transaction() as tx:
            assert (await Uploads(app.runtime).get(tx, ALICE, upload_id)).state == "staged"
        first, duplicate = await asyncio.gather(
            *[
                connection.post("/v1/runs", json=body(upload_ids=[upload_id], request_key="same"))
                for _ in range(2)
            ]
        )
        assert first.status_code == duplicate.status_code == 202
        assert first.json() == duplicate.json()
        await app.runtime.wait(first.json()["run_id"], context=ALICE)
        assert len((await app.runtime.session("s", context=ALICE)).runs) == 1
        async with app.runtime.storage.transaction() as tx:
            assert (await Uploads(app.runtime).get(tx, ALICE, upload_id)).state == "consumed"


@pytest.mark.asyncio
async def test_input_aggregate_limits_and_duplicate_names_roll_back_uploads(tmp_path: Path) -> None:
    app = build(tmp_path, limits=ArtifactLimits(max_input_files=1))
    async with client(app) as connection:
        ids = []
        for name in ("a.csv", "b.csv"):
            receipt = await connection.post(
                "/v1/artifacts", params={**TARGET, "name": name}, content=b"x", headers=HEADERS
            )
            ids.append(receipt.json()["upload_id"])
        denied = await connection.post("/v1/runs", json=body(upload_ids=ids))
        assert denied.status_code == 429
        for upload_id in ids:
            async with app.runtime.storage.transaction() as tx:
                assert (await Uploads(app.runtime).get(tx, ALICE, upload_id)).state == "staged"
        duplicate = await connection.post("/v1/runs", json=body(upload_ids=[ids[0], ids[0]]))
        assert duplicate.status_code == 400


@pytest.mark.asyncio
async def test_html_attachment_missing_bytes_and_unavailable_reference(tmp_path: Path) -> None:
    app = build(tmp_path)
    async with client(app) as connection:
        a = app.runtime._agents[("assistant", "1")]
        outcome = await app.runtime.run(
            a,
            "hello",
            context=ALICE,
            session_id="s",
            artifacts=[
                ArtifactInput(
                    name='quoted".html', data=b"<script>private()</script>", media_type="text/html"
                )
            ],
        )
        record = await app.runtime.storage.get_run(ALICE, outcome.run_id)
        ref = record.artifact_inputs[0]
        response = await connection.get(f"/v1/artifacts/{ref.artifact_id}/content")
        assert response.headers["content-type"] == "application/octet-stream"
        assert (
            "attachment" in response.headers["content-disposition"]
            and "%22" in response.headers["content-disposition"]
        )
        assert response.headers["content-security-policy"].startswith("sandbox")
        async with app.runtime.storage.transaction() as tx:
            from drov.artifacts.transaction import ArtifactTransaction

            internal = await ArtifactTransaction(tx).locate(ALICE, ref.artifact_id)
        (tmp_path / "files" / internal.stage_id / ref.artifact_id).unlink()
        unavailable = await connection.get(f"/v1/artifacts/{ref.artifact_id}/content")
        assert unavailable.status_code == 410
        assert (await connection.get(f"/v1/artifacts/{ref.artifact_id}")).json()[
            "state"
        ] == "unavailable"
        denied = await connection.post(
            "/v1/runs", json=body("reuse", artifact_ids=[ref.artifact_id])
        )
        assert denied.status_code == 410


@pytest.mark.asyncio
async def test_revoke_during_attachment_send_prevents_next_chunk(tmp_path: Path) -> None:
    from tests.test_http import Policy

    app = build(tmp_path)
    policy = Policy()
    app.runtime._authorizer = policy
    async with client(app):
        a = app.runtime._agents[("assistant", "1")]
        data = b"private" * 20000
        outcome = await app.runtime.run(
            a,
            "hello",
            session_id="s",
            context=ALICE,
            artifacts=[ArtifactInput(name="data.csv", data=data)],
        )
        ref = (await app.runtime.storage.get_run(ALICE, outcome.run_id)).artifact_inputs[0]
        sent: list[dict[str, Any]] = []
        delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                policy.denied.add("view_content")

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.4"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": f"/v1/artifacts/{ref.artifact_id}/content",
                "query_string": b"",
                "root_path": "",
                "client": ("127.0.0.1", 1),
                "server": ("127.0.0.1", 8000),
                "headers": [(b"host", b"127.0.0.1:8000"), (b"authorization", b"Bearer alice")],
            },
            receive,
            send,
        )
        blocks = [msg["body"] for msg in sent if msg["type"] == "http.response.body"]
        assert b"".join(blocks) == data[:65536]
        assert not any(
            msg["type"] == "http.response.body" and not msg.get("more_body", False) for msg in sent
        )


@pytest.mark.asyncio
async def test_http_incomplete_and_unknown_length_upload_rejected(tmp_path: Path) -> None:
    app = build(tmp_path)
    async with client(app) as connection:

        async def stream() -> Any:
            yield b"value"

        unknown = await connection.post(
            "/v1/artifacts", params=TARGET, content=stream(), headers=HEADERS
        )
        assert unknown.status_code == 411
        too_many = await connection.post(
            "/v1/artifacts",
            params=TARGET,
            content=b"three",
            headers={**HEADERS, "Content-Length": "2"},
        )
        assert too_many.status_code == 413
        too_few = await connection.post(
            "/v1/artifacts",
            params=TARGET,
            content=b"one",
            headers={**HEADERS, "Content-Length": "4"},
        )
        assert too_few.status_code == 409
        assert (await app.runtime.sessions(context=ALICE)).items == []
        pending = await app.runtime._files.store()._pending()
        assert len(pending) == 2 and all(stage.state == "reserved" for stage in pending)
