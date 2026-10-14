"""Sandbox observations; their publication is the Runtime's responsibility."""

from typing import Literal

from pydantic import Field, model_validator

from drov.artifacts.types import ArtifactRef
from drov.types import Identifier, WireModel


class SandboxArtifactError(WireModel):
    code: Identifier


class SandboxResult(WireModel):
    execution_key: str = Field(pattern=r"^[0-9a-f]{32}$")
    exit_code: int | None
    termination: Literal["exited", "timeout", "output_limit", "resource_limit", "cancelled"]
    stdout: str
    stderr: str
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    artifact_status: Literal["not_requested", "complete", "rejected"]
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    artifact_error: SandboxArtifactError | None = None

    @model_validator(mode="after")
    def consistent(self) -> "SandboxResult":
        if self.termination == "exited" and self.exit_code is None:
            raise ValueError("A normal exit requires an actual exit code")
        if self.artifact_status == "complete":
            if (
                self.termination != "exited"
                or self.exit_code != 0
                or not self.artifacts
                or self.artifact_error is not None
            ):
                raise ValueError("Complete exports require normal execution and actual references")
        elif self.artifacts:
            raise ValueError("Incomplete exports cannot carry successful references")
        if (self.artifact_status == "rejected") != (self.artifact_error is not None):
            raise ValueError("Rejected exports require a structured error")
        return self
