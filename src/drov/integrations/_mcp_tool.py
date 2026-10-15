"""Core-safe MCP definitions; optional SDK imports occur only at explicit use."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from drov._json import load_json
from drov.errors import ConfigurationError
from drov.tools.python import PythonTool
from drov.tools.schema import ToolContext


class MCPConnection(ABC):
    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    def validate_tool(self, tool: "MCPTool") -> None: ...

    @abstractmethod
    async def call(self, tool: "MCPTool", arguments: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class MCPTool(PythonTool):
    """A frozen discovery snapshot, executed through a Runtime dependency."""

    dependency: str = ""
    remote_name: str = ""
    source_hash: str = ""
    snapshot: str = ""

    def bind_arguments(
        self, data: str | bytes, *, context: ToolContext, max_bytes: int = 65536
    ) -> dict[str, object]:
        from drov.integrations.mcp import _validator

        value = load_json(data, max_bytes=max_bytes)
        if not _validator(self.parameters).is_valid(value) or type(value) is not dict:
            raise ValueError("MCP arguments do not match the frozen schema")
        result: dict[str, object] = dict(value)
        assert self.context_parameter is not None
        result[self.context_parameter] = context
        return result

    def registration(self) -> dict[str, Any]:
        # Rebuild from the immutable discovery payload; mutable codec/schema dictionaries
        # must not silently change a saved approval or registration fingerprint.
        from drov.integrations.mcp import _make_tool

        checked = _make_tool(
            self.snapshot,
            dependency=self.dependency,
            source_hash=self.source_hash,
            revision=self.revision,
            name=self.name,
            effect=self.effect,
            approval=self.approval,
        )
        if (
            self.parameters != checked.parameters
            or self.description != checked.description
            or self.remote_name != checked.remote_name
            or self.argument_codecs != checked.argument_codecs
            or self.result_codec != checked.result_codec
            or self.defaults != checked.defaults
            or self.context_parameter != checked.context_parameter
            or self.verifier is not None
        ):
            raise ConfigurationError("MCP tool no longer matches its discovery snapshot")
        return {
            "dependency": self.dependency,
            "remote_name": self.remote_name,
            "source_hash": self.source_hash,
            "snapshot": self.snapshot,
        }
