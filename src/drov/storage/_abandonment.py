"""Offline terminal abandonment; retained uncertainty is never verification."""

from typing import TYPE_CHECKING, Literal, Self, cast

from pydantic import Field, field_validator, model_validator

from drov._json import canonical_json
from drov.artifacts.types import Sha256
from drov.errors import Conflict, ContentGone, PersistenceUnavailable
from drov.storage._driver import Key, Row
from drov.storage._retention import identity
from drov.storage.records import EventDraft, RunItem, RunRecord
from drov.storage.transaction import UNFINISHED, Transaction, _decode
from drov.tools.operations import ToolOperation
from drov.types import (
    Failed,
    Identifier,
    OperationSummary,
    PositiveInt,
    RunContext,
    RunError,
    ScopedIdentifier,
    UtcTime,
    WireModel,
)

if TYPE_CHECKING:
    from drov.storage.maintenance import SQLiteMaintenance

ABANDONMENT_DDL = (
    """CREATE TABLE abandonment_facts (
        identity TEXT PRIMARY KEY NOT NULL, payload TEXT NOT NULL
    )""",
)


class AbandonmentPlan(WireModel):
    scope: RunContext
    run_id: Identifier
    reason: str = Field(min_length=1, max_length=512)
    fingerprint: Sha256
    status: Literal["accepted", "running", "suspended"]
    state_version: PositiveInt
    unknown_operations: int = Field(ge=0)
    retained_leases: int = Field(ge=0)

    @field_validator("reason")
    @classmethod
    def reason_is_printable(cls, value: str) -> str:
        if not value.strip() or not value.isprintable():
            raise ValueError("Abandonment requires a nonblank printable reason")
        return value


class AbandonmentFact(WireModel):
    schema_version: Literal[1] = 1
    status: Literal["failed"] = "failed"
    error_code: Literal["data_abandoned"] = "data_abandoned"
    recoverable: bool = False
    run_id: Identifier
    session_id: Identifier
    agent_name: ScopedIdentifier
    state_version: PositiveInt
    original_status: Literal["accepted", "running", "suspended"]
    reason: str = Field(min_length=1, max_length=512)
    unknown_operations: list[OperationSummary]
    retained_leases: int = Field(ge=0)
    abandoned_at: UtcTime
    plan_digest: Sha256

    @model_validator(mode="after")
    def consistent(self) -> Self:
        AbandonmentPlan.reason_is_printable(self.reason)
        if (
            self.recoverable is not False
            or self.state_version < 2
            or any(op.status not in ("dispatched", "unknown") for op in self.unknown_operations)
            or len({op.operation_id for op in self.unknown_operations})
            != len(self.unknown_operations)
        ):
            raise ValueError("Abandonment facts are inconsistent")
        return self


async def lookup(tx: Transaction, scope: RunContext, run_id: str) -> AbandonmentFact | None:
    from drov.storage.sqlite import SQLiteDriver

    assert isinstance(tx._driver, SQLiteDriver)
    enabled, _ = await tx._driver.execute(
        "SELECT value FROM metadata WHERE key='abandonment_schema'"
    )
    if not enabled:
        return None
    key = identity(tx._secret, "abandoned", scope.tenant_id, scope.user_id, run_id)
    rows, _ = await tx._driver.execute(
        "SELECT payload FROM abandonment_facts WHERE identity=?", (key,)
    )
    if not rows:
        return None
    try:
        fact = AbandonmentFact.from_json(rows[0][0])
        if fact.run_id != run_id:
            raise ValueError("Abandonment identity differs")
        return fact
    except (ValueError, TypeError) as exc:
        raise PersistenceUnavailable("Abandonment fact is invalid") from exc


