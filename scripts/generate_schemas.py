"""Generate schemas from implemented Python contracts; --check detects drift."""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from drov.auth import Resource
from drov.config import ContextPolicy, Limits, Memory, ModelCapabilities
from drov.models.protocol import ModelEvent, ModelRequest, ModelResponse
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
    target = Path(__file__).resolve().parents[1] / "docs/reference/schema-v1.json"
    expected = schema_text()
    if args.check:
        if not target.exists() or target.read_text() != expected:
            raise SystemExit("Generated schemas are out of date")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(expected)
    print("Schema check passed" if args.check else "Schemas generated")


if __name__ == "__main__":
    main()
