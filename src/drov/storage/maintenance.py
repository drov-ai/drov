"""Explicit offline retention, reclamation, abandonment and quarantined backups.

Backup bundles are administrator copies, separate from authorized user exports.
Restored stores remain blocked pending independent business reconciliation.
"""

import asyncio
import hmac
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from drov._json import canonical_json
from drov.artifacts.local import LocalArtifacts
from drov.artifacts.records import ArtifactRecord
from drov.artifacts.transaction import ArtifactTransaction
from drov.artifacts.types import Sha256
from drov.artifacts.uploads import UploadRecord
from drov.errors import (
    ConfigurationError,
    Conflict,
    ContentGone,
    DrovError,
    PersistenceUnavailable,
    RecordNotFound,
    StorageInUse,
)
from drov.sandbox._leases import LeaseTransaction, SandboxLease
from drov.storage._abandonment import (
    AbandonmentFact,
    AbandonmentPlan,
)
from drov.storage._abandonment import (
    apply as apply_abandonment,
)
from drov.storage._abandonment import (
    lookup as lookup_abandonment,
)
from drov.storage._abandonment import (
    matches as abandonment_matches,
)
from drov.storage._abandonment import (
    select as select_abandonment,
)
from drov.storage._backup import BackupManifest, RestoreResult
from drov.storage._barrier import RecoveryBarrier
from drov.storage._driver import Key, Row
from drov.storage._reclamation import (
    RECLAMATION_DDL,
    ReclamationEntry,
    ReclamationPlan,
    ReclamationResult,
    capture,
    entries,
    erase,
)
from drov.storage._retention import (
    RETENTION_DDL,
    Tombstone,
    cost_key,
    identity,
    origin_identity,
    retained_cost,
)
from drov.storage.records import MemoryRecord, MemoryScope, RunItem, RunRecord, SessionScope
from drov.storage.sqlite import SQLiteDriver, SQLiteStorage, _settle
from drov.storage.transaction import UNFINISHED, Transaction, _decode
from drov.tools.operations import ToolOperation
from drov.types import RunContext, UtcTime, WireModel

__all__ = [
    "SQLiteMaintenance",
    "SessionDeletionPlan",
    "ReclamationPlan",
    "ReclamationResult",
    "AbandonmentPlan",
    "AbandonmentFact",
    "BackupManifest",
    "RestoreResult",
    "RecoveryBarrier",
]


class SessionDeletionPlan(WireModel):
    """Authorized host-local preview. No prompts, names, arguments or results."""

    scope: SessionScope
    include_memory: bool
    fingerprint: Sha256
    counts: dict[str, int]
    blockers: list[str]
    retained_artifacts: int
    cleared_artifacts: int
    unreferenced_stages: int
    bytes_reclaimed: int = 0


@dataclass
class _Selection:
    plan: SessionDeletionPlan
    rows: list[Row]
    runs: list[RunRecord]
    origins: list[ArtifactRecord]
    cleared: list[ArtifactRecord]
    leases: list[SandboxLease]
    uploads: list[UploadRecord]


def _mentions(item: Row, record: ArtifactRecord, owner_runs: set[str]) -> bool:
    if (item.key.tenant_id, item.key.user_id) != (record.owner.tenant_id, record.owner.user_id):
        return False
    if item.key.kind == "memory":
        scoped = item.key.namespace == record.owner.agent_name
    elif item.key.kind == "run":
        scoped = item.key.key in owner_runs
    else:
        scoped = item.key.kind not in ("session", "start_key") and (
            item.key.namespace in owner_runs
        )
    return scoped and record.ref.artifact_id in item.payload


