"""Reuse current Run and per-Artifact authorization, without raw storage export."""

import base64
import hashlib
import json
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from drov._json import StrictJson, canonical_json
from drov.artifacts.types import ArtifactId, ArtifactRef
from drov.errors import CapacityExceeded, ConfigurationError, RecordNotFound
from drov.exports import ExportedArtifact, ExportRedactor, RunExport
from drov.types import RunContext

if TYPE_CHECKING:
    from drov.runtime.core import Runtime


# Public observation already excludes host storage/backend records. Only known
# protocol shapes need additional projection; arbitrary business keys such as
# "directory" retain their application meaning.
CONTROL_FIELDS = frozenset(
    ("execution_key", "container_id", "exec_id", "remote_id", "stage_id", "store_id")
)
SANDBOX_RESULT = frozenset(
    ("execution_key", "termination", "artifact_status", "stdout", "stderr", "exit_code")
)


def _sandbox(value: dict[str, StrictJson]) -> bool:
    key = value.get("execution_key")
    return (
        SANDBOX_RESULT <= value.keys()
        and isinstance(key, str)
        and re.fullmatch(r"[0-9a-f]{32}", key) is not None
        and isinstance(value.get("stdout"), str)
        and isinstance(value.get("stderr"), str)
    )


def _controls(value: StrictJson) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        source = value
        if value.get("type") == "drov.tool_preview" and isinstance(value.get("text"), str):
            text = value["text"]
            assert isinstance(text, str)
            found.update(re.findall(r'"execution_key"\s*:\s*"([0-9a-f]{32})"', text))
        if value.get("durable") is True and {"seq", "run_id", "type", "data"} <= value.keys():
            source = value["data"] if isinstance(value["data"], dict) else {}
        if source is not value or _sandbox(value):
            found.update(
                item
                for key, item in source.items()
                if key in CONTROL_FIELDS and isinstance(item, str) and len(item) >= 16
            )
        for item in value.values():
            found.update(_controls(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_controls(item))
    return found


def _scrub(value: StrictJson, controls: re.Pattern[str] | None) -> StrictJson:
    if isinstance(value, dict):
        if {
            "execution_key",
            "backend_hash",
            "code_hash",
            "backend_config",
        } <= value.keys() and isinstance(value.get("execution_key"), str):
            raise ConfigurationError("Internal recovery facts cannot be exported")
        if {"store_id", "stage_id", "owner", "ref"} <= value.keys() and isinstance(
            value.get("stage_id"), str
        ):
            raise ConfigurationError("Internal artifact records cannot be exported")
        if value.get("type") == "drov.tool_preview":
            # A truncated JSON text fragment cannot be safely projected as a
            # structured result. Keep its original provenance marker, not text
            # that may contain a partial private execution identity.
            value = value | {"text": "", "export_omitted": True}
        if value.get("durable") is True and {"seq", "run_id", "type", "data"} <= value.keys():
            data = value["data"]
            if isinstance(data, dict):
                value = value | {
                    "data": {key: item for key, item in data.items() if key not in CONTROL_FIELDS}
                }
        omitted: set[str] = set()
        if _sandbox(value):
            omitted.add("execution_key")
        if (
            isinstance(value.get("finish_reason"), str)
            and isinstance(value.get("tool_calls"), list)
            and isinstance(value.get("usage"), dict)
        ):
            omitted.add("provider_items")
        encoded_file = {"source", "data_base64", "sha256", "size_bytes", "name"} <= value.keys()
        return {
            key: item
            if encoded_file and key in ("data_base64", "source", "sha256")
            else _scrub(item, controls)
            for key, item in value.items()
            if key not in omitted
        }
    if isinstance(value, list):
        return [_scrub(item, controls) for item in value]
    if isinstance(value, str) and controls is not None:
        return controls.sub("redacted-control-id", value)
    return value


def _bounded(value: RunExport, limit: int) -> None:
    if len(value.model_dump_json().encode()) > limit:
        raise CapacityExceeded("Export exceeds its byte limit")


def _metadata(value: RunExport) -> object:
    snapshot = value.page.snapshot
    pending = snapshot.pending
    outcome = snapshot.outcome
    return (
        snapshot.model_dump(mode="json", exclude={"outcome", "pending"}),
        pending.model_dump(
            mode="json",
            include={
                "schema_version",
                "request_id",
                "run_id",
                "operation_id",
                "kind",
                "created_at",
                "expires_at",
            },
        )
        if pending
        else None,
        outcome.model_dump(mode="json", exclude={"output", "error"}) if outcome else None,
        outcome.error.model_dump(exclude={"message"})
        if outcome is not None and outcome.status == "failed"
        else None,
        [manifest.model_dump(mode="json") for manifest in value.page.context_manifests],
        [
            (
                manifest.model_dump(mode="json", exclude={"sources"}),
                [
                    source.model_dump(mode="json", exclude={"content"})
                    for source in manifest.sources
                ],
            )
            for manifest in value.context_manifests
        ],
        [
            {
                key: item
                for key, item in event.data.items()
                if key
                in (
                    "operation_id",
                    "attempt_id",
                    "model_call_id",
                    "error_code",
                    "usage",
                    "tool_name",
                    "tool_revision",
                )
            }
            for event in value.page.events
        ],
    )


async def export_run(
    runtime: "Runtime",
    run_id: str,
    *,
    context: RunContext,
    include_content: bool,
    artifact_ids: Sequence[str],
    after: str | None,
    limit: int,
    max_bytes: int,
    redactor: ExportRedactor | None,
) -> RunExport:
    runtime._require_open()
    if (
        type(include_content) is not bool
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= 64 * 1024**2
    ):
        raise ConfigurationError("Export requires a boolean content choice and bounded byte limit")
    if isinstance(artifact_ids, (str, bytes)):
        raise ConfigurationError("Select individual artifact IDs explicitly")
    ids = tuple(
        TypeAdapter(ArtifactId).validate_python(value, strict=True) for value in artifact_ids
    )
    if len(ids) > 16 or len(set(ids)) != len(ids):
        raise ConfigurationError("Export file selection exceeds its bound or repeats an ID")
    if ids and not include_content:
        raise ConfigurationError("File export requires explicit include_content")
    context = RunContext.from_json(context.model_dump_json())
    page = await runtime.inspect(
        run_id, context=context, after=after, limit=limit, include_content=include_content
    )
    record = await runtime._runs.visible(run_id, context, include_content)
    manifests = (
        [
            await runtime.get_context(
                run_id, manifest.model_call_id, context=context, include_content=True
            )
            for manifest in page.context_manifests
        ]
        if include_content
        else []
    )
    exported = RunExport(content_visible=include_content, page=page, context_manifests=manifests)
    _bounded(exported, max_bytes)
    sources = {}
    for artifact_id in ids:
        info = await runtime.get_artifact(artifact_id, context=context, include_content=True)
        if info.run_id != run_id and artifact_id not in {
            ref.artifact_id for ref in record.artifact_inputs
        }:
            raise RecordNotFound("Artifact does not belong to this export")
        # Reject a known oversized selection before loading its bytes.
        if info.size_bytes * 4 // 3 + 4 + len(exported.model_dump_json().encode()) > max_bytes:
            raise CapacityExceeded("Export file exceeds the remaining byte limit")
        data = await runtime.read_artifact(artifact_id, context=context)
        ref = ArtifactRef.model_validate(info.model_dump(include=set(ArtifactRef.model_fields)))
        assert info.name is not None
        exported.files.append(
            ExportedArtifact(
                source=ref,
                name=info.name,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
                data_base64=base64.b64encode(data).decode("ascii"),
            )
        )
        sources[artifact_id] = ref
        _bounded(exported, max_bytes)
    try:
        document = cast(dict[str, StrictJson], json.loads(exported.model_dump_json()))
        values = _controls(document)
        controls = (
            re.compile("|".join(re.escape(value) for value in sorted(values))) if values else None
        )
        cleaned = _scrub(document, controls)
        projected = RunExport.from_json(canonical_json(cleaned))
        if redactor is not None:
            # Only a JSON copy is supplied. A pure, non-blocking application
            # callback may redact body values or omit files, never edit storage.
            transformed = redactor(
                cast(dict[str, StrictJson], json.loads(projected.model_dump_json()))
            )
            transformed["redacted"] = True
            projected = RunExport.from_json(canonical_json(_scrub(transformed, controls)))
        before, after_snapshot = exported.page.snapshot, projected.page.snapshot
        identity = ("run_id", "state_version", "status", "agent_name", "revision", "session_id")
        if projected.content_visible != include_content or any(
            getattr(before, key) != getattr(after_snapshot, key) for key in identity
        ):
            raise ValueError("Redactor changed export identity or authority")
        if _metadata(projected) != _metadata(exported):
            raise ValueError("Redactor changed committed observation metadata")

        def events(value: RunExport) -> list[tuple[object, ...]]:
            return [
                (
                    e.run_id,
                    e.state_version,
                    e.seq,
                    e.type,
                    e.occurred_at,
                    e.schema_version,
                    e.durable,
                    e.content_visible,
                )
                for e in value.page.events
            ]

        if events(projected) != events(exported) or (
            projected.page.next_cursor,
            projected.page.has_more,
        ) != (exported.page.next_cursor, exported.page.has_more):
            raise ValueError("Redactor changed the committed event page")
        if any(sources.get(file.source.artifact_id) != file.source for file in projected.files):
            raise ValueError("Redactor introduced an unselected source")
        _bounded(projected, max_bytes)
    except CapacityExceeded:
        raise
    except Exception:
        raise ConfigurationError("Export transformation failed") from None
    # Recheck after all reads/transformation, including a callback-triggered revoke.
    await runtime._runs.visible(run_id, context, include_content)
    for artifact_id in ids:
        await runtime.get_artifact(artifact_id, context=context, include_content=True)
    return projected