def matches(fact: AbandonmentFact, run: RunRecord, operations: list[ToolOperation]) -> bool:
    outcome = run.state.outcome
    uncertain = [op.summary() for op in operations if op.status in ("unknown", "dispatched")]
    return (
        run.state.status == "failed"
        and not run.state.recoverable
        and isinstance(outcome, Failed)
        and outcome.error.code == "data_abandoned"
        and fact.run_id == run.state.run_id
        and fact.state_version == run.state.state_version
        and fact.session_id == run.scope.session_id
        and fact.agent_name == run.agent_name
        and sorted(op.model_dump_json() for op in fact.unknown_operations)
        == sorted(op.model_dump_json() for op in uncertain)
    )


async def select(
    maintenance: "SQLiteMaintenance", tx: Transaction, scope: RunContext, run_id: str, reason: str
) -> tuple[AbandonmentPlan, RunRecord, list[tuple[Row, ToolOperation]], list[Row]]:
    await maintenance._no_pending_reclamation(tx)
    snapshot = await maintenance._snapshot(tx)
    run = await tx.get_run(scope, run_id)
    if run.state.status not in UNFINISHED:
        raise Conflict("Only unfinished runs can be abandoned")
    rows = [Row(Key(*raw[:5]), *raw[5:]) for raw in snapshot["records"]]
    selected = [
        row
        for row in rows
        if (row.key.tenant_id, row.key.user_id, row.key.namespace)
        == (scope.tenant_id, scope.user_id, run_id)
    ]
    operations = []
    for row in selected:
        if row.key.kind != "operation":
            continue
        item = _decode(RunItem, row)
        op = ToolOperation.from_json(canonical_json(item.data))
        if (op.run_id, op.operation_id, op.version, op.status) != (
            item.run_id,
            item.item_id,
            item.version,
            item.state,
        ):
            raise PersistenceUnavailable("Abandonment operation facts differ")
        operations.append((row, op))
    by_id = {op.operation_id: op for _, op in operations}
    if any(
        by_id.get(summary.operation_id) is None or by_id[summary.operation_id].summary() != summary
        for summary in run.state.operations
    ):
        raise PersistenceUnavailable("Run and operation facts differ")
    leases = [row for row in snapshot.get("sandbox_leases", []) if row[4] == run_id]
    from drov.sandbox._leases import LeaseTransaction, SandboxLease

    for row in leases:
        lease = SandboxLease.from_json(row[8])
        LeaseTransaction._decode(
            (row[8], row[0], *row[1:4], row[6], row[7]), row[4], row[5], lease.owner
        )
        if (lease.owner.tenant_id, lease.owner.user_id, lease.owner.agent_name) != (
            scope.tenant_id,
            scope.user_id,
            run.agent_name,
        ):
            raise PersistenceUnavailable("Abandonment lease ownership differs")
    stages = await maintenance._artifacts._pending() if maintenance._artifacts is not None else []
    plan = AbandonmentPlan(
        scope=scope,
        run_id=run_id,
        reason=reason,
        status=cast(Literal["accepted", "running", "suspended"], run.state.status),
        state_version=run.state.state_version,
        unknown_operations=sum(op.status in ("unknown", "dispatched") for _, op in operations),
        retained_leases=len(leases),
        fingerprint=tx._digest(
            {
                "scope": scope.model_dump(),
                "run_id": run_id,
                "reason": reason,
                "snapshot": snapshot,
                "stages": [
                    item.model_dump() for item in sorted(stages, key=lambda item: item.stage_id)
                ],
            }
        ),
    )
    return plan, run, operations, selected


