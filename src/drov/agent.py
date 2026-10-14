"""Reusable Agent definition with temporary entry points using the same Runtime."""

import asyncio
from collections.abc import Mapping, Sequence
from typing import Generic, TypeVar, cast
from uuid import uuid4

from pydantic import TypeAdapter

from drov._json import StrictJson, canonical_json, load_json
from drov.artifacts.types import ArtifactInput, ArtifactRef
from drov.config import ContextPolicy, Limits, Memory, ModelCapabilities
from drov.errors import ConfigurationError
from drov.models.protocol import ModelAdapter
from drov.tools.schema import ToolDefinition, compile_output
from drov.types import RunContext, RunOutcome, ScopedIdentifier

T = TypeVar("T")


class Agent(Generic[T]):
    def __init__(
        self,
        *,
        model: ModelAdapter,
        name: str | None = None,
        revision: str | None = None,
        instructions: str = "",
        tools: Sequence[ToolDefinition] = (),
        output_type: type[T] | type[str] = str,
        memory: Memory | None = None,
        context_policy: ContextPolicy | None = None,
        limits: Limits | None = None,
        model_config: Mapping[str, StrictJson] | None = None,
        required_dependencies: Sequence[str] = (),
    ) -> None:
        if not isinstance(model, ModelAdapter):
            raise ConfigurationError("model must implement ModelAdapter")
        if (name is None) != (revision is None):
            raise ConfigurationError("Agent name and revision must be supplied together")
        for identifier in (name, revision):
            if identifier is not None:
                TypeAdapter(ScopedIdentifier).validate_python(identifier, strict=True)
        if type(instructions) is not str:
            raise ConfigurationError("instructions must be a string")
        capabilities = ModelCapabilities.model_validate(model.capabilities)
        limits = Limits() if limits is None else Limits.model_validate(limits)
        if not capabilities.text:
            raise ConfigurationError("v0.1 requires text model capability")
        if tools and not capabilities.tools:
            raise ConfigurationError("Model has no declared tool capability")
        if output_type is not str and not capabilities.structured_output:
            raise ConfigurationError("Model has no declared structured output capability")
        if capabilities.context_window is None and limits.max_input_tokens is None:
            raise ConfigurationError("Unknown model window requires explicit max_input_tokens")
        if capabilities.context_window is not None:
            if capabilities.context_window <= limits.output_reserve_tokens:
                raise ConfigurationError("Output reserve leaves no model input budget")
            if (
                limits.max_input_tokens is not None
                and limits.max_input_tokens
                > capabilities.context_window - limits.output_reserve_tokens
            ):
                raise ConfigurationError("Input budget exceeds declared model window")
        if any(not isinstance(tool, ToolDefinition) for tool in tools):
            raise ConfigurationError("Tools must be compiled definitions or decorated Python tools")
        if len({tool.name for tool in tools}) != len(tools):
            raise ConfigurationError("Duplicate logical tool names")
        from drov.memory.tools import NAMES

        if any(tool.name in NAMES for tool in tools):
            raise ConfigurationError("Built-in Memory tool names are reserved")
        from drov.sandbox.tools import NAME, PythonSandbox

        if any(tool.name == NAME and not isinstance(tool, PythonSandbox) for tool in tools):
            raise ConfigurationError("Built-in sandbox tool name is reserved")
        if memory is not None:
            Memory.model_validate(memory)
            if not capabilities.tools:
                raise ConfigurationError("Memory tools require declared tool capability")
        self.model = model
        self.name = name
        self.revision = revision
        self.instructions = instructions
        self.tools = tuple(tools)
        self.output_type = output_type
        self.output_codec = compile_output(output_type)
        self.memory = memory
        self.context_policy = (
            ContextPolicy()
            if context_policy is None
            else ContextPolicy.model_validate(context_policy)
        )
        self.limits = limits
        # Only application-declared, non-secret configuration; never inspect client internals.
        self.model_config = cast(
            dict[str, StrictJson], load_json(canonical_json(dict(model_config or {})))
        )
        if isinstance(required_dependencies, (str, bytes)):
            raise ConfigurationError("required_dependencies must be a sequence of names")
        for dependency in required_dependencies:
            TypeAdapter(ScopedIdentifier).validate_python(dependency, strict=True)
        if len(set(required_dependencies)) != len(required_dependencies):
            raise ConfigurationError("Duplicate required dependency names")
        self.required_dependencies = tuple(required_dependencies)

    @property
    def _execution_tools(self) -> tuple[ToolDefinition, ...]:
        from drov.memory.tools import TOOLS

        return self.tools + (TOOLS if self.memory is not None else ())

    def require_registration_identity(self) -> tuple[str, str]:
        if self.name is None or self.revision is None:
            raise ConfigurationError("Persistent registration requires Agent name and revision")
        return self.name, self.revision

    async def run(
        self,
        input: str,
        *,
        context: RunContext | None = None,
        limits: Limits | None = None,
        artifacts: Sequence[ArtifactInput | ArtifactRef] = (),
    ) -> RunOutcome[T]:
        from drov.runtime.core import Runtime

        if artifacts:
            raise ConfigurationError("Temporary Agent.run cannot accept persistent files")
        if self.memory is not None:
            raise ConfigurationError("Temporary Agent.run cannot use persistent Memory")
        from drov.sandbox.tools import PythonSandbox

        if any(isinstance(tool, PythonSandbox) for tool in self.tools):
            raise ConfigurationError("PythonSandbox requires an explicit persistent Runtime")
        # Clone identity only; never mutate a definition borrowed by an existing Runtime.
        temporary = Agent(
            model=self.model,
            name=self.name or "temporary",
            revision=self.revision or "1",
            instructions=self.instructions,
            tools=self.tools,
            output_type=self.output_type,
            context_policy=self.context_policy,
            limits=self.limits,
            model_config=self.model_config,
            required_dependencies=self.required_dependencies,
        )
        temporary_limits = Limits.model_validate(
            self.limits.model_dump() | (limits.model_dump(exclude_unset=True) if limits else {})
        )
        runtime = Runtime(agents=[temporary], limits=temporary_limits)
        runtime._temporary = True
        async with runtime:
            return await runtime.run(
                temporary,
                input,
                session_id=str(uuid4()),
                context=context or RunContext(tenant_id="local", user_id="local"),
                limits=limits,
            )

    def run_sync(
        self,
        input: str,
        *,
        context: RunContext | None = None,
        limits: Limits | None = None,
        artifacts: Sequence[ArtifactInput | ArtifactRef] = (),
    ) -> RunOutcome[T]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run(input, context=context, limits=limits, artifacts=artifacts))
        raise ConfigurationError("An event loop is running; use await agent.run(...) instead")
