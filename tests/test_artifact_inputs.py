"""Real SDK/SQLite file admission and revocation; no sandbox or model inference."""

import asyncio
import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from drov import Agent, ResourceBinding, RunContext, Runtime
from drov.artifacts import ArtifactInput, ArtifactLimits, ArtifactRef, LocalArtifacts
from drov.artifacts.records import ArtifactGrant
from drov.artifacts.transaction import ArtifactTransaction
from drov.auth import Action, ArtifactResource, OwnerAuthorizer, Resource
from drov.errors import (
    CapacityExceeded,
    ConfigurationError,
    Conflict,
    IncompatibleStorage,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
)
from drov.models import ScriptedModel
from drov.storage import MemoryStorage, SQLiteStorage
from drov.types import Completed, Failed
from tests.test_agent_runs import final

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private file byte layer")
ALICE = RunContext(tenant_id="tenant", user_id="alice")
BOB = RunContext(tenant_id="tenant", user_id="bob")
DATA = b"value\n12\n18\n30\n"


def agent(name: str = "analysis", steps: int = 20) -> Agent[str]:
    return Agent(name=name, revision="1", model=ScriptedModel([final("metadata only")] * steps))


def runtime(path: Path, a: Agent[Any], **kwargs: Any) -> Runtime:
    return Runtime(
        agents=[a],
        storage=SQLiteStorage(path / "state.db"),
        artifacts=ResourceBinding.owned(LocalArtifacts(directory=path / "files")),
        **kwargs,
    )


async def reference(rt: Runtime, run_id: str, actor: RunContext = ALICE) -> ArtifactRef:
    record = await rt.storage.get_run(actor, run_id)
    return record.artifact_inputs[0]


async def uploaded(rt: Runtime, a: Agent[Any], *, session: str = "s") -> ArtifactRef:
    result = await rt.run(
        a,
        "use file metadata",
        session_id=session,
        context=ALICE,
        artifacts=[ArtifactInput(name="numbers.csv", data=DATA, media_type="text/csv")],
    )
    assert isinstance(result, Completed)
    return await reference(rt, result.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["run", "start", "stream"])
async def test_actual_input_public_read_and_protected_context(tmp_path: Path, entry: str) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        files = [ArtifactInput(name="numbers.csv", data=DATA, media_type="text/csv")]
        if entry == "run":
            result = await rt.run(a, "describe", session_id="s", context=ALICE, artifacts=files)
        elif entry == "start":
            accepted = await rt.start(a, "describe", session_id="s", context=ALICE, artifacts=files)
            result = await rt.wait(accepted.run_id, context=ALICE)
        else:
            async with rt.stream(
                a, "describe", session_id="s", context=ALICE, artifacts=files
            ) as stream:
                async for _ in stream:
                    pass
                result = await stream.result()
        assert isinstance(result, Completed)
        ref = await reference(rt, result.run_id)
        assert ref.sha256 == hashlib.sha256(DATA).hexdigest()
        metadata = await rt.get_artifact(ref.artifact_id, context=ALICE)
        assert metadata.name is None and metadata.content_visible is False
        assert not any(
            key in metadata.model_dump() for key in ("stage_id", "store_id", "owner", "directory")
        )
        body = await rt.get_artifact(ref.artifact_id, context=ALICE, include_content=True)
        assert body.name == "numbers.csv" and body.state == "ready"
        assert await rt.read_artifact(ref, context=ALICE) == DATA
        assert await rt.read_artifact(ref.artifact_id, context=ALICE) == DATA
        assert await rt.read_artifact(metadata, context=ALICE) == DATA
        model = a.model
        assert isinstance(model, ScriptedModel)
        request = model.calls[0]
        serialized = request.model_dump_json()
        assert ref.artifact_id in serialized and "/work/inputs/" in serialized
        assert str(tmp_path) not in serialized and "value\\n12" not in serialized
        manifest = await rt.get_context(
            result.run_id, request.model_call_id, context=ALICE, include_content=True
        )
        sources = [source for source in manifest.sources if source.kind == "artifact"]
        assert manifest.schema_version == 2
        assert len(sources) == 1 and sources[0].version == ref.sha256 and sources[0].included
        hidden = await rt.get_context(result.run_id, request.model_call_id, context=ALICE)
        assert all(source.content is None for source in hidden.sources)


