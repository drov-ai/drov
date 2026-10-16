"""Explicit compatible v1 record backfill, not an invented released v0."""

import json
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from drov._json import canonical_json
from drov.artifacts.types import Sha256
from drov.errors import Conflict, PersistenceUnavailable
from drov.storage._backup import validate
from drov.storage._driver import Key, Row
from drov.storage.records import RunItem, RunRecord
from drov.storage.transaction import UNFINISHED, Transaction, _decode
from drov.tools.operations import ToolOperation
from drov.types import PositiveInt, UtcTime, WireModel

if TYPE_CHECKING:
    from pathlib import Path

    from drov.storage.maintenance import SQLiteMaintenance
    from drov.storage.sqlite import SQLiteDriver

MIGRATION_DDL = (
    """CREATE TABLE migration_receipts (
        identity TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL
    )""",
)
PROFILE = Literal["v1-compatible-defaults"]


class MigrationPlan(WireModel):
    profile: PROFILE = "v1-compatible-defaults"
    source_schema: Literal[1] = 1
    target_schema: Literal[1] = 1
    record_schema: Literal[1] = 1
    fingerprint: Sha256
    changes: dict[Literal["run", "operation"], PositiveInt]
    unfinished_runs: int = Field(ge=0)
    unknown_operations: int = Field(ge=0)


class MigrationReceipt(WireModel):
    schema_version: Literal[1] = 1
    profile: PROFILE = "v1-compatible-defaults"
    source_schema: Literal[1] = 1
    target_schema: Literal[1] = 1
    record_schema: Literal[1] = 1
    plan_digest: Sha256
    backup_snapshot_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    changes: dict[Literal["run", "operation"], PositiveInt]
    migrated_at: UtcTime


async def lookup(driver: "SQLiteDriver", identity: str) -> MigrationReceipt | None:
    enabled, _ = await driver.execute("SELECT value FROM metadata WHERE key='migration_schema'")
    if not enabled:
        return None
    rows, _ = await driver.execute(
        "SELECT payload FROM migration_receipts WHERE identity=?", (identity,)
    )
    if not rows:
        return None
    receipt = MigrationReceipt.from_json(rows[0][0])
    if receipt.plan_digest != identity:
        raise PersistenceUnavailable("Migration receipt identity differs")
    return receipt


async def select(
    maintenance: "SQLiteMaintenance", tx: Transaction
) -> tuple[MigrationPlan, list[tuple[Row, str]]]:
    await maintenance._no_pending_reclamation(tx)
    snapshot = await maintenance._snapshot(tx)
    changed = []
    counts: dict[Literal["run", "operation"], int] = {}
    unfinished = unknown = 0
    for raw in snapshot["records"]:
        row = Row(Key(*raw[:5]), *raw[5:])
        if row.key.kind == "run":
            run = _decode(RunRecord, row)
            unfinished += run.state.status in UNFINISHED
            value = json.loads(row.payload)
            if "artifact_inputs" not in value or "artifact_binding" not in value:
                payload = run.model_dump_json()
                # Current decoding already supplies exactly these compatible defaults.
                if RunRecord.from_json(payload) != run:
                    raise PersistenceUnavailable("Migration changed run semantics")
                changed.append((row, payload))
                counts["run"] = counts.get("run", 0) + 1
        elif row.key.kind == "operation":
            item = _decode(RunItem, row)
            op = ToolOperation.from_json(canonical_json(item.data))
            if (op.run_id, op.operation_id, op.version, op.status) != (
                item.run_id,
                item.item_id,
                item.version,
                item.state,
            ):
                raise PersistenceUnavailable("Migration operation index differs")
            unknown += op.status in ("dispatched", "unknown")
            if any(
                name not in item.data
                for name in ("effect_resolution", "evidence", "memory_sources")
            ):
                migrated = item.model_copy(update={"data": op.model_dump(mode="json")})
                if ToolOperation.from_json(canonical_json(migrated.data)) != op:
                    raise PersistenceUnavailable("Migration changed operation semantics")
                changed.append((row, migrated.model_dump_json()))
                counts["operation"] = counts.get("operation", 0) + 1
    stages = await maintenance._artifacts._pending() if maintenance._artifacts else []
    plan = MigrationPlan(
        changes=counts,
        unfinished_runs=unfinished,
        unknown_operations=unknown,
        fingerprint=tx._digest(
            {
                "profile": "v1-compatible-defaults",
                "snapshot": snapshot,
                "stages": [
                    s.model_dump(mode="json") for s in sorted(stages, key=lambda s: s.stage_id)
                ],
            }
        ),
    )
    return plan, changed


async def apply(
    maintenance: "SQLiteMaintenance", plan: MigrationPlan, destination: "Path"
) -> MigrationReceipt:
    async with maintenance._storage.transaction() as tx:
        await maintenance._no_pending_reclamation(tx)
        prior = await lookup(maintenance._driver(tx), tx._digest(plan.model_dump(mode="json")))
        if prior is not None:
            return prior
        current, changed = await select(maintenance, tx)
        if current != plan:
            raise Conflict("Migration preview changed; obtain a new preview")
        if not changed:
            raise Conflict("No compatible historical records need migration")
    await validate(maintenance)
    # The source remains locked for the entire backup and migration. No source
    # extension or payload is written until a complete quarantined backup exists.
    from drov.storage._backup import backup

    snapshot = await backup(maintenance, destination)
    async with maintenance._storage.transaction() as tx:
        current, changed = await select(maintenance, tx)
        if current != plan:
            raise Conflict("Migration source changed during backup")
        driver = maintenance._driver(tx)
        for row, payload in changed:
            key = row.key
            _, count = await driver.execute(
                "UPDATE records SET payload=? WHERE kind=? AND tenant_id=? AND user_id=? "
                "AND namespace=? AND key=? AND version=? AND payload=?",
                (
                    payload,
                    key.kind,
                    key.tenant_id,
                    key.user_id,
                    key.namespace,
                    key.key,
                    row.version,
                    row.payload,
                ),
            )
            if count != 1:
                raise Conflict("Migration lost its original record")
        enabled, _ = await driver.execute("SELECT value FROM metadata WHERE key='migration_schema'")
        if not enabled:
            for statement in MIGRATION_DDL:
                await driver.execute(statement)
            await driver.execute("INSERT INTO metadata VALUES ('migration_schema','1')")
        receipt = MigrationReceipt(
            plan_digest=tx._digest(plan.model_dump(mode="json")),
            backup_snapshot_id=snapshot.snapshot_id,
            changes=plan.changes,
            migrated_at=tx._now(),
        )
        await driver.execute(
            "INSERT INTO migration_receipts VALUES (?,?)",
            (receipt.plan_digest, receipt.model_dump_json()),
        )
        integrity, _ = await driver.execute("PRAGMA integrity_check")
        foreign, _ = await driver.execute("PRAGMA foreign_key_check")
        if integrity != [("ok",)] or foreign:
            raise PersistenceUnavailable("Migrated database integrity check failed")
        return receipt
