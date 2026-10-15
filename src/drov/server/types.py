"""HTTP values without Web imports. Runtime configuration remains host-owned."""

from typing import Annotated

from pydantic import Field

from drov.artifacts.types import ArtifactId
from drov.config import Limits
from drov.types import Identifier, InteractionResponse, NonEmpty, ScopedIdentifier, WireModel

UploadId = Annotated[str, Field(pattern=r"^upl_[0-9a-f]{32}$")]


class StartBody(WireModel):
    agent_name: ScopedIdentifier
    revision: ScopedIdentifier
    session_id: ScopedIdentifier
    input: NonEmpty
    request_key: ScopedIdentifier | None = None
    limits: Limits | None = None
    artifact_ids: list[ArtifactId] = Field(default_factory=list, max_length=256)
    upload_ids: list[UploadId] = Field(default_factory=list, max_length=256)


class ResponseBody(WireModel):
    request_id: Identifier
    response: InteractionResponse


class VerifyBody(WireModel):
    request_id: Identifier


class EmptyBody(WireModel):
    pass


class ErrorDetail(WireModel):
    code: str
    message: str
    request_id: str


class ErrorEnvelope(WireModel):
    error: ErrorDetail


class ServerLimits(WireModel):
    max_json_bytes: int = Field(default=262144, ge=1, le=16 * 1024**2)
    max_connections: int = Field(default=128, ge=1, le=4096)
    upload_concurrency: int = Field(default=4, ge=1, le=64)
    upload_seconds: float = Field(default=30.0, gt=0, le=300)
    send_seconds: float = Field(default=10.0, gt=0, le=300)