@pytest.mark.asyncio
async def test_bytes_read_after_storage_and_runtime_reopen(tmp_path: Path) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        ref = await uploaded(rt, a)
    async with runtime(tmp_path, agent()) as rt:
        assert await rt.read_artifact(ref, context=ALICE) == DATA
        body = await rt.get_artifact(ref.artifact_id, context=ALICE, include_content=True)
        assert body.name == "numbers.csv"


@pytest.mark.asyncio
async def test_same_key_dedup_precedes_capacity_and_does_not_write_new_stage(
    tmp_path: Path,
) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        files = [ArtifactInput(name="numbers.csv", data=DATA)]
        first = await rt.run(
            a, "describe", session_id="s", context=ALICE, request_key="same", artifacts=files
        )
        count = len(await rt._files.store()._pending())
        # Occupy all currently available slots; retries must still return the original Run.
        slots = [rt._reserve() for _ in range(rt.limits.active_slots)]
        try:
            again = await rt.start(
                a, "describe", session_id="s", context=ALICE, request_key="same", artifacts=files
            )
        finally:
            for slot in slots:
                slot.release()
        assert again.run_id == first.run_id
        assert len(await rt._files.store()._pending()) == count
        assert isinstance(a.model, ScriptedModel) and len(a.model.calls) == 1
        for changed in (
            [ArtifactInput(name="changed.csv", data=DATA)],
            [ArtifactInput(name="numbers.csv", data=b"different")],
            [ArtifactInput(name="numbers.csv", data=DATA, media_type="text/csv")],
            [],
        ):
            with pytest.raises(Conflict):
                await rt.start(
                    a,
                    "describe",
                    session_id="s",
                    context=ALICE,
                    request_key="same",
                    artifacts=changed,
                )
        assert len(await rt._files.store()._pending()) == count


@pytest.mark.asyncio
async def test_file_order_is_part_of_request_identity(tmp_path: Path) -> None:
    a = agent()
    files = [ArtifactInput(name="one", data=b"1"), ArtifactInput(name="two", data=b"2")]
    async with runtime(tmp_path, a) as rt:
        await rt.run(
            a, "describe", session_id="s", context=ALICE, request_key="key", artifacts=files
        )
        with pytest.raises(Conflict):
            await rt.start(
                a,
                "describe",
                session_id="s",
                context=ALICE,
                request_key="key",
                artifacts=files[::-1],
            )


@pytest.mark.asyncio
async def test_explicit_ref_reuse_cross_session_has_no_copy_and_implicit_ids_have_no_grant(
    tmp_path: Path,
) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        ref = await uploaded(rt, a)
        count = len(await rt._files.store()._pending())
        reused = await rt.run(a, "reuse", session_id="second", context=ALICE, artifacts=[ref])
        assert isinstance(reused, Completed)
        assert await reference(rt, reused.run_id) == ref
        assert len(await rt._files.store()._pending()) == count
        implicit = await rt.run(
            a, "ID in text: " + ref.artifact_id, session_id="third", context=ALICE
        )
        assert not (await rt.storage.get_run(ALICE, implicit.run_id)).artifact_inputs
        async with rt.storage.transaction() as tx:
            assert await ArtifactTransaction(tx).grants(ALICE, implicit.run_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["tenant", "user", "agent", "hash", "size", "type"])
