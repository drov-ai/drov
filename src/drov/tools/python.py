"""Executable Python tools, sharing the T-02 schema contract."""

import inspect
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Any, Literal

from drov.errors import ConfigurationError
from drov.tools.schema import ToolDefinition, compile_tool
from drov.types import Effect


@dataclass(frozen=True)
class PythonTool(ToolDefinition):
    function: Callable[..., Any]


def tool(
    *,
    name: str,
    revision: str = "1",
    effect: Effect = "unknown",
    approval: Literal["required", "policy"] = "policy",
    description: str | None = None,
) -> Callable[[Callable[..., Any]], PythonTool]:
    """Compile a callable; construction neither authorizes nor executes it."""
    if name in {"memory.remember", "memory.recall", "memory.forget"}:
        raise ConfigurationError("Built-in Memory tool names are reserved")

    def decorate(function: Callable[..., Any]) -> PythonTool:
        if (
            not callable(function)
            or inspect.isgeneratorfunction(function)
            or inspect.isasyncgenfunction(function)
        ):
            raise ConfigurationError("Tools must return a value, not a generator")
        definition = compile_tool(
            function,
            name=name,
            revision=revision,
            effect=effect,
            approval=approval,
            description=description,
        )
        return PythonTool(
            **{field.name: getattr(definition, field.name) for field in fields(ToolDefinition)},
            function=function,
        )

    return decorate
