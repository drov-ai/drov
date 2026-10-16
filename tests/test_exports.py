"""Export authorization is current, scoped, bounded and independent of storage."""

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from drov import Agent, Runtime, tool
from drov._json import StrictJson
from drov.artifacts import ArtifactInput, LocalArtifacts
from drov.auth import Action, ArtifactResource, OwnerAuthorizer, Resource
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    CursorExpired,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
)
from drov.models import ScriptedModel
from drov.storage import MemoryStorage, SQLiteStorage
from drov.storage.maintenance import SQLiteMaintenance
from drov.storage.records import SessionScope
from drov.types import Completed, RunContext
from tests.test_agent_runs import final

ALICE = RunContext(tenant_id="tenant", user_id="alice")
BOB = RunContext(tenant_id="tenant", user_id="bob")


class Policy(OwnerAuthorizer):
    def __init__(self) -> None:
        self.denied: set[str] = set()
        self.files: set[str] = set()
        self.file_content: set[str] = set()
        self.bob_read = False

    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
        if action in self.denied:
            return False
        if isinstance(resource, ArtifactResource) and resource.artifact_id in self.files:
            return False
        if (
            isinstance(resource, ArtifactResource)
            and action == "view_content"
            and resource.artifact_id in self.file_content
        ):
            return False
        if self.bob_read and actor == BOB:
            return action == "read"
        return await super().authorize(actor, action, resource)


