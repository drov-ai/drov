"""Executable Python tools, sharing the T-02 schema contract."""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, fields
from typing import Any, Literal

from drov.errors import ConfigurationError
from drov.tools.schema import ToolContext, ToolDefinition, compile_tool
from drov.types import Effect, ReconciliationDecision


@dataclass(frozen=True)
class PythonTool(ToolDefinition):
    function: Callable[..., Any]
    verifier: Callable[[ToolContext], Awaitable[ReconciliationDecision]] | None = None
    verifier_revision: str | None = None


def tool(
    *,
    name: str,
    revision: str = "1",
    effect: Effect = "unknown",
    approval: Literal["required", "policy"] = "policy",
    description: str | None = None,
    verifier: Callable[[ToolContext], Awaitable[ReconciliationDecision]] | None = None,
    verifier_revision: str = "1",
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
        if verifier is not None:
            if not inspect.iscoroutinefunction(verifier):
                raise ConfigurationError("Verifier must be an async read-only context callback")
            from pydantic import TypeAdapter

            from drov.types import ScopedIdentifier

            TypeAdapter(ScopedIdentifier).validate_python(verifier_revision, strict=True)
            try:
                inspect.signature(verifier).bind(object())
            except TypeError as exc:
                raise ConfigurationError("Verifier must accept one injected ToolContext") from exc
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
            verifier=verifier,
            verifier_revision=verifier_revision if verifier is not None else None,
        )

    return decorate
