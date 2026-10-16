"""Actual Docker CSV -> CSV/PNG, equal SDK/stream/HTTP facts and post-close HTTP bytes."""

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from drov import Agent, DrovApp, ResourceBinding, Runtime, SQLiteStorage
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.errors import ContentGone
from drov.models import ScriptedModel
from drov.models.protocol import ModelComplete, ModelEvent, ModelRequest, ModelResponse
from drov.sandbox import DockerSandbox, PythonSandbox
from drov.sandbox._png import validate_png
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import SessionScope
from examples.sandbox_offline import CSV, FixtureModel
from tests.test_agent_runs import final
from tests.test_http import ALICE, Auth, body, client
from tests.test_sandbox_docker import config as config

pytest.importorskip("fastapi")
pytestmark = pytest.mark.docker

PNG_CODE = r"""
import struct,zlib,binascii
w,h=120,80
raw=b''
for y in range(h):
    row=bytearray()
    for x in range(w):
        bar=(x//40)
        on=bar<3 and (x%40)<30 and (h-y)<=values[bar]*2
        row.extend((30,120,210) if on else (255,255,255))
    raw+=b'\x00'+row

def chunk(t,d):
    return struct.pack('>I',len(d))+t+d+struct.pack('>I',binascii.crc32(t+d)&0xffffffff)
png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
open('/work/outputs/chart.png','wb').write(png)
"""


class ChartModel(FixtureModel):
    async def generate(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        async for event in super().generate(request):
            assert isinstance(event, ModelComplete)
            response = event.response
            if response.tool_calls:
                call = response.tool_calls[0]
                args = dict(call.arguments)
                args["code"] = str(args["code"]) + PNG_CODE
                args["outputs"] = ["summary.csv", "chart.png"]
                response = ModelResponse(
                    finish_reason="tool_calls",
                    tool_calls=[call.model_copy(update={"arguments": args})],
                )
            yield ModelComplete(response=response)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["sdk", "stream", "http"])
