"""Generate schemas from implemented Python contracts; --check detects drift."""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from drov.artifacts import ArtifactInfo, ArtifactLimits, ArtifactRef
from drov.artifacts.records import ARTIFACT_DDL
from drov.artifacts.uploads import UPLOAD_DDL, UploadReceipt
from drov.auth import ArtifactResource, Resource, ToolResource
from drov.config import ContextPolicy, Limits, Memory, ModelCapabilities
from drov.integrations.e2b import E2BLimits
from drov.models.protocol import ModelEvent, ModelRequest, ModelResponse
from drov.runtime import AgentRegistration
from drov.sandbox import SandboxLimits, SandboxResult
from drov.sandbox._leases import SANDBOX_DDL
from drov.server.types import AgentCatalog, ResponseBody, ServerLimits, StartBody, VerifyBody
from drov.storage.sqlite import DDL, RECORD_VERSION, SCHEMA_VERSION
from drov.tools import PermissionPolicy
from drov.tools.permissions import PermissionDecision
from drov.types import (
    CancelReceipt,
    ContextManifest,
    DurableEvent,
    InteractionRequest,
    InteractionResponse,
    JsonOutcome,
    ResponseReceipt,
    RunContext,
    RunInspection,
    RunSnapshot,
    SessionPage,
    SessionView,
    TransientEvent,
    UnknownDurableEvent,
)


def schema_text() -> str:
    types: dict[str, Any] = {
        "RunOutcome": JsonOutcome,
        "InteractionRequest": InteractionRequest,
        "InteractionResponse": InteractionResponse,
        "TransientEvent": TransientEvent,
        "DurableEvent": DurableEvent,
        "UnknownDurableEvent": UnknownDurableEvent,
        "RunSnapshot": RunSnapshot,
        "RunInspection": RunInspection,
        "ContextManifest": ContextManifest,
        "ResponseReceipt": ResponseReceipt,
        "CancelReceipt": CancelReceipt,
        "SessionPage": SessionPage,
        "SessionView": SessionView,
        "Limits": Limits,
        "ModelCapabilities": ModelCapabilities,
        "ModelRequest": ModelRequest,
        "ModelResponse": ModelResponse,
        "ModelEvent": ModelEvent,
        "RunContext": RunContext,
        "ContextPolicy": ContextPolicy,
        "Memory": Memory,
        "Resource": Resource,
        "AgentRegistration": AgentRegistration,
        "PermissionPolicy": PermissionPolicy,
        "PermissionDecision": PermissionDecision,
        "ToolResource": ToolResource,
        "ArtifactRef": ArtifactRef,
        "ArtifactInfo": ArtifactInfo,
        "ArtifactLimits": ArtifactLimits,
        "ArtifactResource": ArtifactResource,
        "HTTPStart": StartBody,
        "AgentCatalog": AgentCatalog,
        "HTTPResponse": ResponseBody,
        "HTTPVerify": VerifyBody,
        "ServerLimits": ServerLimits,
        "UploadReceipt": UploadReceipt,
        "SandboxLimits": SandboxLimits,
        "E2BLimits": E2BLimits,
        "SandboxResult": SandboxResult,
    }
    bundle = {
        "schema_version": 1,
        "description": (
            "Independent JSON Schema documents; "
            "cross-field invariants also require Python validation."
        ),
        "schemas": {name: TypeAdapter(value).json_schema() for name, value in types.items()},
    }
    return json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "docs/reference"
    sql = (
        f"-- Generated: SQLite schema {SCHEMA_VERSION}, record schema {RECORD_VERSION}.\n"
        "-- Reference only: SQLiteStorage.open initializes and validates private metadata.\n"
        + ";\n\n".join(statement.strip() for statement in DDL)
        + f";\n\nPRAGMA user_version={SCHEMA_VERSION};\n"
    )
    for target, expected in (
        (root / "schema-v1.json", schema_text()),
        (root / "storage-schema-v1.sql", sql),
        (
            root / "storage-uploads-schema-v1.sql",
            "-- Explicit HTTP upload extension v1; core/Artifact schema remain 1.\n"
            + ";\n\n".join(statement.strip() for statement in UPLOAD_DDL)
            + ";\n",
        ),
        (
            root / "storage-sandbox-schema-v1.sql",
            "-- Explicit sandbox lease extension v1.\n"
            "-- E2B opts into metadata sandbox_schema=2; same DDL, remote payload fields.\n"
            + ";\n\n".join(statement.strip() for statement in SANDBOX_DDL)
            + ";\n",
        ),
        (
            root / "storage-artifacts-schema-v1.sql",
            "-- Explicit ArtifactStore extension v1; core records/user_version remain 1.\n"
            + ";\n\n".join(statement.strip() for statement in ARTIFACT_DDL)
            + ";\n",
        ),
    ):
        if args.check:
            if not target.exists() or target.read_text() != expected:
                raise SystemExit("Generated schemas are out of date")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(expected)
    print("Schema check passed" if args.check else "Schemas generated")


if __name__ == "__main__":
    main()
