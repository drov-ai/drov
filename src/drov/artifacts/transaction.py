"""Artifact SQL uses the SAME owning SQLite transaction as Run acceptance/results."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar, cast

from drov.artifacts.records import ArtifactGrant, ArtifactRecord
from drov.errors import ConfigurationError, ContentGone, PersistenceUnavailable, RecordNotFound
from drov.storage._driver import Query
from drov.storage.sqlite import SQLiteDriver
from drov.storage.transaction import Transaction
from drov.types import RunContext

P = ParamSpec("P")
R = TypeVar("R")


def _write(method: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @wraps(method)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        transaction = cast(ArtifactTransaction, args[0]).tx
        transaction._check()
        try:
            return await method(*args, **kwargs)
        except BaseException:
            transaction._failed = True
            raise

    return guarded


class ArtifactTransaction:
    def __init__(self, transaction: Transaction) -> None:
        transaction._check()
        if not isinstance(transaction._driver, SQLiteDriver):
            raise ConfigurationError("Artifact metadata requires SQLite")
        self.tx, self.driver = transaction, transaction._driver

    async def locate(self, context: RunContext, artifact_id: str) -> ArtifactRecord:
        self.tx._check()
        rows, _ = await self.driver.execute(
            "SELECT payload,tenant_id,user_id,agent_name,source_run_id,state,version "
            "FROM artifact_records WHERE tenant_id=? AND artifact_id=?",
            (context.tenant_id, artifact_id),
        )
        if len(rows) != 1:
            await self.driver.check_retained(
                Query("artifact", context.tenant_id, context.user_id, key=artifact_id)
            )
            raise RecordNotFound("Artifact is unavailable")
        try:
            record = ArtifactRecord.from_json(rows[0][0])
            if (
                record.owner.tenant_id,
                record.owner.user_id,
                record.owner.agent_name,
                record.run_id,
                record.state,
                record.version,
            ) != tuple(rows[0][1:]) or record.ref.artifact_id != artifact_id:
                raise ValueError("Artifact payload differs from its index")
            try:
                origin = await self.tx.get_run(record.owner, record.run_id)
            except ContentGone:
                from drov.storage._retention import origin_identity

                proof, _ = await self.driver.execute(
                    "SELECT 1 FROM retention_tombstones WHERE kind='origin' AND identity=?",
                    (origin_identity(self.tx._secret, record),),
                )
                if not proof:
                    raise PersistenceUnavailable(
                        "Retained artifact source proof is missing"
                    ) from None
                return record
            if origin.agent_name != record.owner.agent_name:
                raise ValueError("Artifact source Agent does not match")
            if record.operation_id is None and record.ref not in origin.artifact_inputs:
                raise ValueError("Artifact input is absent from its source Run")
            return record
        except (ValueError, TypeError) as exc:
            raise PersistenceUnavailable("Artifact record is invalid") from exc

    @_write
    async def insert(self, record: ArtifactRecord) -> None:
        self.tx._check()
        await self.driver.execute(
            "INSERT INTO artifact_records VALUES (?,?,?,?,?,?,?,?)",
            (
                record.ref.artifact_id,
                record.owner.tenant_id,
                record.owner.user_id,
                record.owner.agent_name,
                record.run_id,
                record.model_dump_json(),
                record.state,
                record.version,
            ),
        )

    @_write
    async def grant(self, context: RunContext, grant: ArtifactGrant) -> None:
        run = await self.tx.get_run(context, grant.run_id)
        original = await self.locate(context, grant.ref.artifact_id)
        if original.state != "ready" or original.ref != grant.ref or original.name != grant.name:
            raise PersistenceUnavailable("Run grant does not match ready artifact bytes")
        if run.artifact_inputs[grant.position] != grant.ref:
            raise PersistenceUnavailable("Run input does not match its grant")
        await self.driver.execute(
            "INSERT INTO artifact_grants VALUES (?,?,?,?)",
            (grant.run_id, grant.ref.artifact_id, grant.position, grant.model_dump_json()),
        )

    async def grants(self, context: RunContext, run_id: str) -> list[ArtifactGrant]:
        run = await self.tx.get_run(context, run_id)
        if not run.artifact_inputs:
            return []
        rows, _ = await self.driver.execute(
            "SELECT payload,artifact_id,position FROM artifact_grants "
            "WHERE run_id=? ORDER BY position",
            (run_id,),
        )
        try:
            grants = [ArtifactGrant.from_json(row[0]) for row in rows]
            for index, (grant, row) in enumerate(zip(grants, rows, strict=True)):
                if (grant.run_id, grant.ref.artifact_id, grant.position) != (
                    run_id,
                    row[1],
                    index,
                ) or row[2] != index:
                    raise ValueError("Grant payload differs from its index")
            if [grant.ref for grant in grants] != run.artifact_inputs:
                raise ValueError("Run and grant list disagree")
            return grants
        except (ValueError, TypeError) as exc:
            raise PersistenceUnavailable("Artifact grants are invalid") from exc

    @_write
    async def unavailable(self, record: ArtifactRecord) -> None:
        self.tx._check()
        current = await self.locate(record.owner, record.ref.artifact_id)
        if current.state == "unavailable":
            return
        updated = current.model_copy(
            update={
                "state": "unavailable",
                "version": current.version + 1,
                "unavailable_reason": "bytes_unavailable",
            }
        )
        _, count = await self.driver.execute(
            "UPDATE artifact_records SET payload=?,state='unavailable',version=? "
            "WHERE artifact_id=? AND version=?",
            (updated.model_dump_json(), updated.version, updated.ref.artifact_id, current.version),
        )
        if count != 1:
            raise PersistenceUnavailable("Artifact availability update lost its version")