class SQLiteMaintenance:
    """Trusted local administrator capability, guarded by the Runtime's locks.

    Requires an existing database, and its original LocalArtifacts when paired.
    Do not expose this filesystem capability through an application route. A
    preview is read-only; deletion rechecks the exact snapshot in one transaction.
    """

    def __init__(self, path: str | Path, *, artifacts: LocalArtifacts | None = None) -> None:
        self._storage = SQLiteStorage(path)
        self._artifacts = artifacts
        self._gate = asyncio.Lock()
        self._opened = False
        self._owns_artifacts = False

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def open(self) -> None:
        async with self._gate:
            if self._opened or self._storage.is_open:
                raise StorageInUse("Maintenance already owns storage")
            if not self._storage.path.is_file() or self._storage.path.is_symlink():
                raise ConfigurationError("Maintenance requires an existing regular database")
            if self._artifacts is not None and self._artifacts.is_open:
                raise StorageInUse("Maintenance requires an unopened artifact store")
            try:
                await self._storage._open(initialize=False)
                metadata, _ = await self._storage._sql("SELECT key,value FROM metadata")
                values = dict(metadata)
                paired = "artifact_schema" in values
                if paired:
                    if "artifact_store_id" not in values:
                        raise ConfigurationError(
                            "Maintenance cannot repair missing artifact pairing"
                        )
                    if self._artifacts is None:
                        raise ConfigurationError("Maintenance requires the paired artifact store")
                    if not (self._artifacts.directory / "store.json").is_file():
                        raise ConfigurationError("Maintenance cannot create an artifact store")
                    await self._artifacts._open_for_maintenance(storage=self._storage)
                    self._owns_artifacts = True
                elif self._artifacts is not None:
                    raise ConfigurationError("Maintenance cannot add an artifact extension")
                self._opened = True
            except BaseException:
                await self._close()
                raise

    async def _close(self) -> None:
        # Settle byte ownership before releasing the database lock, even if the
        # caller cancels close. A failed byte close retains the database owner.
        async def close() -> None:
            if self._artifacts is not None and self._owns_artifacts:
                await self._artifacts.aclose()
                self._owns_artifacts = False
            await self._storage.aclose()
            self._opened = False

        settled = await _settle(close())
        if settled.error is not None:
            raise settled.error
        if settled.cancelled:
            raise asyncio.CancelledError

    async def aclose(self) -> None:
        async with self._gate:
            await self._close()

    def _require_open(self) -> None:
        if not self._opened:
            raise ConfigurationError("Maintenance is not open")
        self._storage._require_open()

    @staticmethod
    def _driver(tx: Transaction) -> SQLiteDriver:
        tx._check()
        assert isinstance(tx._driver, SQLiteDriver)
        return tx._driver

    async def _no_pending_reclamation(self, tx: Transaction) -> None:
        if any(item.state == "pending" for item in await entries(self._driver(tx))):
            raise Conflict("Resume the pending byte reclamation first")

    async def _snapshot(self, tx: Transaction) -> dict[str, Any]:
        driver = self._driver(tx)
        integrity, _ = await driver.execute("PRAGMA quick_check")
        foreign, _ = await driver.execute("PRAGMA foreign_key_check")
        if integrity != [("ok",)] or foreign:
            raise PersistenceUnavailable("Maintenance refuses an inconsistent database")
        tables, _ = await driver.execute("SELECT name FROM sqlite_master WHERE type='table'")
        snapshot: dict[str, Any] = {}
        for (name,) in sorted(tables):
            rows, _ = await driver.execute(f'SELECT * FROM "{name}" ORDER BY rowid')
            snapshot[name] = [list(row) for row in rows]
        return snapshot

    async def preview_session(
        self, scope: SessionScope, *, include_memory: bool = False
    ) -> SessionDeletionPlan:
        scope = SessionScope.from_json(scope.model_dump_json())
        if type(include_memory) is not bool:
            raise ConfigurationError("include_memory must be explicit boolean")
        async with self._gate:
            self._require_open()
            async with self._storage.transaction() as tx:
                return (await self._select(tx, scope, include_memory)).plan

    async def _select(
        self, tx: Transaction, scope: SessionScope, include_memory: bool
    ) -> _Selection:
        await self._no_pending_reclamation(tx)
        session = await tx.get_session(scope)
        # Hash the entire locked snapshot: other sessions, grants, and ledgers can
        # change whether a reference may be released. Only the HMAC is returned.
        snapshot = await self._snapshot(tx)
        records = [Row(Key(*row[:5]), *row[5:]) for row in snapshot["records"]]
        runs = [
            _decode(RunRecord, row)
            for row in records
            if row.key.kind == "run"
            and (row.key.tenant_id, row.key.user_id, row.key.namespace)
            == (scope.tenant_id, scope.user_id, scope.session_id)
        ]
        if not runs:
            raise PersistenceUnavailable("Session has no retained runs")
        ids = {run.state.run_id for run in runs}

        def selected(row: Row) -> bool:
            key = row.key
            if (key.tenant_id, key.user_id) != (scope.tenant_id, scope.user_id):
                return False
            if key.kind == "session":
                return key.namespace == "" and key.key == scope.session_id
            if key.kind in ("run", "start_key"):
                return key.namespace == scope.session_id
            if key.kind == "memory":
                return include_memory and key.namespace == session.agent_name
            return key.namespace in ids

        target = [row for row in records if selected(row)]
        remaining = [row for row in records if not selected(row)]
        blockers = set()
        if session.active_run_id is not None:
            blockers.add("active_session")
        if any(run.state.status in UNFINISHED for run in runs):
            blockers.add("unfinished_run")
        if include_memory:
            for row in remaining:
                if row.key.kind == "run" and (row.key.tenant_id, row.key.user_id) == (
                    scope.tenant_id,
                    scope.user_id,
                ):
                    other = _decode(RunRecord, row)
                    if other.agent_name == session.agent_name and (
                        other.state.status in UNFINISHED
                        or any(
                            op.status in ("dispatched", "unknown") for op in other.state.operations
                        )
                    ):
                        blockers.add("memory_scope_in_use")
        operations = {}
        for row in target:
            if row.key.kind == "memory":
                _decode(MemoryRecord, row)
            if row.key.kind != "operation":
                continue
            try:
                item = _decode(RunItem, row)
                op = ToolOperation.from_json(canonical_json(item.data))
                if (op.run_id, op.operation_id, op.version, op.status) != (
                    item.run_id,
                    item.item_id,
                    item.version,
                    item.state,
                ):
                    raise ValueError("Operation index differs")
            except (ValueError, TypeError) as exc:
                raise PersistenceUnavailable("Invalid operation prevents maintenance") from exc
            operations[(op.run_id, op.operation_id)] = op
        for run in runs:
            for summary in run.state.operations:
                operation = operations.get((run.state.run_id, summary.operation_id))
                if operation is None or operation.summary() != summary:
                    raise PersistenceUnavailable("Run and operation facts disagree")
            uncertain = [
                op
                for (run_id, _), op in operations.items()
                if run_id == run.state.run_id and op.status in ("dispatched", "unknown")
            ]
            if uncertain:
                fact = await lookup_abandonment(tx, scope, run.state.run_id)
                if fact is None or not abandonment_matches(
                    fact,
                    run,
                    [op for (run_id, _), op in operations.items() if run_id == run.state.run_id],
                ):
                    blockers.add("unresolved_operation")

        leases = []
        other_leases = []
        for row in snapshot.get("sandbox_leases", []):
            try:
                lease = SandboxLease.from_json(row[8])
                LeaseTransaction._decode(
                    (row[8], row[0], *row[1:4], row[6], row[7]),
                    row[4],
                    row[5],
                    lease.owner,
                )
            except (ValueError, TypeError) as exc:
                raise PersistenceUnavailable(
                    "Invalid execution lease prevents maintenance"
                ) from exc
            if lease.run_id not in ids:
                other_leases.append(lease)
                continue
            if (lease.owner.tenant_id, lease.owner.user_id, lease.owner.agent_name) != (
                scope.tenant_id,
                scope.user_id,
                session.agent_name,
            ):
                raise PersistenceUnavailable("Execution lease owner differs")
            leases.append(lease)
            if lease.active or (lease.create_intent and not lease.destroyed):
                blockers.add("unconfirmed_sandbox")

        uploads = []
        other_uploads = []
        for row in snapshot.get("artifact_uploads", []):
            try:
                upload = UploadRecord.from_json(row[3])
                if (
                    upload.upload_id,
                    upload.owner.tenant_id,
                    upload.owner.user_id,
                    upload.version,
                ) != (row[0], row[1], row[2], row[4]):
                    raise ValueError("Upload index differs")
            except (ValueError, TypeError) as exc:
                raise PersistenceUnavailable("Invalid upload prevents maintenance") from exc
            if upload.owner == RunContext(**scope.model_dump(exclude={"session_id"})) and (
                upload.target.session_id == scope.session_id
            ):
                uploads.append(upload)
            else:
                other_uploads.append(upload)

        origins, cleared = [], []
        grants = snapshot.get("artifact_grants", [])
        target_artifacts = {row[1] for row in grants if row[0] in ids}
        remaining_grants = {row[1] for row in grants if row[0] not in ids}
        art = ArtifactTransaction(tx) if "artifact_records" in snapshot else None
        for row in snapshot.get("artifact_records", []):
            if row[4] not in ids and row[0] not in target_artifacts:
                continue
            assert art is not None
            record = await art.locate(scope, row[0])
            if record.run_id in ids:
                origins.append(record)
            # A surviving source Run is itself a reference. Untrusted mentions
            # conservatively retain bytes but can never grant access to them.
            source_survives = any(
                item.key.kind == "run" and item.key.key == record.run_id for item in remaining
            )
            owner_runs = {
                item.key.key
                for item in remaining
                if item.key.kind == "run"
                and (item.key.tenant_id, item.key.user_id)
                == (record.owner.tenant_id, record.owner.user_id)
                and _decode(RunRecord, item).agent_name == record.owner.agent_name
            }

            keep = (
                source_survives
                or record.ref.artifact_id in remaining_grants
                or any(_mentions(item, record, owner_runs) for item in remaining)
                or any(grant.ref == record.ref for lease in other_leases for grant in lease.inputs)
                or any(
                    upload.ref == record.ref
                    and upload.stage_id == record.stage_id
                    and (upload.owner.tenant_id, upload.owner.user_id, upload.target.agent_name)
                    == (record.owner.tenant_id, record.owner.user_id, record.owner.agent_name)
                    for upload in other_uploads
                )
                or (record.owner.tenant_id, record.owner.user_id)
                != (scope.tenant_id, scope.user_id)
            )
            if not keep:
                cleared.append(record)

        stages = await self._artifacts._pending() if self._artifacts is not None else []
        removed = {record.ref.artifact_id for record in cleared}
        held_stages = {
            ArtifactRecord.from_json(row[5]).stage_id
            for row in snapshot.get("artifact_records", [])
            if row[0] not in removed
        }
        held_stages.update(lease.stage_id for lease in other_leases if lease.stage_id is not None)
        held_stages.update(upload.stage_id for upload in other_uploads)
        # Blocked plans are previews only; do not present their current leases as
        # reclaimable. No stages are physically removed by this increment.
        held_stages.update(
            lease.stage_id for lease in leases if lease.active and lease.stage_id is not None
        )
        fingerprint = tx._digest(
            {
                "scope": scope.model_dump(),
                "include_memory": include_memory,
                "snapshot": snapshot,
                "stages": [
                    stage.model_dump() for stage in sorted(stages, key=lambda item: item.stage_id)
                ],
            }
        )
        counts = dict(Counter(row.key.kind for row in target))
        counts.update(sandbox_leases=len(leases), uploads=len(uploads))
        plan = SessionDeletionPlan(
            scope=scope,
            include_memory=include_memory,
            fingerprint=fingerprint,
            counts=counts,
            blockers=sorted(blockers),
            retained_artifacts=len(
                target_artifacts | {record.ref.artifact_id for record in origins}
            )
            - len(cleared),
            cleared_artifacts=len(cleared),
            unreferenced_stages=sum(stage.stage_id not in held_stages for stage in stages),
        )
        return _Selection(plan, target, runs, origins, cleared, leases, uploads)

    async def delete_session(self, plan: SessionDeletionPlan) -> SessionDeletionPlan:
        """Apply an unchanged preview atomically; never bypass a blocker."""
        plan = SessionDeletionPlan.from_json(plan.model_dump_json())
        async with self._gate:
            self._require_open()
            async with self._storage.transaction() as tx:
                selection = await self._select(tx, plan.scope, plan.include_memory)
                if not hmac.compare_digest(selection.plan.fingerprint, plan.fingerprint) or (
                    selection.plan != plan
                ):
                    raise Conflict("Maintenance preview changed; obtain a new preview")
                if plan.blockers:
                    raise Conflict("Protected execution facts prevent session deletion")
                driver = self._driver(tx)
                metadata, _ = await driver.execute(
                    "SELECT value FROM metadata WHERE key='retention_schema'"
                )
                if not metadata:
                    for statement in RETENTION_DDL:
                        await driver.execute(statement)
                    await driver.execute("INSERT INTO metadata VALUES ('retention_schema','1')")
                now = tx._now()

                async def tombstone(kind: str, digest: str, fact: Tombstone) -> None:
                    await driver.execute(
                        "INSERT INTO retention_tombstones VALUES (?,?,?)",
                        (kind, digest, fact.model_dump_json()),
                    )

                def digest(kind: str, key: str, namespace: str = "") -> str:
                    return identity(
                        tx._secret, kind, plan.scope.tenant_id, plan.scope.user_id, key, namespace
                    )

                await tombstone(
                    "session",
                    digest("session", plan.scope.session_id),
                    Tombstone(
                        resource_id=plan.scope.session_id,
                        session_id=plan.scope.session_id,
                        deleted_at=now,
                    ),
                )
                for run in selection.runs:
                    await tombstone(
                        "run",
                        digest("run", run.state.run_id),
                        Tombstone(
                            resource_id=run.state.run_id,
                            session_id=plan.scope.session_id,
                            agent_name=run.agent_name,
                            status=run.state.status,
                            deleted_at=now,
                        ),
                    )
                for row in selection.rows:
                    if row.key.kind == "start_key":
                        await tombstone(
                            "start_key",
                            digest("start_key", row.key.key, row.key.namespace),
                            Tombstone(deleted_at=now),
                        )
                for record in selection.origins:
                    await tombstone(
                        "origin",
                        origin_identity(tx._secret, record),
                        Tombstone(
                            resource_id=record.run_id,
                            agent_name=record.owner.agent_name,
                            deleted_at=now,
                        ),
                    )
                for record in selection.cleared:
                    await tombstone(
                        "artifact",
                        digest("artifact", record.ref.artifact_id),
                        Tombstone(
                            resource_id=record.ref.artifact_id,
                            agent_name=record.owner.agent_name,
                            operation_id=record.operation_id,
                            source_run_id=record.run_id,
                            deleted_at=now,
                        ),
                    )
                for lease in selection.leases:
                    # Preserve cumulative cloud admission even after removing its
                    # content-bearing execution ledger. Never refund a lifetime.
                    config = lease.backend_config
                    if lease.create_intent and config.get("backend") == "e2b":
                        budget, amount = (
                            config.get("budget_id"),
                            config.get("reservation_micro_usd"),
                        )
                        if not isinstance(budget, str) or type(amount) is not int or amount <= 0:
                            raise PersistenceUnavailable(
                                "Invalid cloud reservation prevents deletion"
                            )
                        key = cost_key(tx._secret, budget)
                        previous, _ = await driver.execute(
                            "SELECT value FROM metadata WHERE key=?", (key,)
                        )
                        total = retained_cost(previous[0][0]) if previous else 0
                        await driver.execute(
                            "INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) "
                            "DO UPDATE SET value=excluded.value",
                            (key, str(total + amount)),
                        )
                    await driver.execute(
                        "DELETE FROM sandbox_leases WHERE execution_key=?", (lease.execution_key,)
                    )
                for upload in selection.uploads:
                    await tombstone(
                        "upload",
                        digest("upload", upload.upload_id),
                        Tombstone(
                            resource_id=upload.upload_id,
                            deleted_at=now,
                        ),
                    )
                    await driver.execute(
                        "DELETE FROM artifact_uploads WHERE upload_id=?", (upload.upload_id,)
                    )
                for run in selection.runs:
                    if self._artifacts is not None:
                        await driver.execute(
                            "DELETE FROM artifact_grants WHERE run_id=?", (run.state.run_id,)
                        )
                for record in selection.cleared:
                    await driver.execute(
                        "DELETE FROM artifact_records WHERE artifact_id=?",
                        (record.ref.artifact_id,),
                    )
                for row in selection.rows:
                    row_key = row.key
                    await driver.execute(
                        "DELETE FROM records WHERE kind=? AND tenant_id=? AND user_id=? "
                        "AND namespace=? AND key=?",
                        (
                            row_key.kind,
                            row_key.tenant_id,
                            row_key.user_id,
                            row_key.namespace,
                            row_key.key,
                        ),
                    )
            # Only after durable commit. A cancelled/uncertain commit requires
            # reopening storage, which revalidates the extension before reuse.
            self._storage._retention_enabled = True
            return selection.plan

    async def preview_reclamation(
        self, scope: MemoryScope, *, expired_uploads: bool = False
    ) -> ReclamationPlan:
        scope = MemoryScope.from_json(scope.model_dump_json())
        if type(expired_uploads) is not bool:
            raise ConfigurationError("expired_uploads must be an explicit boolean")
        async with self._gate:
            self._require_open()
            async with self._storage.transaction() as tx:
                plan, _, _ = await self._select_reclamation(tx, scope, expired_uploads, tx._now())
                return plan

    async def preview_abandonment(
        self, scope: RunContext, run_id: str, *, reason: str
    ) -> AbandonmentPlan:
        scope = RunContext.from_json(scope.model_dump_json())
        async with self._gate:
            self._require_open()
            async with self._storage.transaction() as tx:
                plan, _, _, _ = await select_abandonment(self, tx, scope, run_id, reason)
                return plan

    async def abandon_run(self, plan: AbandonmentPlan) -> AbandonmentFact:
        plan = AbandonmentPlan.from_json(plan.model_dump_json())
        async with self._gate:
            self._require_open()
            return await apply_abandonment(self, plan)

    async def get_abandonment(self, scope: RunContext, run_id: str) -> AbandonmentFact:
        scope = RunContext.from_json(scope.model_dump_json())
        async with self._gate:
            self._require_open()
            async with self._storage.transaction() as tx:
                fact = await lookup_abandonment(tx, scope, run_id)
                if fact is None:
                    raise RecordNotFound("Abandonment is unavailable")
                return fact

    async def backup(self, destination: str | Path) -> BackupManifest:
        """Snapshot the locked pair into a new private, quarantined bundle."""
        from drov.storage._backup import backup

        async with self._gate:
            self._require_open()
            settled = await _settle(backup(self, Path(destination)))
            if settled.error is not None:
                raise settled.error
            if settled.cancelled:
                raise asyncio.CancelledError
            assert settled.value is not None
            return settled.value

    @staticmethod
    async def inspect_backup(source: str | Path) -> BackupManifest:
        from drov.storage._backup import inspect

        settled = await _settle(inspect(Path(source)))
        if settled.error is not None:
            raise settled.error
        if settled.cancelled:
            raise asyncio.CancelledError
        assert settled.value is not None
        return settled.value

    @staticmethod
    async def restore(source: str | Path, destination: str | Path) -> RestoreResult:
        """Restore into a new directory; never replace a live database or store."""
        from drov.storage._backup import restore

        settled = await _settle(restore(Path(source), Path(destination)))
        if settled.error is not None:
            raise settled.error
        if settled.cancelled:
            raise asyncio.CancelledError
        assert settled.value is not None
        return settled.value

    async def get_recovery_barrier(self) -> RecoveryBarrier | None:
        from drov.storage._barrier import get

        async with self._gate:
            self._require_open()
            async with self._storage.transaction() as tx:
                return await get(self._driver(tx))

    async def _select_reclamation(
        self, tx: Transaction, scope: MemoryScope, expire: bool, as_of: UtcTime
    ) -> tuple[ReclamationPlan, list[ReclamationEntry], list[UploadRecord]]:
        if self._artifacts is None:
            raise ConfigurationError("Reclamation requires the original artifact store")
        await self._no_pending_reclamation(tx)
        if as_of > tx._now():
            raise Conflict("Reclamation preview is from the future")
        snapshot = await self._snapshot(tx)
        rows = [Row(Key(*row[:5]), *row[5:]) for row in snapshot["records"]]
        runs = {row.key.key: _decode(RunRecord, row) for row in rows if row.key.kind == "run"}
        held_runs = {
            key
            for key, run in runs.items()
            if run.state.status in UNFINISHED
            or any(op.status in ("dispatched", "unknown") for op in run.state.operations)
        }
        held_ids = {ref.artifact_id for run in runs.values() for ref in run.artifact_inputs}
        held_stages: set[str] = set()
        for row in rows:
            if row.key.kind == "operation":
                item = _decode(RunItem, row)
                op = ToolOperation.from_json(canonical_json(item.data))
                if (op.run_id, op.operation_id, op.version, op.status) != (
                    item.run_id,
                    item.item_id,
                    item.version,
                    item.state,
                ) or op.run_id not in runs:
                    raise PersistenceUnavailable("Operation facts prevent byte reclamation")
                if any(
                    summary.operation_id == op.operation_id and summary != op.summary()
                    for summary in runs[op.run_id].state.operations
                ):
                    raise PersistenceUnavailable("Run and operation facts disagree")
                if op.status in ("dispatched", "unknown"):
                    held_runs.add(op.run_id)
        for row in snapshot.get("artifact_records", []):
            record = ArtifactRecord.from_json(row[5])
            await ArtifactTransaction(tx).locate(record.owner, row[0])
            held_stages.add(record.stage_id)
            held_ids.add(record.ref.artifact_id)
        held_ids.update(row[1] for row in snapshot.get("artifact_grants", []))
        for row in snapshot.get("sandbox_leases", []):
            lease = SandboxLease.from_json(row[8])
            LeaseTransaction._decode(
                (row[8], row[0], *row[1:4], row[6], row[7]), row[4], row[5], lease.owner
            )
            if lease.stage_id is not None:
                held_stages.add(lease.stage_id)
            held_ids.update(item.ref.artifact_id for item in lease.inputs)
            if lease.active or (lease.create_intent and not lease.destroyed):
                held_runs.add(lease.run_id)
        eligible_uploads = []
        for row in snapshot.get("artifact_uploads", []):
            upload = UploadRecord.from_json(row[3])
            if (upload.upload_id, upload.owner.tenant_id, upload.owner.user_id, upload.version) != (
                row[0],
                row[1],
                row[2],
                row[4],
            ):
                raise PersistenceUnavailable("Upload facts prevent byte reclamation")
            if (
                expire
                and upload.state != "consumed"
                and upload.expires_at <= as_of
                and (upload.owner.tenant_id, upload.owner.user_id, upload.target.agent_name)
                == (scope.tenant_id, scope.user_id, scope.agent_name)
            ):
                eligible_uploads.append(upload)
            else:
                held_stages.add(upload.stage_id)
                if upload.ref is not None:
                    held_ids.add(upload.ref.artifact_id)

        stages = await self._artifacts._pending()
        done = {item.stage_id for item in await entries(self._driver(tx))}
        candidates, protected = [], Counter[str]()
        owner_runs = {
            key
            for key, run in runs.items()
            if (run.scope.tenant_id, run.scope.user_id, run.agent_name)
            == (scope.tenant_id, scope.user_id, scope.agent_name)
        }
        for stage in stages:
            if stage.stage_id in done:
                raise PersistenceUnavailable("A reclaimed stage reappeared")
            if stage.owner != scope:
                continue
            source = runs.get(stage.run_id)
            if source is not None and (
                source.scope.tenant_id,
                source.scope.user_id,
                source.agent_name,
            ) != (scope.tenant_id, scope.user_id, scope.agent_name):
                raise PersistenceUnavailable("Stage source ownership differs")
            reason = None
            if stage.run_id in held_runs:
                reason = "unfinished_or_unknown"
            elif stage.stage_id in held_stages or any(
                file.ref.artifact_id in held_ids for file in stage.files
            ):
                reason = "retained_reference_or_lease"
            elif any(
                _mentions(
                    row,
                    ArtifactRecord(
                        ref=file.ref,
                        name=file.name,
                        owner=scope,
                        run_id=stage.run_id,
                        operation_id=stage.operation_id,
                        store_id=self._artifacts.store_id,
                        stage_id=stage.stage_id,
                    ),
                    owner_runs,
                )
                for file in stage.files
                for row in rows
            ):
                reason = "retained_content_reference"
            elif stage.purpose == "output" and source is None:
                try:
                    await tx.get_run(scope, stage.run_id)
                except ContentGone:
                    pass
                except RecordNotFound:
                    reason = "unaccounted_output"
                else:
                    raise PersistenceUnavailable("Stage source snapshot changed")
            if reason is not None:
                protected[reason] += 1
                continue
            candidates.append(
                await capture(
                    self._artifacts,
                    stage,
                    scope_digest=tx._digest(scope.model_dump()),
                    stage_digest=tx._digest(stage.model_dump()),
                    now=as_of,
                )
            )
        candidates.sort(key=lambda item: item.stage_id)
        selected_ids = {item.stage_id for item in candidates}
        uploads = [item for item in eligible_uploads if item.stage_id in selected_ids]
        plan = ReclamationPlan(
            scope=scope,
            expired_uploads=expire,
            as_of=as_of,
            fingerprint=tx._digest(
                {
                    "scope": scope.model_dump(),
                    "expired_uploads": expire,
                    "as_of": as_of.isoformat(),
                    "snapshot": snapshot,
                    "stages": [
                        stage.model_dump()
                        for stage in sorted(stages, key=lambda item: item.stage_id)
                    ],
                    "candidates": [item.model_dump(mode="json") for item in candidates],
                }
            ),
            stages=len(candidates),
            bytes_eligible=sum(item.bytes_reclaimed for item in candidates),
            capacity_eligible=sum(item.capacity_released for item in candidates),
            uploads=len(uploads),
            protected=dict(protected),
        )
        return plan, candidates, uploads

    async def reclaim(self, plan: ReclamationPlan) -> ReclamationResult:
        plan = ReclamationPlan.from_json(plan.model_dump_json())
        async with self._gate:
            self._require_open()
            async with self._storage.transaction() as tx:
                current, selected, uploads = await self._select_reclamation(
                    tx, plan.scope, plan.expired_uploads, plan.as_of
                )
                if current != plan:
                    raise Conflict("Reclamation preview changed; obtain a new preview")
                if not selected:
                    return ReclamationResult()
                driver = self._driver(tx)
                enabled, _ = await driver.execute(
                    "SELECT value FROM metadata WHERE key='reclamation_schema'"
                )
                if not enabled:
                    for statement in RECLAMATION_DDL:
                        await driver.execute(statement)
                    await driver.execute("INSERT INTO metadata VALUES ('reclamation_schema','1')")
                for entry in selected:
                    await driver.execute(
                        "INSERT INTO artifact_reclamation VALUES (?,?,?)",
                        (entry.stage_id, entry.model_dump_json(), "pending"),
                    )
                for upload in uploads:
                    # Expired, unconsumed receipts cannot become inputs after intent.
                    # No filename, content, or expired upload identity is retained.
                    await driver.execute(
                        "DELETE FROM artifact_uploads WHERE upload_id=?", (upload.upload_id,)
                    )
            return await self._resume_reclamation()

    async def resume_reclamation(self) -> ReclamationResult:
        async with self._gate:
            self._require_open()
            return await self._resume_reclamation()

    async def _resume_reclamation(self) -> ReclamationResult:
        if self._artifacts is None:
            raise ConfigurationError("Reclamation requires the original artifact store")
        async with self._storage.transaction() as tx:
            pending = [item for item in await entries(self._driver(tx)) if item.state == "pending"]
            # Once intent commits, ordinary storage open is barred. Revalidate
            # references anyway: manual SQLite edits cannot silently release bytes.
            snapshot = await self._snapshot(tx)
            stored_rows = [Row(Key(*raw[:5]), *raw[5:]) for raw in snapshot["records"]]
            stored_runs = {
                row.key.key: _decode(RunRecord, row) for row in stored_rows if row.key.kind == "run"
            }
            for entry in pending:
                for stored in stored_rows:
                    scoped: MemoryScope | None = None
                    if stored.key.kind == "run":
                        run = _decode(RunRecord, stored)
                        scoped = MemoryScope(
                            tenant_id=run.scope.tenant_id,
                            user_id=run.scope.user_id,
                            agent_name=run.agent_name,
                        )
                        if (
                            run.state.run_id == entry.run_id
                            and (
                                run.state.status in UNFINISHED
                                or any(
                                    item.status in ("unknown", "dispatched")
                                    for item in run.state.operations
                                )
                            )
                        ) or any(
                            ref.artifact_id == file.name
                            for ref in run.artifact_inputs
                            for file in entry.files
                        ):
                            raise PersistenceUnavailable("A pending reclamation acquired a Run")
                    elif stored.key.kind == "operation" and stored.key.namespace == entry.run_id:
                        item = _decode(RunItem, stored)
                        op = ToolOperation.from_json(canonical_json(item.data))
                        if op.status in ("unknown", "dispatched"):
                            raise PersistenceUnavailable(
                                "A pending reclamation acquired an unknown operation"
                            )
                    if stored.key.kind == "memory":
                        scoped = MemoryScope(
                            tenant_id=stored.key.tenant_id,
                            user_id=stored.key.user_id,
                            agent_name=stored.key.namespace,
                        )
                    elif stored.key.kind not in ("session", "start_key", "run"):
                        source = stored_runs.get(stored.key.namespace)
                        if source is not None:
                            scoped = MemoryScope(
                                tenant_id=stored.key.tenant_id,
                                user_id=stored.key.user_id,
                                agent_name=source.agent_name,
                            )
                    if scoped is not None and tx._digest(scoped.model_dump()) == entry.scope_digest:
                        if any(
                            file.name != "stage.json" and file.name in stored.payload
                            for file in entry.files
                        ):
                            raise PersistenceUnavailable(
                                "A pending reclamation acquired a content reference"
                            )
                for row in snapshot.get("artifact_records", []):
                    record = ArtifactRecord.from_json(row[5])
                    if record.stage_id == entry.stage_id or any(
                        file.name == record.ref.artifact_id for file in entry.files
                    ):
                        raise PersistenceUnavailable("A pending reclamation acquired a reference")
                for row in snapshot.get("sandbox_leases", []):
                    lease = SandboxLease.from_json(row[8])
                    if lease.stage_id == entry.stage_id or any(
                        item.ref.artifact_id == file.name
                        for item in lease.inputs
                        for file in entry.files
                    ):
                        raise PersistenceUnavailable("A pending reclamation acquired a lease")
                if any(
                    UploadRecord.from_json(row[3]).stage_id == entry.stage_id
                    for row in snapshot.get("artifact_uploads", [])
                ):
                    raise PersistenceUnavailable("A pending reclamation acquired an upload")
        self._artifacts._reclaiming = frozenset(item.stage_id for item in pending)
        result = ReclamationResult()
        for entry in pending:
            await erase(self._artifacts, entry)
            async with self._storage.transaction() as tx:
                completed = entry.model_copy(update={"state": "done", "completed_at": tx._now()})
                _, count = await self._driver(tx).execute(
                    "UPDATE artifact_reclamation SET payload=?,state='done' "
                    "WHERE stage_id=? AND state='pending'",
                    (completed.model_dump_json(), entry.stage_id),
                )
                if count != 1:
                    raise PersistenceUnavailable("Reclamation completion changed")
            result = ReclamationResult(
                completed_stages=result.completed_stages + 1,
                bytes_reclaimed=result.bytes_reclaimed + entry.bytes_reclaimed,
                capacity_released=result.capacity_released + entry.capacity_released,
            )
        self._artifacts._reclaiming = frozenset()
        return result


