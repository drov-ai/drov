"""Registered input schema with a kernel handler; no suspended Python function stack."""

from dataclasses import dataclass, fields
from typing import Any

from drov.tools.schema import ToolDefinition, compile_tool


@dataclass(frozen=True, init=False)
class HumanInput(ToolDefinition):
    def __init__(self, *, name: str, input_type: type[Any], revision: str = "1") -> None:
        def definition(prompt: str) -> Any:
            raise AssertionError("HumanInput is handled by the runtime")

        definition.__annotations__ = {"prompt": str, "return": input_type}
        compiled = compile_tool(definition, name=name, revision=revision, effect="read")
        for field in fields(ToolDefinition):
            object.__setattr__(self, field.name, getattr(compiled, field.name))