def redact(document: dict[str, StrictJson]) -> dict[str, StrictJson]:
    return cast(
        dict[str, StrictJson], json.loads(json.dumps(document).replace("secret", "[redacted]"))
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("entry", ["run", "stream"])
async def test_default_metadata_full_context_redacted_copy_and_paging(
    tmp_path: Path, backend: str, entry: str
) -> None:
    model = ScriptedModel([final("secret answer")])
    a: Agent[str] = Agent(name="assistant", revision="1", model=model)
    storage = MemoryStorage() if backend == "memory" else SQLiteStorage(tmp_path / "state.db")
    async with Runtime(agents=[a], storage=storage) as runtime:
        if entry == "stream":
            async with runtime.stream(a, "secret prompt", session_id="s", context=ALICE) as events:
                async for _ in events:
                    pass
                done = await events.result()
        else:
            done = await runtime.run(a, "secret prompt", session_id="s", context=ALICE)
        metadata = await runtime.export_run(done.run_id, context=ALICE)
        assert (
            not metadata.content_visible
            and not metadata.files
            and "secret" not in metadata.model_dump_json()
        )
        body = await runtime.export_run(done.run_id, context=ALICE, include_content=True)
        assert (
            "secret answer" in body.model_dump_json() and "secret prompt" in body.model_dump_json()
        )
        filtered = await runtime.export_run(
            done.run_id, context=ALICE, include_content=True, redactor=redact
        )
        assert filtered.redacted and "secret" not in filtered.model_dump_json()
        assert (await runtime.export_run(done.run_id, context=ALICE, include_content=True)) == body
        assert len(model.calls) == 1
        cursor = None
        sequences = []
        while True:
            page = await runtime.export_run(done.run_id, context=ALICE, after=cursor, limit=2)
            sequences += [event.seq for event in page.page.events]
            if not page.page.has_more:
                break
            cursor = page.page.next_cursor
        assert sequences == [
            event.seq
            for event in (await runtime.inspect(done.run_id, context=ALICE, limit=500)).events
        ]


@pytest.mark.asyncio
async def test_current_read_and_content_permissions_and_callback_revoke(tmp_path: Path) -> None:
    policy = Policy()
    model = ScriptedModel([final("secret answer")])
    a: Agent[str] = Agent(name="assistant", revision="1", model=model)
    async with Runtime(
        agents=[a], storage=SQLiteStorage(tmp_path / "state.db"), authorizer=policy
    ) as runtime:
        done = await runtime.run(a, "secret prompt", session_id="s", context=ALICE)
        with pytest.raises(RecordNotFound):
            await runtime.export_run(done.run_id, context=BOB)
        policy.bob_read = True
        assert not (await runtime.export_run(done.run_id, context=BOB)).content_visible
        with pytest.raises(PermissionDenied):
            await runtime.export_run(done.run_id, context=BOB, include_content=True)
        with pytest.raises(RecordNotFound):
            await runtime.export_run(
                done.run_id, context=RunContext(tenant_id="other", user_id="alice")
            )

        def revoke(value: dict[str, StrictJson]) -> dict[str, StrictJson]:
            policy.denied.add("view_content")
            return value

        with pytest.raises(PermissionDenied):
            await runtime.export_run(
                done.run_id, context=ALICE, include_content=True, redactor=revoke
            )
        policy.denied = {"read"}
        with pytest.raises(RecordNotFound):
            await runtime.export_run(done.run_id, context=ALICE)
        assert len(model.calls) == 1


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX artifact byte layer")
async def test_selected_files_authorized_individually_and_redacted_copy_hashes(
    tmp_path: Path,
) -> None:
    policy = Policy()
    model = ScriptedModel([final("done")] * 2)
    a: Agent[str] = Agent(name="assistant", revision="1", model=model)
    path, files = tmp_path / "state.db", tmp_path / "files"
    async with Runtime(
        agents=[a],
        storage=SQLiteStorage(path),
        artifacts=LocalArtifacts(directory=files),
        authorizer=policy,
    ) as runtime:
        done = await runtime.run(
            a,
            "first",
            session_id="s",
            context=ALICE,
            artifacts=[ArtifactInput(name="secret.csv", data=b"secret bytes")],
        )
        ref = (await runtime.storage.get_run(ALICE, done.run_id)).artifact_inputs[0]
        other = await runtime.run(
            a,
            "other",
            session_id="other",
            context=ALICE,
            artifacts=[ArtifactInput(name="other.csv", data=b"other")],
        )
        unrelated = (await runtime.storage.get_run(ALICE, other.run_id)).artifact_inputs[0]
        before = path.read_bytes()
        default = await runtime.export_run(done.run_id, context=ALICE)
        assert default.files == [] and "secret.csv" not in default.model_dump_json()
        with pytest.raises(ConfigurationError):
            await runtime.export_run(done.run_id, context=ALICE, artifact_ids=[ref.artifact_id])
        with pytest.raises(RecordNotFound):
            await runtime.export_run(
                done.run_id,
                context=ALICE,
                include_content=True,
                artifact_ids=[unrelated.artifact_id],
            )
        selected = await runtime.export_run(
            done.run_id, context=ALICE, include_content=True, artifact_ids=[ref.artifact_id]
        )
        assert base64.b64decode(selected.files[0].data_base64) == b"secret bytes"
        assert selected.files[0].source == ref and selected.files[0].name == "secret.csv"

        def filter_file(value: dict[str, StrictJson]) -> dict[str, StrictJson]:
            document = cast(dict[str, list[dict[str, StrictJson]]], value)
            file = document["files"][0]
            data = b"[redacted]"
            file.update(
                data_base64=base64.b64encode(data).decode(),
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                name="redacted.csv",
            )
            return value

        redacted = await runtime.export_run(
            done.run_id,
            context=ALICE,
            include_content=True,
            artifact_ids=[ref.artifact_id],
            redactor=filter_file,
        )
        assert redacted.redacted and redacted.files[0].source == ref
        assert (
            redacted.files[0].sha256 != ref.sha256
            and base64.b64decode(redacted.files[0].data_base64) == b"[redacted]"
        )
        assert await runtime.read_artifact(ref, context=ALICE) == b"secret bytes"
        policy.file_content.add(ref.artifact_id)
        with pytest.raises(PermissionDenied):
            await runtime.export_run(
                done.run_id, context=ALICE, include_content=True, artifact_ids=[ref.artifact_id]
            )
        policy.file_content.clear()

        def inject(value: dict[str, StrictJson]) -> dict[str, StrictJson]:
            cast(dict[str, Any], value)["files"][0]["source"] = unrelated.model_dump(mode="json")
            return value

        with pytest.raises(ConfigurationError):
            await runtime.export_run(
                done.run_id,
                context=ALICE,
                include_content=True,
                artifact_ids=[ref.artifact_id],
                redactor=inject,
            )
        policy.files.add(ref.artifact_id)
        with pytest.raises((RecordNotFound, PermissionDenied)):
            await runtime.export_run(
                done.run_id, context=ALICE, include_content=True, artifact_ids=[ref.artifact_id]
            )
        policy.files.clear()

        def revoke(value: dict[str, StrictJson]) -> dict[str, StrictJson]:
            policy.files.add(ref.artifact_id)
            return value

        with pytest.raises((RecordNotFound, PermissionDenied)):
            await runtime.export_run(
                done.run_id,
                context=ALICE,
                include_content=True,
                artifact_ids=[ref.artifact_id],
                redactor=revoke,
            )
        assert path.read_bytes() == before and len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "identity",
        "authority",
        "event",
        "error",
        "expand",
        "state_flags",
        "context_metadata",
        "event_metadata",
    ],
)
async def test_redactor_cannot_rewrite_authority_or_committed_page(
    tmp_path: Path, change: str
) -> None:
    model = ScriptedModel([final("secret answer")])
    a: Agent[str] = Agent(name="assistant", revision="1", model=model)
    async with Runtime(agents=[a], storage=SQLiteStorage(tmp_path / "state.db")) as runtime:
        done = await runtime.run(a, "secret prompt", session_id="s", context=ALICE)

        def bad(value: dict[str, StrictJson]) -> dict[str, StrictJson]:
            doc = cast(dict[str, Any], value)
            if change == "identity":
                doc["page"]["snapshot"]["run_id"] = "different"
            elif change == "authority":
                doc["content_visible"] = False
            elif change == "event":
                doc["page"]["events"][0]["type"] = "future.changed"
            elif change == "error":
                raise ValueError("secret error payload")
            elif change == "state_flags":
                doc["page"]["snapshot"]["cancel_requested"] = True
            elif change == "context_metadata":
                doc["context_manifests"][0]["input_hash"] = "0" * 64
            elif change == "event_metadata":
                doc["page"]["events"][0]["data"]["usage"] = {"complete": False}
            else:
                doc["page"]["snapshot"]["outcome"]["output"] = "x" * 20000
            return value

        with pytest.raises(CapacityExceeded if change == "expand" else ConfigurationError) as error:
            await runtime.export_run(
                done.run_id, context=ALICE, include_content=True, redactor=bad, max_bytes=10000
            )
        assert "secret" not in str(error.value)
        outcome = await runtime.wait(done.run_id, context=ALICE)
        assert isinstance(outcome, Completed) and outcome.output == "secret answer"