async def test_no_default_cross_scope_or_forged_reference(tmp_path: Path, changed: str) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        ref = await uploaded(rt, a)
        actor = (
            RunContext(tenant_id="other", user_id="alice")
            if changed == "tenant"
            else BOB
            if changed == "user"
            else ALICE
        )
        a2 = agent("other") if changed == "agent" else a
        if changed == "agent":
            # Reopen with another registered Agent; the data retains its original Agent.
            pass
        else:
            bad = (
                ref.model_copy(update={"sha256": "f" * 64})
                if changed == "hash"
                else ref.model_copy(update={"size_bytes": 1})
                if changed == "size"
                else ref.model_copy(update={"media_type": "text/plain"})
                if changed == "type"
                else ref
            )
            with pytest.raises((RecordNotFound, PermissionDenied)):
                await rt.start(a2, "reuse", session_id="next", context=actor, artifacts=[bad])
            assert isinstance(a.model, ScriptedModel) and len(a.model.calls) == 1
    if changed == "agent":
        async with runtime(tmp_path, a2) as rt:
            with pytest.raises((RecordNotFound, PermissionDenied)):
                await rt.start(a2, "reuse", session_id="next", context=actor, artifacts=[ref])


class Authority(OwnerAuthorizer):
    def __init__(self) -> None:
        self.shared = False
        self.body = True
        self.calls: list[ArtifactResource] = []

    async def authorize(self, actor: RunContext, action: Action, resource: Resource) -> bool:
        if isinstance(resource, ArtifactResource):
            self.calls.append(resource)
            if action == "view_content" and not self.body:
                return False
            if self.shared and actor.tenant_id == resource.owner.tenant_id:
                return True
        return await super().authorize(actor, action, resource)


@pytest.mark.asyncio
async def test_explicit_shared_grant_and_revocation_block_cached_metadata_context_and_bytes(
    tmp_path: Path,
) -> None:
    a, authority = agent(), Authority()
    async with runtime(tmp_path, a, authorizer=authority) as rt:
        ref = await uploaded(rt, a)
        authority.shared = True
        result = await rt.run(a, "reuse", session_id="bob", context=BOB, artifacts=[ref])
        assert isinstance(result, Completed)
        assert await rt.read_artifact(ref, context=BOB) == DATA
        assert any(
            resource.target_run_id == result.run_id
            and resource.target_agent_name == a.name
            and resource.target_session_id == "bob"
            for resource in authority.calls
        )
        authority.body = False
        assert (await rt.get_artifact(ref.artifact_id, context=BOB)).content_visible is False
        with pytest.raises(PermissionDenied):
            await rt.read_artifact(ref, context=BOB)
        with pytest.raises(PermissionDenied):
            await rt.get_artifact(ref.artifact_id, context=BOB, include_content=True)
        with pytest.raises(PermissionDenied):
            await rt.wait(result.run_id, context=BOB)
        assert (await rt.inspect(result.run_id, context=BOB)).snapshot.content_visible is False


@pytest.mark.asyncio
async def test_revoke_during_actual_disk_read_never_returns_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, authority = agent(), Authority()
    async with runtime(tmp_path, a, authorizer=authority) as rt:
        ref = await uploaded(rt, a)
        original = rt._files.store()._read_staged

        async def revoke_after_read(*args: Any, **kwargs: Any) -> bytes:
            data = await original(*args, **kwargs)
            authority.body = False
            return data

        monkeypatch.setattr(rt._files.store(), "_read_staged", revoke_after_read)
        with pytest.raises(PermissionDenied):
            await rt.read_artifact(ref, context=ALICE)
        assert (await rt.get_artifact(ref.artifact_id, context=ALICE)).state == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing", "hash", "symlink"])
