"""Artifact values and explicit private byte storage, with inert module imports."""

from typing import TYPE_CHECKING, Any

from drov.artifacts.types import ArtifactInfo, ArtifactInput, ArtifactLimits, ArtifactRef

if TYPE_CHECKING:
    from drov.artifacts.local import LocalArtifacts

__all__ = ["ArtifactInfo", "ArtifactInput", "ArtifactLimits", "ArtifactRef", "LocalArtifacts"]


def __getattr__(name: str) -> Any:
    if name == "LocalArtifacts":
        from drov.artifacts.local import LocalArtifacts

        return LocalArtifacts
    raise AttributeError(name)
