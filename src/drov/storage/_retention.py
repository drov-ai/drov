"""Versioned, content-free retention facts shared by maintenance and readers."""

import hashlib
import hmac
import re
from typing import TYPE_CHECKING, Literal

from drov._json import canonical_json
from drov.errors import PersistenceUnavailable
from drov.types import Identifier, RunStatus, UtcTime, WireModel

if TYPE_CHECKING:
    from drov.artifacts.records import ArtifactRecord

RETENTION_DDL = (
    """CREATE TABLE retention_tombstones (
        kind TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(kind,identity)
    )""",
)


class Tombstone(WireModel):
    schema_version: Literal[1] = 1
    resource_id: Identifier | None = None
    session_id: Identifier | None = None
    agent_name: Identifier | None = None
    operation_id: Identifier | None = None
    source_run_id: Identifier | None = None
    status: RunStatus | None = None
    reason: Literal["session_deleted"] = "session_deleted"
    deleted_at: UtcTime


def identity(
    secret: bytes, kind: str, tenant: str, user: str, key: str, namespace: str = ""
) -> str:
    return hmac.new(
        secret,
        canonical_json(["drov.retention.v1", kind, tenant, user, namespace, key]).encode(),
        hashlib.sha256,
    ).hexdigest()


def origin_identity(secret: bytes, record: "ArtifactRecord") -> str:
    return hmac.new(
        secret,
        canonical_json(
            [
                "drov.retained-origin.v1",
                record.owner.model_dump(),
                record.run_id,
                record.operation_id,
                record.ref.model_dump(),
                record.name,
                record.store_id,
                record.stage_id,
            ]
        ).encode(),
        hashlib.sha256,
    ).hexdigest()


def cost_key(secret: bytes, budget_id: str) -> str:
    return "retained_e2b_cost:" + identity(secret, "e2b_cost", "", "", budget_id)


def retained_cost(value: str) -> int:
    try:
        if not re.fullmatch(r"0|[1-9][0-9]*", value):
            raise ValueError("Invalid amount")
        return int(value)
    except (ValueError, TypeError) as exc:
        raise PersistenceUnavailable("Retained cloud cost ledger is invalid") from exc