async def test_missing_or_changed_ready_bytes_are_marked_unavailable_without_reimport(
    tmp_path: Path, damage: str
) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        ref = await uploaded(rt, a)
        stage = (await rt._files.store()._pending())[0]
        path = rt._files.store().directory / stage.stage_id / ref.artifact_id
        if damage == "hash":
            path.write_bytes(b"!" * len(DATA))
        else:
            path.unlink()
            if damage == "symlink":
                (tmp_path / "outside").write_bytes(DATA)
                path.symlink_to(tmp_path / "outside")
        with pytest.raises(PersistenceUnavailable):
            await rt.read_artifact(ref, context=ALICE)
        assert (await rt.get_artifact(ref.artifact_id, context=ALICE)).state == "unavailable"
        assert isinstance(a.model, ScriptedModel) and len(a.model.calls) == 1
        with pytest.raises(PersistenceUnavailable):
            await rt.start(a, "reuse", session_id="new", context=ALICE, artifacts=[ref])
        assert len(await rt._files.store()._pending()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["count", "bytes", "names", "url"])
async def test_bad_inputs_have_no_ready_rows_run_or_model(tmp_path: Path, bad: str) -> None:
    a = agent()
    limits = (
        ArtifactLimits(max_input_files=1, max_input_bytes=2)
        if bad in ("count", "bytes")
        else ArtifactLimits()
    )
    store = LocalArtifacts(directory=tmp_path / "files", limits=limits)
    async with Runtime(
        agents=[a], storage=SQLiteStorage(tmp_path / "state.db"), artifacts=store
    ) as rt:
        files: Any = (
            [ArtifactInput(name="one", data=b"123")]
            if bad == "bytes"
            else [ArtifactInput(name="one", data=b"1"), ArtifactInput(name="two", data=b"2")]
            if bad == "count"
            else [ArtifactInput(name="é", data=b"1"), ArtifactInput(name="e\u0301", data=b"2")]
            if bad == "names"
            else ["https://example.com/file"]
        )
        with pytest.raises((CapacityExceeded, ConfigurationError)):
            await rt.start(a, "describe", session_id="s", context=ALICE, artifacts=files)
        assert not await store._pending()
        assert isinstance(a.model, ScriptedModel) and not a.model.calls
        with closing(sqlite3.connect(tmp_path / "state.db")) as connection:
            assert connection.execute("SELECT count(*) FROM artifact_records").fetchone()[0] == 0
            assert (
                connection.execute("SELECT count(*) FROM records WHERE kind='run'").fetchone()[0]
                == 0
            )


@pytest.mark.asyncio
async def test_real_sqlite_grant_failure_rolls_back_ready_run_and_dedup_but_keeps_bytes(
    tmp_path: Path,
) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        with closing(sqlite3.connect(tmp_path / "state.db")) as connection:
            connection.execute(
                "CREATE TRIGGER refuse_grant BEFORE INSERT ON artifact_grants "
                "BEGIN SELECT RAISE(ABORT,'fixture refusal'); END"
            )
        with pytest.raises(Conflict):
            await rt.start(
                a,
                "describe",
                session_id="s",
                context=ALICE,
                request_key="k",
                artifacts=[ArtifactInput(name="x", data=DATA)],
            )
        pending = await rt._files.store()._pending()
        assert len(pending) == 1 and pending[0].state == "staged"
        with pytest.raises(RecordNotFound):
            await rt.get_artifact(pending[0].files[0].ref.artifact_id, context=ALICE)
        with closing(sqlite3.connect(tmp_path / "state.db")) as connection:
            assert connection.execute("SELECT count(*) FROM artifact_records").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM artifact_grants").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 0
            connection.execute("DROP TRIGGER refuse_grant")
        assert isinstance(a.model, ScriptedModel) and not a.model.calls
        result = await rt.run(
            a,
            "describe",
            session_id="s",
            context=ALICE,
            request_key="k",
            artifacts=[ArtifactInput(name="x", data=DATA)],
        )
        assert isinstance(result, Completed)
        assert len(await rt._files.store()._pending()) == 2
        assert await rt.read_artifact(await reference(rt, result.run_id), context=ALICE) == DATA


