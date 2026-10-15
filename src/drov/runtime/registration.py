"""Versioned fingerprints of declared configuration, never of executable code."""

import hashlib
import inspect
from typing import Any, Literal, cast

from pydantic import Field, TypeAdapter

from drov._json import canonical_json, load_json
from drov.agent import Agent
from drov.config import ContextPolicy, Limits, Memory, ModelCapabilities
from drov.errors import ConfigurationError
from drov.sandbox.tools import PythonSandbox, SandboxResultCodec
from drov.sandbox.types import SandboxResult
from drov.tools.python import PythonTool
from drov.tools.schema import JsonCodec, compile_output, compile_type
from drov.types import ScopedIdentifier, WireModel


class AgentRegistration(WireModel):
    name: ScopedIdentifier
    revision: ScopedIdentifier
    fingerprint_version: Literal[1] = 1
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def fingerprint(agent: Agent[Any]) -> AgentRegistration:
    name, revision = agent.require_registration_identity()
    # Reconstruct to revalidate definitions mutated after construction.
    checked = Agent(
        model=agent.model,
        name=name,
        revision=revision,
        instructions=agent.instructions,
        tools=agent.tools,
        output_type=agent.output_type,
        memory=Memory.from_json(agent.memory.model_dump_json()) if agent.memory else None,
        context_policy=ContextPolicy.from_json(agent.context_policy.model_dump_json()),
        limits=Limits.from_json(agent.limits.model_dump_json()),
        model_config=agent.model_config,
        required_dependencies=agent.required_dependencies,
    )
    tools: list[dict[str, Any]] = []
    for tool in checked._execution_tools:
        TypeAdapter(ScopedIdentifier).validate_python(tool.name, strict=True)
        TypeAdapter(ScopedIdentifier).validate_python(tool.revision, strict=True)
        if tool.effect not in ("read", "write", "unknown") or tool.approval not in (
            "required",
            "policy",
        ):
            raise ConfigurationError("Invalid registered tool metadata")
        if isinstance(tool, PythonTool) and tool.verifier is not None:
            if not inspect.iscoroutinefunction(tool.verifier):
                raise ConfigurationError("Registered verifier must be an async read-only callback")
            TypeAdapter(ScopedIdentifier).validate_python(tool.verifier_revision, strict=True)
        properties = {
            key: dict(compile_type(codec.python_type).schema)
            for key, codec in tool.argument_codecs.items()
        }
        if set(tool.defaults) - properties.keys():
            raise ConfigurationError("Invalid registered tool defaults")
        for key, default in tool.defaults.items():
            properties[key]["default"] = tool.argument_codecs[key].encode(default)
        expected = {
            "type": "object",
            "properties": properties,
            "required": [key for key in properties if key not in tool.defaults],
            "additionalProperties": False,
        }
        result: JsonCodec
        if isinstance(tool, PythonSandbox):
            if not isinstance(tool.result_codec, SandboxResultCodec):
                raise ConfigurationError("Sandbox result requires its dedicated trusted codec")
            result = SandboxResultCodec(SandboxResult, SandboxResult.model_json_schema())
        else:
            result = compile_type(tool.result_codec.python_type, allow_dict=True)
        if (
            canonical_json(expected) != canonical_json(tool.parameters)
            or result.schema != tool.result_codec.schema
            or (tool.context_parameter is not None and tool.context_parameter in properties)
        ):
            raise ConfigurationError("Registered tool schema does not match its codecs")
        tools.append(
            {
                "name": tool.name,
                "revision": tool.revision,
                "description": tool.description,
                "effect": tool.effect,
                "approval": tool.approval,
                "parameters": expected,
                "result": result.schema,
                "context_parameter": tool.context_parameter,
                **({"sandbox_backend": tool.backend} if isinstance(tool, PythonSandbox) else {}),
                **(
                    {"verifier_revision": tool.verifier_revision}
                    if isinstance(tool, PythonTool) and tool.verifier is not None
                    else {}
                ),
            }
        )
    output_schema = compile_output(checked.output_type).schema
    if output_schema != agent.output_codec.schema:
        raise ConfigurationError("Registered output schema does not match its codec")
    payload = {
        "fingerprint_version": 1,
        "name": name,
        "revision": revision,
        "instructions": checked.instructions,
        "model": {
            "adapter": f"{type(agent.model).__module__}.{type(agent.model).__qualname__}",
            "capabilities": ModelCapabilities.from_json(
                agent.model.capabilities.model_dump_json()
            ).model_dump(mode="json"),
            "config": checked.model_config,
        },
        "tools": tools,
        "output_schema": output_schema,
        "memory": checked.memory.model_dump(mode="json") if checked.memory else None,
        "context_policy": checked.context_policy.model_dump(mode="json"),
        "limits": checked.limits.model_dump(mode="json"),
        "required_dependencies": sorted(checked.required_dependencies),
    }
    adapter_config = getattr(agent.model, "registration_config", None)
    if adapter_config is not None:
        # Adapter-declared public configuration only. Never inspect client internals.
        if type(adapter_config) is not dict:
            raise ConfigurationError("Invalid adapter registration configuration")
        cast(dict[str, Any], payload["model"])["adapter_config"] = load_json(
            canonical_json(adapter_config)
        )
    return AgentRegistration(
        name=name,
        revision=revision,
        config_hash=hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(),
    )
