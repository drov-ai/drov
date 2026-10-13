"""Provider-independent model contract. No provider client is imported."""

from drov.models.protocol import ModelAdapter, ModelRequest, ModelResponse

__all__ = ["ModelAdapter", "ModelRequest", "ModelResponse"]