async def apply(maintenance: "SQLiteMaintenance", plan: AbandonmentPlan) -> AbandonmentFact:
    async with maintenance._storage.transaction() as tx:
        await maintenance._no_pending_reclamation(tx)
        prior = await lookup(tx, plan.scope, plan.run_id)
        if prior is not None:
            if prior.plan_digest != tx._digest(plan.model_dump(mode="json")):
                raise Conflict("Abandonment already has a different immutable receipt")
            try:
                current = await tx.get_run(plan.scope, plan.run_id)
            except ContentGone:
                return prior
            if (
                current.state.status != "failed"
                or current.state.state_version != prior.state_version
                or current.state.recoverable
                or not isinstance(current.state.outcome, Failed)
                or current.state.outcome.error.code != "data_abandoned"
                or current.scope.session_id != prior.session_id
                or current.agent_name != prior.agent_name
            ):
                raise PersistenceUnavailable("Abandonment receipt differs from current state")
            return prior
        current_plan, run, operations, rows = await select(
            maintenance, tx, plan.scope, plan.run_id, plan.reason
        )
        if current_plan != plan:
            raise Conflict("Abandonment preview changed; obtain a new preview")
        driver = maintenance._driver(tx)
        enabled, _ = await driver.execute(
            "SELECT value FROM metadata WHERE key='abandonment_schema'"
        )
        if not enabled:
            for statement in ABANDONMENT_DDL:
                await driver.execute(statement)
            await driver.execute("INSERT INTO metadata VALUES ('abandonment_schema','1')")
        summaries, events = [], []
        order = [item.operation_id for item in run.state.operations]
        order += sorted(op.operation_id for _, op in operations if op.operation_id not in order)
        by_id = {op.operation_id: (row, op) for row, op in operations}
        for op_id in order:
            row, op = by_id[op_id]
            if op.status == "prepared":
                updated = op.model_copy(
                    update={
                        "version": op.version + 1,
                        "status": "not_executed",
                        "error_code": "data_abandoned_before_dispatch",
                    }
                )
                item = _decode(RunItem, row)
                await tx.put_item(
                    plan.scope,
                    item.model_copy(
                        update={
                            "version": item.version + 1,
                            "state": updated.status,
                            "data": updated.model_dump(mode="json"),
                        }
                    ),
                    expected_version=item.version,
                )
                op = updated
                events.append(EventDraft(type="tool.not_executed", data={"operation_id": op_id}))
            summaries.append(op.summary())
        for row in rows:
            if row.key.kind == "interaction" and row.state in ("pending", "active"):
                item = _decode(RunItem, row)
                await tx.put_item(
                    plan.scope,
                    item.model_copy(
                        update={
                            "version": item.version + 1,
                            "state": "abandoned",
                        }
                    ),
                    expected_version=item.version,
                )
        version = run.state.state_version + 1
        usage = run.state.usage.model_copy(update={"complete": False})
        outcome = Failed(
            run_id=plan.run_id,
            state_version=version,
            usage=usage,
            operations=summaries,
            error=RunError(
                code="data_abandoned",
                message="Run explicitly abandoned by administrator",
                stage="maintenance",
                retryable=False,
            ),
        )
        checkpoint = await tx.get_checkpoint(plan.scope, plan.run_id)
        await tx.save_step(
            plan.scope,
            expected_version=version - 1,
            state=run.state.model_copy(
                update={
                    "state_version": version,
                    "status": "failed",
                    "reason": None,
                    "pending": None,
                    "outcome": outcome,
                    "operations": summaries,
                    "recoverable": False,
                    "usage": usage,
                }
            ),
            checkpoint=checkpoint.model_copy(
                update={"state_version": version, "cursor": "data_abandoned"}
            ),
            events=[
                *events,
                EventDraft(
                    type="run.failed",
                    data={
                        "error_code": "data_abandoned",
                        "usage": usage.model_dump(mode="json"),
                    },
                ),
            ],
        )
        fact = AbandonmentFact(
            run_id=plan.run_id,
            session_id=run.scope.session_id,
            agent_name=run.agent_name,
            state_version=version,
            original_status=plan.status,
            reason=plan.reason,
            unknown_operations=[op for op in summaries if op.status in ("dispatched", "unknown")],
            retained_leases=plan.retained_leases,
            abandoned_at=tx._now(),
            plan_digest=tx._digest(plan.model_dump(mode="json")),
        )
        await driver.execute(
            "INSERT INTO abandonment_facts VALUES (?,?)",
            (
                identity(
                    tx._secret, "abandoned", plan.scope.tenant_id, plan.scope.user_id, plan.run_id
                ),
                fact.model_dump_json(),
            ),
        )
        return fact