@pytest.mark.asyncio
async def test_before_publication_actual_byte_corruption_never_becomes_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        store = rt._files.store()
        original = store._stage

        async def corrupt(*args: Any, **kwargs: Any) -> Any:
            stage = await original(*args, **kwargs)
            (store.directory / stage.stage_id / stage.files[0].ref.artifact_id).write_bytes(
                b"!" * len(DATA)
            )
            return stage

        monkeypatch.setattr(store, "_stage", corrupt)
        with pytest.raises(PersistenceUnavailable):
            await rt.start(
                a,
                "describe",
                session_id="s",
                context=ALICE,
                artifacts=[ArtifactInput(name="x", data=DATA)],
            )
        stage = (await store._pending())[0]
        with pytest.raises(RecordNotFound):
            await rt.get_artifact(stage.files[0].ref.artifact_id, context=ALICE)
        assert isinstance(a.model, ScriptedModel) and not a.model.calls


@pytest.mark.asyncio
async def test_explicit_store_ownership_and_borrowed_pairing(tmp_path: Path) -> None:
    a = agent()
    storage = SQLiteStorage(tmp_path / "state.db")
    store = LocalArtifacts(directory=tmp_path / "files")
    await storage.open()
    try:
        await store.open(storage=storage)
        try:
            async with Runtime(
                agents=[a],
                storage=ResourceBinding.borrowed(storage),
                artifacts=ResourceBinding.borrowed(store),
            ) as rt:
                ref = await uploaded(rt, a)
            assert store.is_open and storage.is_open
            assert ref
        finally:
            await store.aclose()
    finally:
        await storage.aclose()


@pytest.mark.asyncio
async def test_no_store_or_sqlite_rejected_before_model_or_file_creation(tmp_path: Path) -> None:
    a = agent()
    async with Runtime(agents=[a]) as rt:
        with pytest.raises(ConfigurationError):
            await rt.start(
                a,
                "describe",
                session_id="s",
                context=ALICE,
                artifacts=[ArtifactInput(name="x", data=DATA)],
            )
    store = LocalArtifacts(directory=tmp_path / "files")
    with pytest.raises(ConfigurationError):
        async with Runtime(agents=[a], storage=MemoryStorage(), artifacts=store):
            pass
    assert not store.directory.exists()
    assert isinstance(a.model, ScriptedModel) and not a.model.calls