@pytest.mark.asyncio
async def test_business_keys_not_scrubbed_and_oversized_export_fails(tmp_path: Path) -> None:
    from drov.models.protocol import ModelResponse, ToolCall

    @tool(name="read", effect="read")
    def read() -> dict[str, str]:
        return {"directory": "business value"}

    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=[ToolCall(call_id="c", name="read", arguments={})],
                finish_reason="tool_calls",
            ),
            final("done"),
        ]
    )
    a: Agent[str] = Agent(name="assistant", revision="1", model=model, tools=[read])
    async with Runtime(agents=[a], storage=SQLiteStorage(tmp_path / "state.db")) as runtime:
        done = await runtime.run(a, "input", session_id="s", context=ALICE)
        assert (
            "business value"
            in (
                await runtime.export_run(done.run_id, context=ALICE, include_content=True)
            ).model_dump_json()
        )
        with pytest.raises(CapacityExceeded):
            await runtime.export_run(done.run_id, context=ALICE, max_bytes=1)
        with pytest.raises(ConfigurationError):
            await runtime.export_run(done.run_id, context=ALICE, max_bytes=True)


@pytest.mark.asyncio
async def test_deleted_history_and_revoked_read_are_not_exported(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    model = ScriptedModel([final("secret answer")])
    a: Agent[str] = Agent(name="assistant", revision="1", model=model)
    async with Runtime(agents=[a], storage=SQLiteStorage(path)) as runtime:
        done = await runtime.run(a, "secret prompt", session_id="s", context=ALICE)
        page = await runtime.export_run(done.run_id, context=ALICE, limit=1)
    async with SQLiteMaintenance(path) as maintenance:
        await maintenance.delete_session(
            await maintenance.preview_session(SessionScope(**ALICE.model_dump(), session_id="s"))
        )
    async with Runtime(agents=[a], storage=SQLiteStorage(path)) as runtime:
        with pytest.raises(CursorExpired):
            await runtime.export_run(done.run_id, context=ALICE, after=page.page.next_cursor)
        with pytest.raises(RecordNotFound):
            await runtime.export_run(done.run_id, context=BOB)


@pytest.mark.asyncio
async def test_metadata_redactor_cannot_insert_content_and_private_recovery_payload_refused(
    tmp_path: Path,
) -> None:
    from drov.models.protocol import ModelResponse, ToolCall

    @tool(name="read_private", effect="read")
    def read_private() -> dict[str, str]:
        return {
            "execution_key": "a" * 32,
            "backend_hash": "b" * 64,
            "code_hash": "c" * 64,
            "backend_config": "private control",
        }

    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=[ToolCall(call_id="c", name="read_private", arguments={})],
                finish_reason="tool_calls",
            ),
            final("done"),
        ]
    )
    a: Agent[str] = Agent(name="assistant", revision="1", model=model, tools=[read_private])
    async with Runtime(agents=[a], storage=SQLiteStorage(tmp_path / "state.db")) as runtime:
        done = await runtime.run(a, "input", session_id="s", context=ALICE)
        assert (
            "private control"
            not in (await runtime.export_run(done.run_id, context=ALICE)).model_dump_json()
        )
        with pytest.raises(ConfigurationError):
            await runtime.export_run(done.run_id, context=ALICE, include_content=True)

        def insert(value: dict[str, StrictJson]) -> dict[str, StrictJson]:
            cast(dict[str, Any], value)["page"]["events"][0]["data"]["message"] = "secret body"
            return value

        with pytest.raises(ConfigurationError):
            await runtime.export_run(done.run_id, context=ALICE, redactor=insert)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
