"""Runtime-only tool descriptor, with a fixed logical name and no host callable."""

from dataclasses import dataclass, fields
from typing import Any, Literal

from pydantic import TypeAdapter

from drov._json import StrictJson
from drov.sandbox.types import SandboxResult
from drov.tools.schema import JsonCodec, ToolDefinition, compile_type
from drov.types import ScopedIdentifier

NAME = "sandbox.run_python"


@dataclass(frozen=True)
class SandboxResultCodec(JsonCodec):
    """Dedicated trusted boundary; ordinary tools cannot enable custom validators."""

    def decode(self, value: StrictJson) -> SandboxResult:
        return SandboxResult.model_validate(value)

    def encode(self, value: Any) -> StrictJson:
        return SandboxResult.model_validate(value).model_dump(mode="json")


@dataclass(frozen=True, init=False)
class PythonSandbox(ToolDefinition):
    backend: str

    def __init__(self, *, backend: str, approval: Literal["policy", "required"] = "policy") -> None:
        TypeAdapter(ScopedIdentifier).validate_python(backend, strict=True)
        TypeAdapter(Literal["policy", "required"]).validate_python(approval, strict=True)
        codecs = {
            name: compile_type(kind)
            for name, kind in {"code": str, "inputs": list[str], "outputs": list[str]}.items()
        }
        properties = {name: dict(codec.schema) for name, codec in codecs.items()}
        properties["inputs"]["default"] = []
        properties["outputs"]["default"] = []
        definition = ToolDefinition(
            name=NAME,
            revision="1",
            effect="write",
            approval=approval,
            description="Execute Python once in a fresh isolated environment. "
            "Read only the granted "
            "input IDs at their described /work/inputs/<id>/<name> locations; "
            "declare outputs relative to /work/outputs. "
            "Use actual results and file references; no implicit shared state.",
            parameters={
                "type": "object",
                "properties": properties,
                "required": ["code"],
                "additionalProperties": False,
            },
            argument_codecs=codecs,
            defaults={"inputs": [], "outputs": []},
            result_codec=SandboxResultCodec(SandboxResult, SandboxResult.model_json_schema()),
            context_parameter=None,
        )
        for item in fields(ToolDefinition):
            object.__setattr__(self, item.name, getattr(definition, item.name))
        object.__setattr__(self, "backend", backend)