async def test_actual_file_execution_entry_equivalence_and_post_close_download(
    tmp_path: Path, config: dict[str, str], entry: str
) -> None:
    model = ChartModel(chart=False)
    a: Agent[str] = Agent(
        name="assistant", revision="1", model=model, tools=[PythonSandbox(backend="code")]
    )
    backend = DockerSandbox(image=config["image"], docker=config["docker"], host=config["host"])
    runtime = Runtime(
        agents=[a],
        storage=SQLiteStorage(tmp_path / "state.db"),
        artifacts=LocalArtifacts(directory=tmp_path / "files"),
        dependencies={"code": ResourceBinding.owned(backend)},
    )
    async with runtime:
        app = DrovApp(runtime=runtime, auth=Auth())
        async with client(app) as connection:
            if entry == "http":
                uploaded = await connection.post(
                    "/v1/artifacts",
                    params=dict(
                        agent_name="assistant",
                        revision="1",
                        session_id="s",
                        name="numbers.csv",
                        media_type="text/csv",
                    ),
                    content=CSV,
                    headers={"Content-Type": "application/octet-stream"},
                )
                assert uploaded.status_code == 201, uploaded.text
                receipt = await connection.post(
                    "/v1/runs",
                    json=body(upload_ids=[uploaded.json()["upload_id"]], request_key="once"),
                )
                run_id = receipt.json()["run_id"]
                events = await connection.get(f"/v1/runs/{run_id}/events?include_content=true")
                assert "event: run.suspended" in events.text
                paused = await runtime.wait(run_id, context=ALICE)
            elif entry == "stream":
                async with runtime.stream(
                    a,
                    "hello",
                    context=ALICE,
                    session_id="s",
                    artifacts=[ArtifactInput(name="numbers.csv", media_type="text/csv", data=CSV)],
                ) as subscription:
                    async for _ in subscription:
                        pass
                    paused = await subscription.result()
            else:
                paused = await runtime.run(
                    a,
                    "hello",
                    context=ALICE,
                    session_id="s",
                    artifacts=[ArtifactInput(name="numbers.csv", media_type="text/csv", data=CSV)],
                )
            assert paused.status == "suspended" and paused.pending
            assert len(model.calls) == 1
            # No backend creation before the original persisted approval is consumed.
            async with runtime.storage.transaction() as tx:
                from drov.sandbox._leases import LeaseTransaction

                assert await LeaseTransaction(tx).active_count() == 0
            approved = await connection.post(
                f"/v1/runs/{paused.run_id}/responses",
                json={
                    "request_id": paused.pending.request_id,
                    "response": {"kind": "approval", "approved": True},
                },
            )
            assert approved.status_code == 202
            done = await runtime.wait(paused.run_id, context=ALICE)
            assert done.status == "completed" and model.result
            assert model.result.stdout == "60\n" and len(model.result.artifacts) == 2
            refs = model.result.artifacts
            downloaded = []
            for ref in refs:
                response = await connection.get(f"/v1/artifacts/{ref.artifact_id}/content")
                assert response.status_code == 200
                assert hashlib.sha256(response.content).hexdigest() == ref.sha256
                downloaded.append(response.content)
            assert downloaded[0] == b"count,sum,mean\n3,60,20.0\n"
            assert validate_png(downloaded[1]) == (120, 80)
            page = await connection.get(f"/v1/runs/{paused.run_id}")
            assert PNG_CODE not in page.text and "INPUT_PATH=" not in page.text
            full = await connection.get(f"/v1/runs/{paused.run_id}?include_content=true")
            assert "INPUT_PATH=" in full.text
            assert len(model.calls) == 2
            async with runtime.storage.transaction() as tx:
                assert await LeaseTransaction(tx).active_count() == 0
            if entry == "http":
                repeat = await connection.post(
                    "/v1/runs",
                    json=body(upload_ids=[uploaded.json()["upload_id"]], request_key="once"),
                )
                assert repeat.json() == receipt.json() and len(model.calls) == 2
    # No Agent/code backend is registered on reopen: inspection/download cannot execute.
    async with Runtime(
        agents=[],
        storage=SQLiteStorage(tmp_path / "state.db"),
        artifacts=LocalArtifacts(directory=tmp_path / "files"),
    ) as reader:
        app = DrovApp(runtime=reader, auth=Auth())
        async with client(app) as connection:
            for ref, expected in zip(refs, downloaded, strict=True):
                response = await connection.get(f"/v1/artifacts/{ref.artifact_id}/content")
                assert response.status_code == 200 and response.content == expected
                assert hashlib.sha256(response.content).hexdigest() == ref.sha256

    # Explicitly share both real outputs, then delete the origin. Inspection of
    # the surviving Run uses retained provenance, with no backend/model replay.
    reuse: Agent[str] = Agent(
        name="assistant", revision="reader", model=ScriptedModel([final("retained files")])
    )
    async with Runtime(
        agents=[reuse],
        storage=SQLiteStorage(tmp_path / "state.db"),
        artifacts=LocalArtifacts(directory=tmp_path / "files"),
    ) as reader:
        reused = await reader.run(
            reuse, "retain both files", session_id="reuse", context=ALICE, artifacts=refs
        )
        assert reused.status == "completed"
    async with SQLiteMaintenance(
        tmp_path / "state.db", artifacts=LocalArtifacts(directory=tmp_path / "files")
    ) as maintenance:
        plan = await maintenance.preview_session(SessionScope(**ALICE.model_dump(), session_id="s"))
        assert plan.retained_artifacts == 2 and plan.cleared_artifacts == 1
        await maintenance.delete_session(plan)
        from drov.storage.records import MemoryScope

        owner = MemoryScope(**ALICE.model_dump(), agent_name="assistant")
        garbage = await maintenance.preview_reclamation(owner)
        # The old input can be removed, while the batch with shared CSV/PNG stays.
        assert garbage.stages >= 1 and garbage.protected
        await maintenance.reclaim(garbage)
    async with Runtime(
        agents=[],
        storage=SQLiteStorage(tmp_path / "state.db"),
        artifacts=LocalArtifacts(directory=tmp_path / "files"),
    ) as reader:
        assert (
            await reader.inspect(reused.run_id, context=ALICE, include_content=True)
        ).snapshot.status == "completed"
        for ref, expected in zip(refs, downloaded, strict=True):
            assert await reader.read_artifact(ref, context=ALICE) == expected
    async with SQLiteMaintenance(
        tmp_path / "state.db", artifacts=LocalArtifacts(directory=tmp_path / "files")
    ) as maintenance:
        plan = await maintenance.preview_session(
            SessionScope(**ALICE.model_dump(), session_id="reuse")
        )
        assert plan.cleared_artifacts == 2 and plan.bytes_reclaimed == 0
        await maintenance.delete_session(plan)
        garbage = await maintenance.preview_reclamation(owner)
        assert garbage.stages >= 1 and not garbage.protected
        removed = await maintenance.reclaim(garbage)
        assert removed.bytes_reclaimed == sum(len(data) for data in downloaded)
        assert (await maintenance.preview_reclamation(owner)).stages == 0
    async with Runtime(
        agents=[],
        storage=SQLiteStorage(tmp_path / "state.db"),
        artifacts=LocalArtifacts(directory=tmp_path / "files"),
    ) as reader:
        for ref in refs:
            with pytest.raises(ContentGone):
                await reader.read_artifact(ref, context=ALICE)
    assert len(model.calls) == 2