async def test_unknown_event_has_only_metadata_even_in_content_export(
    tmp_path: Path, version: int
) -> None:
    path = tmp_path / "state.db"
    model = ScriptedModel([final("done")])
    a: Agent[str] = Agent(name="assistant", revision="1", model=model)
    async with Runtime(agents=[a], storage=SQLiteStorage(path)) as runtime:
        done = await runtime.run(a, "input", session_id="s", context=ALICE)
        async with runtime.storage.transaction() as tx:
            from drov.storage.sqlite import SQLiteDriver

            assert isinstance(tx._driver, SQLiteDriver)
            row, _ = await tx._driver.execute(
                "SELECT rowid,payload FROM records WHERE kind='event' AND namespace=? "
                "ORDER BY sequence LIMIT 1",
                (done.run_id,),
            )
            payload = json.loads(row[0][1])
            payload["type"] = "future.event"
            payload["schema_version"] = version
            payload["data"] = {"private": "future secret"}
            await tx._driver.execute(
                "UPDATE records SET payload=? WHERE rowid=?", (json.dumps(payload), row[0][0])
            )
        if version == 2:
            with pytest.raises(PersistenceUnavailable):
                await runtime.export_run(done.run_id, context=ALICE, include_content=True)
            return
        result = await runtime.export_run(done.run_id, context=ALICE, include_content=True)
        assert result.page.events[0].type == "future.event" and result.page.events[0].data == {}
        assert "future secret" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_logical_execution_id_echo_and_preview_text_are_portable_copy_only(
    tmp_path: Path,
) -> None:
    from dataclasses import dataclass

    from drov import Limits
    from drov.models.protocol import ModelResponse, ToolCall

    @dataclass
    class Observation:
        execution_key: str
        exit_code: int
        termination: str
        stdout: str
        stderr: str
        artifact_status: str

    key = "d" * 32

    @tool(name="read_result", effect="read")
    def read_result() -> Observation:
        return Observation(
            execution_key=key,
            exit_code=0,
            termination="exited",
            stdout="x" * 2000 + key,
            stderr="",
            artifact_status="not_requested",
        )

    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=[ToolCall(call_id="c", name="read_result", arguments={})],
                finish_reason="tool_calls",
            ),
            final("execution was " + key),
        ]
    )
    a: Agent[str] = Agent(
        name="assistant",
        revision="1",
        model=model,
        tools=[read_result],
        limits=Limits(tool_preview_bytes=512),
    )
    async with Runtime(agents=[a], storage=SQLiteStorage(tmp_path / "state.db")) as runtime:
        done = await runtime.run(a, "input", session_id="s", context=ALICE)
        result = await runtime.export_run(done.run_id, context=ALICE, include_content=True)
        assert key not in result.model_dump_json() and "export_omitted" in result.model_dump_json()
        source = await runtime.storage.get_run(ALICE, done.run_id)
        assert source.state.outcome is not None and key in source.state.outcome.model_dump_json()
        assert len(model.calls) == 2


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX paired files")
async def test_retained_shared_file_export_survives_origin_cleanup_and_restore_barrier(
    tmp_path: Path,
) -> None:
    path, files = tmp_path / "state.db", tmp_path / "files"
    model = ScriptedModel([final("done")] * 2)
    a: Agent[str] = Agent(name="assistant", revision="1", model=model)
    async with Runtime(
        agents=[a], storage=SQLiteStorage(path), artifacts=LocalArtifacts(directory=files)
    ) as runtime:
        source = await runtime.run(
            a,
            "source",
            session_id="source",
            context=ALICE,
            artifacts=[ArtifactInput(name="shared.csv", data=b"x\n1\n")],
        )
        ref = (await runtime.storage.get_run(ALICE, source.run_id)).artifact_inputs[0]
        consumer = await runtime.run(
            a, "use", session_id="consumer", context=ALICE, artifacts=[ref]
        )
    async with SQLiteMaintenance(path, artifacts=LocalArtifacts(directory=files)) as maintenance:
        await maintenance.delete_session(
            await maintenance.preview_session(
                SessionScope(**ALICE.model_dump(), session_id="source")
            )
        )
        await maintenance.backup(tmp_path / "backup")
    restored = await SQLiteMaintenance.restore(tmp_path / "backup", tmp_path / "restored")
    for database, directory in ((str(path), str(files)), (restored.database, restored.artifacts)):
        assert directory is not None
        async with Runtime(
            agents=[],
            storage=SQLiteStorage(database),
            artifacts=LocalArtifacts(directory=directory, limits=restored.artifact_limits),
        ) as runtime:
            exported = await runtime.export_run(
                consumer.run_id, context=ALICE, include_content=True, artifact_ids=[ref.artifact_id]
            )
            assert base64.b64decode(exported.files[0].data_base64) == b"x\n1\n"
            assert exported.files[0].source == ref
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_tool_argument_schema_properties_are_preserved_in_content_export(
    tmp_path: Path,
) -> None:
    from drov.models.protocol import ModelResponse, ToolCall

    @tool(name="business", effect="read")
    def business(
        execution_key: str,
        termination: str,
        artifact_status: str,
        stdout: str,
        stderr: str,
        exit_code: int,
    ) -> str:
        return "business result"

    arguments: dict[str, StrictJson] = {
        "execution_key": "business key",
        "termination": "state",
        "artifact_status": "state",
        "stdout": "text",
        "stderr": "text",
        "exit_code": 0,
    }
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=[ToolCall(call_id="c", name="business", arguments=arguments)],
                finish_reason="tool_calls",
            ),
            final("done"),
        ]
    )
    a: Agent[str] = Agent(name="assistant", revision="1", model=model, tools=[business])
    async with Runtime(agents=[a], storage=SQLiteStorage(tmp_path / "state.db")) as runtime:
        done = await runtime.run(a, "input", session_id="s", context=ALICE)
        source = await runtime.get_context(
            done.run_id, model.calls[0].model_call_id, context=ALICE, include_content=True
        )
        exported = await runtime.export_run(done.run_id, context=ALICE, include_content=True)
        expected = [item.content for item in source.sources if item.kind == "schema"]
        actual = [
            item.content
            for manifest in exported.context_manifests
            if manifest.model_call_id == source.model_call_id
            for item in manifest.sources
            if item.kind == "schema"
        ]
        assert actual == expected and "business key" in exported.model_dump_json()