@pytest.mark.asyncio
async def test_corrupt_extension_definition_fails_closed_and_old_core_db_stays_compatible(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    async with SQLiteStorage(path) as storage:
        with closing(sqlite3.connect(path)) as connection:
            assert {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            } == {"records", "metadata"}
        store = LocalArtifacts(directory=tmp_path / "files")
        await store.open(storage=storage)
        await store.aclose()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP INDEX artifact_owners")
    with pytest.raises(IncompatibleStorage):
        async with SQLiteStorage(path):
            pass


@pytest.mark.asyncio
async def test_fresh_bytes_denied_by_business_policy_remain_private(tmp_path: Path) -> None:
    a, authority = agent(), Authority()
    authority.body = False
    async with runtime(tmp_path, a, authorizer=authority) as rt:
        with pytest.raises(PermissionDenied):
            await rt.start(
                a,
                "describe",
                session_id="s",
                context=ALICE,
                artifacts=[ArtifactInput(name="x", data=DATA)],
            )
        stage = (await rt._files.store()._pending())[0]
        with pytest.raises(RecordNotFound):
            await rt.get_artifact(stage.files[0].ref.artifact_id, context=ALICE)
        assert isinstance(a.model, ScriptedModel) and not a.model.calls


@pytest.mark.asyncio
async def test_caller_cancel_during_admission_commits_one_run_and_retry_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        store = rt._files.store()
        original = store._stage
        entered, release = asyncio.Event(), asyncio.Event()

        async def gated(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(store, "_stage", gated)
        files = [ArtifactInput(name="x", data=DATA)]
        receiver = asyncio.create_task(
            rt.start(a, "describe", session_id="s", context=ALICE, request_key="k", artifacts=files)
        )
        await asyncio.wait_for(entered.wait(), 2)
        receiver.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await receiver
        accepted = await rt.start(
            a, "describe", session_id="s", context=ALICE, request_key="k", artifacts=files
        )
        assert isinstance(await rt.wait(accepted.run_id, context=ALICE), Completed)
        assert len(await store._pending()) == 1
        assert isinstance(a.model, ScriptedModel) and len(a.model.calls) == 1


@pytest.mark.asyncio
async def test_caught_artifact_write_failure_still_aborts_transaction(tmp_path: Path) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        ref = await uploaded(rt, a)
        original_run = (await rt.get_artifact(ref.artifact_id, context=ALICE)).run_id
        with pytest.raises(PersistenceUnavailable):
            async with rt.storage.transaction() as tx:
                files = ArtifactTransaction(tx)
                record = await files.locate(ALICE, ref.artifact_id)
                await files.unavailable(record)
                with pytest.raises(PersistenceUnavailable):
                    await files.grant(
                        ALICE, ArtifactGrant(run_id=original_run, ref=ref, name="wrong", position=0)
                    )
        assert (await rt.get_artifact(ref.artifact_id, context=ALICE)).state == "ready"


@pytest.mark.asyncio
async def test_changed_store_limits_cannot_continue_saved_run(tmp_path: Path) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        ref = await uploaded(rt, a)
        saved = await rt.storage.get_run(
            ALICE, (await rt.get_artifact(ref.artifact_id, context=ALICE)).run_id
        )
    async with Runtime(
        agents=[agent()],
        storage=SQLiteStorage(tmp_path / "state.db"),
        artifacts=LocalArtifacts(
            directory=tmp_path / "files", limits=ArtifactLimits(max_input_bytes=16 * 1024**2)
        ),
    ) as rt:
        with pytest.raises(Conflict):
            rt.validate_record(saved)
        assert await rt.read_artifact(ref, context=ALICE) == DATA


@pytest.mark.asyncio
async def test_temporary_agent_rejects_files_without_calling_model() -> None:
    a = agent()
    with pytest.raises(ConfigurationError):
        await a.run("describe", artifacts=[ArtifactInput(name="x", data=DATA)])
    assert isinstance(a.model, ScriptedModel) and not a.model.calls


@pytest.mark.asyncio
async def test_context_v1_and_boolean_versions_cannot_claim_artifact_sources(
    tmp_path: Path,
) -> None:
    from pydantic import ValidationError

    from drov.types import ContextManifest

    a = agent()
    async with runtime(tmp_path, a) as rt:
        ref = await uploaded(rt, a)
        info = await rt.get_artifact(ref.artifact_id, context=ALICE)
        assert isinstance(a.model, ScriptedModel)
        manifest = await rt.get_context(
            info.run_id, a.model.calls[0].model_call_id, context=ALICE, include_content=True
        )
        for version in (1, True, 3):
            with pytest.raises(ValidationError):
                ContextManifest.model_validate(manifest.model_dump() | {"schema_version": version})


@pytest.mark.asyncio
async def test_input_lost_after_receipt_is_durable_failure_before_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = agent()
    async with runtime(tmp_path, a) as rt:
        store = rt._files.store()
        original = store._read_staged

        async def delete_after_ready(*args: Any, **kwargs: Any) -> bytes:
            with closing(sqlite3.connect(tmp_path / "state.db")) as connection:
                ready = connection.execute("SELECT count(*) FROM artifact_records").fetchone()[0]
            if ready:
                stage = (await store._pending())[0]
                (store.directory / stage.stage_id / stage.files[0].ref.artifact_id).unlink(
                    missing_ok=True
                )
            return await original(*args, **kwargs)

        monkeypatch.setattr(store, "_read_staged", delete_after_ready)
        result = await rt.run(
            a,
            "describe",
            session_id="s",
            context=ALICE,
            artifacts=[ArtifactInput(name="x", data=DATA)],
        )
        assert isinstance(result, Failed) and result.error.code == "artifact_unavailable"
        assert isinstance(a.model, ScriptedModel) and not a.model.calls
