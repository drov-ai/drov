"""Exclusive startup scan and explicit continuation from the last committed boundary."""

from typing import TYPE_CHECKING, Any, cast

from drov._json import StrictJson, canonical_json
from drov.errors import Conflict, PersistenceUnavailable
from drov.models.protocol import ModelRequest, ModelResponse
from drov.runtime.reconciliation import ambiguous, operation_in, put_operation
from drov.storage.records import EventDraft, RunRecord
from drov.storage.transaction import Transaction
from drov.types import RunContext, RunOutcome, RunSnapshot

if TYPE_CHECKING:
    from drov.runtime.runs import RunController


class RecoveryController:
    def __init__(self, runs: "RunController") -> None:
        self.runs = runs

    async def normalize_in(self, tx: Transaction, record: RunRecord) -> None:
        if ambiguous(record):
            await self.runs.reconciliation.suspend_in(tx, record)
            return
        checkpoint = await tx.get_checkpoint(record.scope, record.state.run_id)
        version = record.state.state_version + 1
        usage = record.state.usage
        if checkpoint.data.get("pending_model_call") is not None and checkpoint.data.get(
            "pending_model_attempt_count", 0
        ):
            usage = usage.model_copy(update={"complete": False})
        await tx.save_step(
            record.scope,
            expected_version=version - 1,
            state=record.state.model_copy(
                update={
                    "status": "suspended",
                    "reason": "recovery",
                    "pending": None,
                    "outcome": None,
                    "recoverable": True,
                    "state_version": version,
                    "usage": usage,
                }
            ),
            checkpoint=checkpoint.model_copy(update={"state_version": version}),
            events=[EventDraft(type="recovery.required")],
        )

    async def scan(self) -> None:
        before = None
        while True:
            async with self.runs.storage.transaction() as tx:
                page = await tx._unfinished_runs(before=before)
                for record in page:
                    if record.state.status in ("accepted", "running"):
                        await self.normalize_in(tx, record)
                    else:
                        await self.runs.interactions.expire_in(tx, record)
            if len(page) < 100:
                break
            before = (page[-1].created_at, page[-1].state.run_id)

    async def cursors_in(self, tx: Transaction, record: RunRecord) -> dict[str, StrictJson]:
        checkpoint = await tx.get_checkpoint(record.scope, record.state.run_id)
        data = dict(checkpoint.data)
        engine = data.get("engine_schema")
        ids = data.get("response_ids", [])
        if (engine is not None and (type(engine) is not int or engine != 1)) or (
            engine is None and ids
        ):
            raise Conflict("Saved engine schema is incompatible")
        if not isinstance(ids, list) or any(not isinstance(id, str) for id in ids):
            raise PersistenceUnavailable("Invalid saved model response index")
        latest_input = None
        attempts = 0
        sequence = 0
        while True:
            events = await tx.events(record.scope, record.state.run_id, after=sequence, limit=500)
            for event in events:
                if event.type == "context.prepared" and event.data.get("error_code") is None:
                    latest_input = event.data.get("model_call_id")
                    attempts = 0
                if (
                    event.type == "model.attempt_started"
                    and event.data.get("model_call_id") == latest_input
                ):
                    attempts += 1
            if len(events) < 500:
                break
            sequence = events[-1].seq
        if latest_input is not None and latest_input not in ids:
            if not isinstance(latest_input, str):
                raise PersistenceUnavailable("Invalid interrupted model input")
            item = await tx.get_item(record.scope, record.state.run_id, "model_input", latest_input)
            if (
                item.state != "prepared"
                or type(item.data.get("schema_version")) is not int
                or item.data.get("schema_version") != 1
            ):
                raise Conflict("Saved model input is incompatible")
            ModelRequest.from_json(canonical_json(item.data["request"]))
            await self.runs.context_record(tx, record.scope, record.state.run_id, latest_input)
            data["pending_model_call"] = latest_input
            data["pending_model_attempt_count"] = attempts
        elif ids:
            last = cast(str, ids[-1])
            saved = await tx.get_item(record.scope, record.state.run_id, "model_response", last)
            if (
                type(saved.data.get("schema_version")) is not int
                or saved.data.get("schema_version") != 1
            ):
                raise Conflict("Saved model response is incompatible")
            response = ModelResponse.model_validate(saved.data["response"])
            plan = saved.data.get("operation_ids")
            if (
                not isinstance(plan, list)
                or len(plan) != len(response.tool_calls)
                or any(not isinstance(id, str) for id in plan)
            ):
                raise PersistenceUnavailable("Invalid original operation plan")
            data["pending_model_call"] = None
            data["pending_plan"] = last if response.tool_calls else None
            data["pending_output"] = last if not response.tool_calls else None
        # A stopped read may be reported as interrupted; writes keep their unknown effect.
        for summary in record.state.operations:
            if summary.status == "dispatched" and summary.effect == "read":
                operation = await operation_in(tx, record, summary.operation_id)
                await put_operation(
                    tx,
                    record,
                    operation.model_copy(
                        update={
                            "version": operation.version + 1,
                            "status": "failed_known",
                            "error_code": "read_interrupted",
                        }
                    ),
                )
        return data

    async def recover(
        self, run_id: str, *, context: RunContext, wait: bool
    ) -> RunOutcome[Any] | RunSnapshot:
        if type(wait) is not bool:
            raise ValueError("wait must be a bool")
        runs = self.runs
        record = await runs.visible(run_id, context, wait)
        await runs.authorize(context, runs.resource(record), ("recover",))
        owner = runs.resource(record).owner

        async def apply() -> None:
            slot = None
            try:
                if runs.runtime._sandboxes is not None:
                    latest = await runs.storage.get_run(owner, run_id)
                    runs.runtime.validate_record(latest)
                    local = runs.works.get(run_id)
                    if local is None or local.task.done():
                        await runs.runtime._sandboxes.recover(latest)
                async with runs.storage.transaction() as tx:
                    current = await tx.get_run(owner, run_id)
                    await runs.authorize(
                        context,
                        runs.resource(current),
                        ("read", "view_content", "recover") if wait else ("read", "recover"),
                    )
                    if current.state.status in ("completed", "failed", "cancelled"):
                        raise Conflict("Terminal run cannot be recovered")
                    runs.runtime.validate_record(current)
                    if current.state.status == "suspended" and current.state.reason != "recovery":
                        return  # Only respond/resume may consume a human request.
                    work = runs.works.get(run_id)
                    if work is not None and not work.task.done():
                        raise Conflict("Run already has a local execution owner")
                    if ambiguous(current):
                        await runs.reconciliation.suspend_in(tx, current)
                        return
                    if runs.reconciliation.live_calls(run_id):
                        raise Conflict("Original local calls are still active")
                    data = await self.cursors_in(tx, current)
                    checkpoint = await tx.get_checkpoint(owner, run_id)
                    summaries = []
                    for summary in current.state.operations:
                        summaries.append(
                            (await operation_in(tx, current, summary.operation_id)).summary()
                        )
                    stop = runs.reconciliation.stopping_error(current, data)
                    if current.state.cancel_requested or stop is not None:
                        await runs.finish_in(
                            tx,
                            run_id,
                            owner,
                            cancelled=current.state.cancel_requested,
                            error=stop,
                            operations=summaries,
                        )
                        return
                    slot = runs.runtime._reserve()
                    version = current.state.state_version + 1
                    await tx.save_step(
                        owner,
                        expected_version=version - 1,
                        state=current.state.model_copy(
                            update={
                                "state_version": version,
                                "status": "running",
                                "pending": None,
                                "reason": None,
                                "recoverable": False,
                                "operations": summaries,
                            }
                        ),
                        checkpoint=checkpoint.model_copy(
                            update={"state_version": version, "data": data}
                        ),
                        events=[EventDraft(type="run.running")],
                    )
                if slot is not None:
                    try:
                        work = runs.runtime._spawn(
                            slot, lambda work: runs.execute(run_id, owner, work), check_cancel=False
                        )
                    except Exception:
                        await runs.suspend_recovery(run_id, owner)
                        raise
                    runs.attach(run_id, work)
            finally:
                if slot is not None and not slot.claimed:
                    slot.release()

        await runs.control(apply)
        if wait:
            return await runs.wait(run_id, context=context)
        return (await runs.inspect(run_id, context=context)).snapshot
