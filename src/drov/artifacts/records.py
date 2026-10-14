"""SQLite file extension records; private byte paths never leave the kernel."""

from typing import Literal

from pydantic import Field

from drov.artifacts.types import ArtifactRef, FileName
from drov.storage.records import MemoryScope
from drov.types import Identifier, PositiveInt, WireModel

ARTIFACT_SCHEMA = 1
ARTIFACT_DDL = (
    """CREATE TABLE artifact_records (
        artifact_id TEXT PRIMARY KEY NOT NULL,
        tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, agent_name TEXT NOT NULL,
        source_run_id TEXT NOT NULL, payload TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('ready','unavailable')),
        version INTEGER NOT NULL CHECK(typeof(version)='integer' AND version>=1)
    )""",
    "CREATE INDEX artifact_owners ON artifact_records(tenant_id,user_id,agent_name)",
    """CREATE TABLE artifact_grants (
        run_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
        position INTEGER NOT NULL CHECK(typeof(position)='integer' AND position>=0),
        payload TEXT NOT NULL,
        PRIMARY KEY(run_id,artifact_id), UNIQUE(run_id,position),
        FOREIGN KEY(artifact_id) REFERENCES artifact_records(artifact_id)
    )""",
)


class ArtifactRecord(WireModel):
    schema_version: Literal[1] = 1
    ref: ArtifactRef
    name: FileName
    owner: MemoryScope
    run_id: Identifier
    operation_id: Identifier | None = None
    store_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    stage_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    state: Literal["ready", "unavailable"] = "ready"
    version: PositiveInt = 1
    unavailable_reason: Identifier | None = None


class ArtifactGrant(WireModel):
    schema_version: Literal[1] = 1
    run_id: Identifier
    ref: ArtifactRef
    name: FileName
    position: int = Field(ge=0)