def main() -> int:
    """Local OS file access is the administrator capability; no remote endpoint."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "preview-session",
            "delete-session",
            "preview-reclamation",
            "reclaim",
            "resume-reclamation",
            "preview-abandonment",
            "abandon-run",
            "get-abandonment",
            "backup",
            "restore",
            "inspect-backup",
            "get-recovery-barrier",
        ),
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--source", type=Path, help="Complete backup bundle for restore")
    parser.add_argument("--destination", type=Path, help="New private bundle directory")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--tenant")
    parser.add_argument("--user")
    parser.add_argument("--session")
    parser.add_argument("--agent")
    parser.add_argument("--run")
    parser.add_argument(
        "--reason", help="Bounded administrator reason retained after content deletion"
    )
    parser.add_argument("--expired-uploads", action="store_true")
    parser.add_argument(
        "--plan-file", type=Path, help="Exact JSON from the matching maintenance preview"
    )
    parser.add_argument("--include-memory", action="store_true")
    parser.add_argument("--fingerprint", help="Exact fingerprint from the current preview")
    args = parser.parse_args()
    if args.action not in ("restore", "inspect-backup") and args.database is None:
        parser.error("this action requires --database")
    if args.action in ("backup", "restore") and args.destination is None:
        parser.error("backup and restore require a new --destination")
    if args.action in ("restore", "inspect-backup") and args.source is None:
        parser.error("restore and inspect-backup require --source")
    if args.action in (
        "preview-session",
        "delete-session",
        "preview-reclamation",
        "preview-abandonment",
        "get-abandonment",
    ) and (args.tenant is None or args.user is None):
        parser.error("preview and session actions require --tenant and --user")
    if args.action in ("preview-session", "delete-session") and args.session is None:
        parser.error("session actions require --session")
    if args.action == "preview-reclamation" and args.agent is None:
        parser.error("preview-reclamation requires --agent")
    if args.action == "reclaim" and args.plan_file is None:
        parser.error("reclaim requires --plan-file from preview-reclamation")
    if args.action == "abandon-run" and args.plan_file is None:
        parser.error("abandon-run requires --plan-file from preview-abandonment")
    if args.action in ("preview-abandonment", "get-abandonment") and args.run is None:
        parser.error("abandonment preview/lookup requires --run")
    if args.action == "preview-abandonment" and args.reason is None:
        parser.error("preview-abandonment requires an explicit --reason")
    if args.action == "delete-session" and args.fingerprint is None:
        parser.error("delete-session requires --fingerprint from a preview")
    if args.fingerprint is not None and (
        len(args.fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in args.fingerprint)
    ):
        parser.error("--fingerprint must be a lowercase SHA-256 digest")

    async def run() -> None:
        if args.action == "inspect-backup":
            print((await SQLiteMaintenance.inspect_backup(args.source)).model_dump_json(indent=2))
            return
        if args.action == "restore":
            print(
                (await SQLiteMaintenance.restore(args.source, args.destination)).model_dump_json(
                    indent=2
                )
            )
            return
        files = LocalArtifacts(directory=args.artifacts) if args.artifacts is not None else None
        async with SQLiteMaintenance(args.database, artifacts=files) as maintenance:
            if args.action == "backup":
                print((await maintenance.backup(args.destination)).model_dump_json(indent=2))
                return
            if args.action == "get-recovery-barrier":
                barrier = await maintenance.get_recovery_barrier()
                print(barrier.model_dump_json(indent=2) if barrier else "null")
                return
            if args.action == "preview-abandonment":
                abandoned = await maintenance.preview_abandonment(
                    RunContext(tenant_id=args.tenant, user_id=args.user),
                    args.run,
                    reason=args.reason,
                )
                print(abandoned.model_dump_json(indent=2))
                return
            if args.action == "get-abandonment":
                fact = await maintenance.get_abandonment(
                    RunContext(tenant_id=args.tenant, user_id=args.user), args.run
                )
                print(fact.model_dump_json(indent=2))
                return
            if args.action == "preview-reclamation":
                preview = await maintenance.preview_reclamation(
                    MemoryScope(tenant_id=args.tenant, user_id=args.user, agent_name=args.agent),
                    expired_uploads=args.expired_uploads,
                )
                print(preview.model_dump_json(indent=2))
                return
            if args.action in ("reclaim", "abandon-run"):
                with args.plan_file.open("rb") as source:
                    raw = source.read(65537)
                if len(raw) > 65536:
                    raise ConfigurationError("Maintenance plan exceeds its bound")
                if args.action == "abandon-run":
                    fact = await maintenance.abandon_run(AbandonmentPlan.from_json(raw))
                    print(fact.model_dump_json(indent=2))
                    return
                result = await maintenance.reclaim(ReclamationPlan.from_json(raw))
                print(result.model_dump_json(indent=2))
                return
            if args.action == "resume-reclamation":
                print((await maintenance.resume_reclamation()).model_dump_json(indent=2))
                return
            scope = SessionScope(tenant_id=args.tenant, user_id=args.user, session_id=args.session)
            plan = await maintenance.preview_session(scope, include_memory=args.include_memory)
            if args.action == "delete-session":
                if not hmac.compare_digest(plan.fingerprint, args.fingerprint):
                    raise Conflict("Maintenance preview changed; obtain a new preview")
                await maintenance.delete_session(plan)
            print(plan.model_dump_json(indent=2))

    try:
        asyncio.run(run())
    except (DrovError, ValueError, OSError) as exc:
        # Errors never echo arbitrary stored JSON, paths, or command-line content.
        print(canonical_json({"error": getattr(exc, "code", "configuration_error")}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
