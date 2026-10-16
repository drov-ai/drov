"""Authorized, bounded observation copies; never recovery records or backups."""

import base64
import hashlib
from collections.abc import Callable
from typing import Literal, Self, TypeAlias

from pydantic import Field, model_validator

from drov._json import StrictJson
from drov.artifacts.types import ArtifactRef, FileName, Sha256
from drov.types import ContextManifest, RunInspection, WireModel

ExportDocument: TypeAlias = dict[str, StrictJson]
ExportRedactor: TypeAlias = Callable[[ExportDocument], ExportDocument]


class ExportedArtifact(WireModel):
    """Selected copy; source hash remains distinct from a redacted copy's hash."""

    source: ArtifactRef
    name: FileName
    sha256: Sha256
    size_bytes: int = Field(ge=0, le=64 * 1024**2)
    data_base64: str = Field(max_length=96 * 1024**2, repr=False)

    @model_validator(mode="after")
    def valid_bytes(self) -> Self:
        try:
            data = base64.b64decode(self.data_base64, validate=True)
        except ValueError as exc:
            raise ValueError("Invalid exported byte encoding") from exc
        if len(data) != self.size_bytes or hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError("Exported copy hash or length differs")
        return self


class RunExport(WireModel):
    schema_version: Literal[1] = 1
    content_visible: bool = False
    redacted: bool = False
    page: RunInspection
    context_manifests: list[ContextManifest] = Field(default_factory=list)
    files: list[ExportedArtifact] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def authorized_shape(self) -> Self:
        if self.page.snapshot.content_visible != self.content_visible:
            raise ValueError("Export content marker differs from its projection")
        if not self.content_visible:
            if (
                self.files
                or self.context_manifests
                or any(event.content_visible for event in self.page.events)
            ):
                raise ValueError("Metadata exports cannot contain bodies or bytes")
            allowed = {"operation_id", "attempt_id", "model_call_id", "error_code", "usage"}
            if any(event.data.keys() - allowed for event in self.page.events):
                raise ValueError("Metadata export contains content fields")
            if any(
                manifest.content_visible or manifest.sources
                for manifest in self.page.context_manifests
            ):
                raise ValueError("Metadata export contains context bodies")
        if len({file.source.artifact_id for file in self.files}) != len(self.files):
            raise ValueError("Export repeats a file identity")
        if any(
            manifest.run_id != self.page.snapshot.run_id or not manifest.content_visible
            for manifest in self.context_manifests
        ):
            raise ValueError("Export context does not match its authorized Run")
        if not self.redacted and any(
            (file.sha256, file.size_bytes) != (file.source.sha256, file.source.size_bytes)
            for file in self.files
        ):
            raise ValueError("Original export differs from its source reference")
        return self
