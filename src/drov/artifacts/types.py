"""Bounded file values; bytes are host input, never model tool arguments."""

import re
import unicodedata
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from drov.types import Identifier, WireModel


def file_path(value: str, *, single: bool = False) -> str:
    value = unicodedata.normalize("NFC", value)
    parts = value.split("/")
    if (
        len(value.encode("utf-8")) > 1024
        or len(parts) > (1 if single else 8)
        or "\\" in value
        or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
        or any(part in ("", ".", "..") or len(part.encode("utf-8")) > 255 for part in parts)
    ):
        raise ValueError("Expected a bounded relative POSIX file name")
    return value


def _name(value: str) -> str:
    return file_path(value, single=True)


def _media(value: str) -> str:
    # No header delimiters/parameters: MIME is an untrusted attachment hint.
    if not re.fullmatch(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+", value):
        raise ValueError("Expected a MIME type without parameters")
    return value.lower()


FileName = Annotated[str, AfterValidator(_name)]
MediaType = Annotated[str, Field(max_length=128), AfterValidator(_media)]
ArtifactId = Annotated[str, Field(pattern=r"^art_[0-9a-f]{32}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ArtifactInput(WireModel):
    name: FileName
    data: bytes = Field(exclude=True, repr=False)
    media_type: MediaType = "application/octet-stream"


class ArtifactRef(WireModel):
    artifact_id: ArtifactId
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    media_type: MediaType


class ArtifactInfo(ArtifactRef):
    schema_version: Literal[1] = 1
    run_id: Identifier
    operation_id: Identifier | None = None
    state: Literal["ready", "unavailable"]
    content_visible: bool = False
    name: FileName | None = None

    @model_validator(mode="after")
    def projection(self) -> Self:
        if (self.name is not None) != self.content_visible:
            raise ValueError("Only a content-authorized projection contains a name")
        return self


class ArtifactLimits(WireModel):
    max_input_files: int = Field(default=8, ge=1, le=256)
    max_input_bytes: int = Field(default=8 * 1024**2, ge=1, le=1024**3)
    max_output_files: int = Field(default=8, ge=1, le=256)
    max_artifact_bytes: int = Field(default=8 * 1024**2, ge=1, le=1024**3)
    max_export_bytes: int = Field(default=16 * 1024**2, ge=1, le=1024**3)
    max_owner_bytes: int = Field(default=64 * 1024**2, ge=1, le=64 * 1024**3)
    max_store_bytes: int = Field(default=1024**3, ge=1, le=1024**4)

    @model_validator(mode="after")
    def capacity(self) -> Self:
        if self.max_owner_bytes > self.max_store_bytes:
            raise ValueError("Owner capacity exceeds store capacity")
        return self
