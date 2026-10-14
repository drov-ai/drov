"""Host file admission, Run grants and dynamically authorized SDK projections."""

import asyncio
import hashlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from drov._json import StrictJson, canonical_json
from drov.artifacts.local import LocalArtifacts
from drov.artifacts.records import ArtifactGrant, ArtifactRecord
from drov.artifacts.transaction import ArtifactTransaction
from drov.artifacts.types import ArtifactInfo, ArtifactInput, ArtifactRef
from drov.auth import ArtifactResource
from drov.errors import (
    ArtifactUnavailable,
    CapacityExceeded,
    ConfigurationError,
    PermissionDenied,
    PersistenceUnavailable,
    RecordNotFound,
)
from drov.storage.records import MemoryScope, RunRecord
from drov.storage.transaction import Transaction
from drov.types import RunContext

if TYPE_CHECKING:
    from drov.runtime.core import Runtime


class ArtifactController:
    def __init__(self, runtime: "Runtime") -> None:
        self.runtime = runtime

    def store(self) -> LocalArtifacts:
        if self.runtime._artifacts is None:
            raise ConfigurationError("Files require an explicit Runtime ArtifactStore")
        return self.runtime._artifacts

    def binding(self) -> dict[str, StrictJson]:
        store = self.store()
        return {
            "store_id": store.store_id,
            "artifact_schema": 1,
            "limits_hash": hashlib.sha256(
                canonical_json(store.limits.model_dump()).encode()
            ).hexdigest(),
        }

    def normalize(
        self, artifacts: Sequence[ArtifactInput | ArtifactRef]
    ) -> tuple[list[ArtifactInput | ArtifactRef], list[dict[str, StrictJson]]]:
        if isinstance(artifacts, (str, bytes)) or len(artifacts) > 256:
            raise ConfigurationError("Expected a bounded sequence of ArtifactInput/ArtifactRef")
        checked: list[ArtifactInput | ArtifactRef] = []
        declarations: list[dict[str, StrictJson]] = []
        total_bytes = 0
        for item in artifacts:
            if isinstance(item, ArtifactInput):
                value = ArtifactInput(name=item.name, data=item.data, media_type=item.media_type)
                total_bytes += len(value.data)
                if total_bytes > 1024**3:
                    raise CapacityExceeded("Host input exceeds the finite administrative ceiling")
                checked.append(value)
                declarations.append(
                    {
                        "kind": "input",
                        "name": value.name,
                        "sha256": hashlib.sha256(value.data).hexdigest(),
                        "size_bytes": len(value.data),
                        "media_type": value.media_type,
                    }
                )
            elif isinstance(item, ArtifactRef):
                ref = ArtifactRef(
                    artifact_id=item.artifact_id,
                    sha256=item.sha256,
                    size_bytes=item.size_bytes,
                    media_type=item.media_type,
                )
                checked.append(ref)
                declarations.append(
                    cast(dict[str, StrictJson], {"kind": "ref", **ref.model_dump(mode="json")})
                )
            else:
                raise ConfigurationError("Files cannot be host paths, URLs or untyped values")
        if checked:
            self.store()
        return checked, declarations

    @staticmethod
    def resource(
        record: ArtifactRecord,
        *,
        target: RunRecord | None = None,
        target_run_id: str | None = None,
        target_agent: str | None = None,
        target_session: str | None = None,
    ) -> ArtifactResource:
        return ArtifactResource(
            owner=RunContext(tenant_id=record.owner.tenant_id, user_id=record.owner.user_id),
            artifact_id=record.ref.artifact_id,
            agent_name=record.owner.agent_name,
            run_id=record.run_id,
            operation_id=record.operation_id,
            target_run_id=target.state.run_id if target else target_run_id,
            target_agent_name=target.agent_name if target else target_agent,
            target_session_id=target.scope.session_id if target else target_session,
        )

    async def authorize(
        self, actor: RunContext, resource: ArtifactResource, *, content: bool
    ) -> None:
        if actor.tenant_id != resource.owner.tenant_id:
            raise RecordNotFound("Artifact is unavailable")
        for action in ("read", "view_content") if content else ("read",):
            try:
                async with asyncio.timeout(self.runtime.limits.verify_timeout_seconds):
                    allowed = await self.runtime.authorizer.authorize(
                        RunContext(tenant_id=actor.tenant_id, user_id=actor.user_id),
                        action,
                        ArtifactResource.model_validate(resource.model_dump()),
                    )
            except Exception:
                allowed = False
            if allowed is not True:
                if action == "read":
                    raise RecordNotFound("Artifact is unavailable")
                raise PermissionDenied("Artifact content permission is unavailable")

    async def load_bytes(self, record: ArtifactRecord) -> bytes:
        store = self.store()
        if record.store_id != store.store_id or record.state != "ready":
            raise PersistenceUnavailable("Artifact bytes are unavailable")
        return await store._read_staged(record.owner, record.stage_id, record.ref, name=record.name)

    async def admit(
        self,
        tx: Transaction,
        *,
        inputs: list[ArtifactInput | ArtifactRef],
        actor: RunContext,
        agent_name: str,
        session_id: str,
        run_id: str,
    ) -> list[ArtifactRecord]:
        if not inputs:
            return []
        store = self.store()
        size = sum(
            len(item.data) if isinstance(item, ArtifactInput) else item.size_bytes
            for item in inputs
        )
        if len(inputs) > store.limits.max_input_files or size > store.limits.max_input_bytes:
            raise CapacityExceeded("Run input files exceed configured limits")
        records: dict[str, ArtifactRecord] = {}
        art = ArtifactTransaction(tx)
        for item in inputs:
            if not isinstance(item, ArtifactRef):
                continue
            original = await art.locate(actor, item.artifact_id)
            await self.authorize(
                actor,
                self.resource(
                    original,
                    target_run_id=run_id,
                    target_agent=agent_name,
                    target_session=session_id,
                ),
                content=True,
            )
            if original.ref != item:
                raise RecordNotFound("Artifact reference does not match saved bytes")
            await self.load_bytes(original)
            records[item.artifact_id] = original
        names = [
            item.name if isinstance(item, ArtifactInput) else records[item.artifact_id].name
            for item in inputs
        ]
        ids = [item.artifact_id for item in inputs if isinstance(item, ArtifactRef)]
        if len(set(names)) != len(names) or len(set(ids)) != len(ids):
            raise ConfigurationError("Run inputs repeat an ID or normalized logical name")
        fresh = [item for item in inputs if isinstance(item, ArtifactInput)]
        new_records = []
        if fresh:
            owner = MemoryScope(**actor.model_dump(), agent_name=agent_name)
            reserved = await store._reserve(
                owner=owner,
                run_id=run_id,
                operation_id=None,
                purpose="input",
                size_bytes=sum(len(item.data) for item in fresh),
            )
            stage = await store._stage(reserved.stage_id, fresh)
            for entry in stage.files:
                record = ArtifactRecord(
                    ref=entry.ref,
                    name=entry.name,
                    owner=owner,
                    run_id=run_id,
                    store_id=store.store_id,
                    stage_id=stage.stage_id,
                )
                await self.authorize(
                    actor,
                    self.resource(
                        record,
                        target_run_id=run_id,
                        target_agent=agent_name,
                        target_session=session_id,
                    ),
                    content=True,
                )
                await self.load_bytes(record)
                await self.authorize(
                    actor,
                    self.resource(
                        record,
                        target_run_id=run_id,
                        target_agent=agent_name,
                        target_session=session_id,
                    ),
                    content=True,
                )
                new_records.append(record)
        # Recheck reused content permissions after disk I/O; no grant exists yet.
        for original in records.values():
            await self.authorize(
                actor,
                self.resource(
                    original,
                    target_run_id=run_id,
                    target_agent=agent_name,
                    target_session=session_id,
                ),
                content=True,
            )
        ordered = []
        fresh_iter = iter(new_records)
        for item in inputs:
            ordered.append(
                next(fresh_iter) if isinstance(item, ArtifactInput) else records[item.artifact_id]
            )
        return ordered

    async def publish_inputs(
        self, tx: Transaction, run: RunRecord, records: list[ArtifactRecord]
    ) -> None:
        if not records:
            return
        art = ArtifactTransaction(tx)
        for position, record in enumerate(records):
            if record.run_id == run.state.run_id:
                await art.insert(record)
            await art.grant(
                run.scope,
                ArtifactGrant(
                    run_id=run.state.run_id, ref=record.ref, name=record.name, position=position
                ),
            )

    async def validate(
        self,
        run: RunRecord,
        *,
        actor: RunContext | None = None,
        tx: Transaction | None = None,
        check_bytes: bool = False,
    ) -> list[ArtifactGrant]:
        if not run.artifact_inputs:
            return []
        self.store()
        if tx is None:
            assert self.runtime._storage is not None
            async with self.runtime._storage.transaction() as current:
                return await self.validate(run, actor=actor, tx=current, check_bytes=check_bytes)
        art = ArtifactTransaction(tx)
        grants = await art.grants(run.scope, run.state.run_id)
        for grant in grants:
            original = await art.locate(run.scope, grant.ref.artifact_id)
            if original.ref != grant.ref or original.name != grant.name:
                raise PersistenceUnavailable("Granted artifact metadata changed")
            if original.state != "ready":
                raise ArtifactUnavailable("Granted artifact is unavailable")
            if original.store_id != self.store().store_id:
                raise ConfigurationError("Original artifact store is not configured")
            await self.authorize(
                actor or run.scope, self.resource(original, target=run), content=True
            )
            if check_bytes:
                try:
                    await self.load_bytes(original)
                except (ConfigurationError, PersistenceUnavailable) as exc:
                    raise ArtifactUnavailable("Granted artifact bytes are unavailable") from exc
                await self.authorize(
                    actor or run.scope, self.resource(original, target=run), content=True
                )
        return grants

    async def get(
        self, artifact_id: str, *, context: RunContext, include_content: bool = False
    ) -> ArtifactInfo:
        self.store()
        context = RunContext.model_validate(context)

        async def query() -> ArtifactInfo:
            assert self.runtime._storage is not None
            async with self.runtime._storage.transaction() as tx:
                record = await ArtifactTransaction(tx).locate(context, artifact_id)
            await self.authorize(context, self.resource(record), content=include_content)
            async with self.runtime._storage.transaction() as tx:
                record = await ArtifactTransaction(tx).locate(context, artifact_id)
            await self.authorize(context, self.resource(record), content=include_content)
            return ArtifactInfo(
                **record.ref.model_dump(),
                run_id=record.run_id,
                operation_id=record.operation_id,
                state=record.state,
                content_visible=include_content,
                name=record.name if include_content else None,
            )

        return await self.runtime._runs.control(query)

    async def read(self, ref: ArtifactRef | str, *, context: RunContext) -> bytes:
        self.store()
        expected = (
            ArtifactRef(
                artifact_id=ref.artifact_id,
                sha256=ref.sha256,
                size_bytes=ref.size_bytes,
                media_type=ref.media_type,
            )
            if isinstance(ref, ArtifactRef)
            else None
        )
        if expected is None and type(ref) is not str:
            raise ConfigurationError("Expected an ArtifactRef or opaque artifact ID")
        artifact_id = expected.artifact_id if expected else cast(str, ref)
        context = RunContext.model_validate(context)

        async def query() -> bytes:
            assert self.runtime._storage is not None
            async with self.runtime._storage.transaction() as tx:
                record = await ArtifactTransaction(tx).locate(context, artifact_id)
            await self.authorize(context, self.resource(record), content=True)
            if expected is not None and record.ref != expected:
                raise RecordNotFound("Artifact reference differs from saved metadata")
            try:
                data = await self.load_bytes(record)
            except (PersistenceUnavailable, ConfigurationError):
                async with self.runtime._storage.transaction() as tx:
                    await ArtifactTransaction(tx).unavailable(record)
                raise PersistenceUnavailable("Artifact bytes are unavailable") from None
            async with self.runtime._storage.transaction() as tx:
                current = await ArtifactTransaction(tx).locate(context, artifact_id)
            await self.authorize(context, self.resource(current), content=True)
            if current != record:
                raise PersistenceUnavailable("Artifact availability changed during reading")
            return data

        return await self.runtime._runs.control(query)
