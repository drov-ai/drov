"""Saved approval is an operation-specific grant, never a replacement for current policy."""

from drov._json import canonical_json
from drov.errors import PersistenceUnavailable
from drov.storage.records import RunRecord
from drov.storage.transaction import Transaction
from drov.tools.operations import ToolOperation
from drov.types import ApprovalDecision, ApprovalRequest, ResponseReceipt


async def approval_status(tx: Transaction, run: RunRecord, operation: ToolOperation) -> str:
    checkpoint = await tx.get_checkpoint(run.scope, run.state.run_id)
    grants = checkpoint.data.get("approval_grants", {})
    if not isinstance(grants, dict):
        raise PersistenceUnavailable("Invalid approval grant index")
    request_id = grants.get(operation.operation_id)
    if request_id is None:
        return "missing"
    if not isinstance(request_id, str):
        raise PersistenceUnavailable("Invalid approval grant reference")
    item = await tx.get_item(run.scope, run.state.run_id, "interaction", request_id)
    receipt_item = await tx.get_item(run.scope, run.state.run_id, "receipt", request_id)
    request = ApprovalRequest.model_validate(item.data["request"])
    receipt = ResponseReceipt.model_validate(receipt_item.data["receipt"])
    response = ApprovalDecision.model_validate(receipt_item.data["response"])
    if (
        item.state != "consumed"
        or receipt_item.state != "accepted"
        or request.request_id != request_id
        or receipt.request_id != request_id
        or request.run_id != run.state.run_id
        or receipt.run_id != run.state.run_id
        or request.operation_id != operation.operation_id
        or request.tool_name != operation.tool_name
        or item.data["request_hash"] != operation.request_hash
        or item.data["config_hash"] != run.config_hash
        or canonical_json(request.arguments_summary) != canonical_json(operation.arguments)
        or response.approved is not True
    ):
        raise PersistenceUnavailable("Approval no longer matches the saved operation")
    return "expired" if tx._now() >= request.expires_at else "granted"
