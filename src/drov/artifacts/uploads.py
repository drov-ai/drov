"""Private HTTP upload ledger; consumption and Run admission share one transaction."""

from datetime import timedelta
from typing import TYPE_CHECKING, Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from drov.artifacts.records import ArtifactRecord
from drov.artifacts.types import ArtifactInput, ArtifactRef, FileName, MediaType
from drov.auth import Resource
from drov.errors import ArtifactUnavailable, Conflict, PersistenceUnavailable, RecordNotFound
from drov.storage.records import MemoryScope
from drov.storage.sqlite import SQLiteDriver
from drov.storage.transaction import Transaction
from drov.types import RunContext, ScopedIdentifier, UtcTime, WireModel

if TYPE_CHECKING:
    from drov.runtime import Runtime

UPLOAD_DDL = (
    """CREATE TABLE artifact_uploads (
        upload_id TEXT PRIMARY KEY NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=1)
    )""",
)


class UploadTarget(WireModel):
    agent_name: ScopedIdentifier
    revision: ScopedIdentifier
    session_id: ScopedIdentifier
    name: FileName
    media_type: MediaType = "application/octet-stream"


class UploadReceipt(WireModel):
    upload_id: str = Field(pattern=r"^upl_[0-9a-f]{32}$")
    expires_at: UtcTime


class UploadRecord(UploadReceipt):
    schema_version: Literal[1] = 1
    owner: RunContext
    target: UploadTarget
    store_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    stage_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    state: Literal["receiving", "staged", "consumed"] = "receiving"
    ref: ArtifactRef | None = None
    run_id: str | None = None
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.state == "receiving") != (self.ref is None):
            raise ValueError("Staged/consumed uploads require a complete reference")
        if (self.state == "consumed") != (self.run_id is not None):
            raise ValueError("Only consumed uploads have a Run")
        if self.ref is not None and self.ref.size_bytes != self.size_bytes:
            raise ValueError("Upload size differs from staged bytes")
        return self


class Uploads:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime

    @staticmethod
    def driver(tx: Transaction) -> SQLiteDriver:
        tx._check()
        if not isinstance(tx._driver, SQLiteDriver):
            raise PersistenceUnavailable("Uploads require SQLite")
        return tx._driver

    async def get(self, tx: Transaction, actor: RunContext, upload_id: str) -> UploadRecord:
        rows, _ = await self.driver(tx).execute(
            "SELECT payload,version FROM artifact_uploads "
            "WHERE upload_id=? AND tenant_id=? AND user_id=?",
            (upload_id, actor.tenant_id, actor.user_id),
        )
        if not rows:
            raise RecordNotFound("Upload is unavailable")
        try:
            record = UploadRecord.from_json(rows[0][0])
            if (
                record.upload_id != upload_id
                or record.owner != actor
                or record.version != rows[0][1]
            ):
                raise ValueError("Upload index differs")
            return record
        except ValueError as exc:
            raise PersistenceUnavailable("Upload record is invalid") from exc

    async def replace(self, tx: Transaction, old: UploadRecord, new: UploadRecord) -> None:
        _, count = await self.driver(tx).execute(
            "UPDATE artifact_uploads SET payload=?,version=? WHERE upload_id=? AND version=?",
            (new.model_dump_json(), new.version, old.upload_id, old.version),
        )
        if count != 1:
            raise Conflict("Upload version changed")

    async def reserve(self, actor: RunContext, target: UploadTarget, size: int) -> UploadRecord:
        store = self.runtime._files.store()
        registration = self.runtime.registration(target.agent_name, target.revision)
        await self.runtime._runs.authorize(
            actor, Resource(owner=actor, session_id=target.session_id), ("start",)
        )
        owner = MemoryScope(**actor.model_dump(), agent_name=target.agent_name)
        async with self.runtime.storage.transaction() as tx:
            stage = await store._reserve(
                owner=owner,
                run_id="upload_" + uuid4().hex,
                operation_id=None,
                purpose="input",
                size_bytes=size,
            )
            record = UploadRecord(
                upload_id="upl_" + uuid4().hex,
                owner=actor,
                target=target,
                store_id=store.store_id,
                stage_id=stage.stage_id,
                config_hash=registration.config_hash,
                size_bytes=size,
                expires_at=tx._now() + timedelta(hours=1),
            )
            await self.driver(tx).execute(
                "INSERT INTO artifact_uploads VALUES (?,?,?,?,?)",
                (record.upload_id, actor.tenant_id, actor.user_id, record.model_dump_json(), 1),
            )
        return record

    async def stage(self, actor: RunContext, original: UploadRecord, data: bytes) -> UploadReceipt:
        if len(data) != original.size_bytes:
            raise Conflict("Upload length differs from reservation")
        store = self.runtime._files.store()
        async with self.runtime.storage.transaction() as tx:
            current = await self.get(tx, actor, original.upload_id)
            if current != original or current.state != "receiving":
                raise Conflict("Upload has already been received")
            if tx._now() >= current.expires_at:
                raise ArtifactUnavailable("Upload expired")
            await self.runtime._runs.authorize(
                actor, Resource(owner=actor, session_id=current.target.session_id), ("start",)
            )
            staged = await store._stage(
                current.stage_id,
                [
                    ArtifactInput(
                        name=current.target.name,
                        media_type=current.target.media_type,
                        data=data,
                    )
                ],
            )
            updated = current.model_copy(
                update={
                    "state": "staged",
                    "ref": staged.files[0].ref,
                    "version": current.version + 1,
                }
            )
            await self.replace(tx, current, updated)
        return UploadReceipt(upload_id=current.upload_id, expires_at=current.expires_at)

    async def consume(
        self,
        tx: Transaction,
        actor: RunContext,
        ids: tuple[str, ...],
        *,
        agent_name: str,
        revision: str,
        session_id: str,
        run_id: str,
    ) -> list[ArtifactRecord]:
        store = self.runtime._files.store()
        records = []
        for upload_id in ids:
            upload = await self.get(tx, actor, upload_id)
            if (upload.target.agent_name, upload.target.revision, upload.target.session_id) != (
                agent_name,
                revision,
                session_id,
            ):
                raise RecordNotFound("Upload is unavailable in this scope")
            if upload.state == "consumed":
                raise Conflict("Upload was already consumed")
            if upload.expires_at <= tx._now():
                raise ArtifactUnavailable("Upload expired")
            if upload.state != "staged" or upload.ref is None:
                raise Conflict("Upload is not ready for consumption")
            if upload.store_id != store.store_id:
                raise Conflict("Upload store changed")
            if upload.config_hash != self.runtime.registration(agent_name, revision).config_hash:
                raise Conflict("Upload Agent configuration changed")
            record = ArtifactRecord(
                ref=upload.ref,
                name=upload.target.name,
                owner=MemoryScope(**actor.model_dump(), agent_name=agent_name),
                run_id=run_id,
                store_id=store.store_id,
                stage_id=upload.stage_id,
            )
            await self.runtime._files.authorize(
                actor,
                self.runtime._files.resource(
                    record,
                    target_run_id=run_id,
                    target_agent=agent_name,
                    target_session=session_id,
                ),
                content=True,
            )
            await self.runtime._files.load_bytes(record)
            await self.runtime._files.authorize(
                actor, self.runtime._files.resource(record), content=True
            )
            await self.replace(
                tx,
                upload,
                upload.model_copy(
                    update={
                        "state": "consumed",
                        "run_id": run_id,
                        "version": upload.version + 1,
                    }
                ),
            )
            records.append(record)
        return records
