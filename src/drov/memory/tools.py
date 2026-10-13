"""Internal descriptors; these tools can only execute through Runtime transactions."""

from dataclasses import dataclass
from typing import Any

from drov.context.assembly import digest, source
from drov.models.protocol import ModelMessage
from drov.storage.records import MemoryRecord
from drov.tools.schema import ToolDefinition, compile_type
from drov.types import ContextSource

NAMES = frozenset(("memory.remember", "memory.recall", "memory.forget"))


@dataclass
class MemoryValue:
    key: str
    content: str | None
    version: int
    source: str
    deleted: bool


@dataclass
class Remembered:
    key: str
    version: int


@dataclass
class Forgotten:
    key: str
    version: int
    deleted: bool


@dataclass(frozen=True)
class MemoryTool(ToolDefinition):
    pass


def _definition(
    name: str, arguments: dict[str, Any], defaults: dict[str, Any], result: Any, description: str
) -> MemoryTool:
    codecs = {key: compile_type(value) for key, value in arguments.items()}
    properties = {key: dict(codec.schema) for key, codec in codecs.items()}
    for key, default in defaults.items():
        properties[key]["default"] = codecs[key].encode(default)
    return MemoryTool(
        name=name,
        revision="1",
        description=description,
        effect="read" if name == "memory.recall" else "write",
        approval="policy",
        parameters={
            "type": "object",
            "properties": properties,
            "required": [key for key in arguments if key not in defaults],
            "additionalProperties": False,
        },
        result_codec=compile_type(result, allow_dict=True),
        argument_codecs=codecs,
        defaults=defaults,
        context_parameter=None,
    )


TOOLS = (
    _definition(
        "memory.remember",
        {"key": str, "content": str, "expected_version": int},
        {},
        Remembered,
        "Explicitly save a user memory. Use 0 for a never-created key; "
        "recall its version to update.",
    ),
    _definition(
        "memory.recall",
        {"key": str | None, "query": str | None, "limit": int},
        {"key": None, "query": None, "limit": 5},
        list[MemoryValue],
        "Read scoped memory by exact key (including tombstones), substring, or recent updates. "
        "key and non-empty query are mutually exclusive; limit is 1 to 50.",
    ),
    _definition(
        "memory.forget",
        {"key": str, "expected_version": int},
        {},
        Forgotten,
        "Delete a current memory using its recalled version. Existing history remains stored.",
    ),
)


def value(record: MemoryRecord) -> MemoryValue:
    return MemoryValue(record.key, record.content, record.version, record.source, record.deleted)


def memory_source(record: MemoryRecord, reason: str) -> ContextSource:
    body = compile_type(MemoryValue).encode(value(record))
    ref = "memory:" + digest({"scope": record.scope.model_dump(mode="json"), "key": record.key})
    return source(ref, "memory", body, version=str(record.version)).model_copy(
        update={"reason": reason}
    )


def memory_message(record: MemoryRecord) -> ModelMessage:
    return ModelMessage(
        role="user",
        content={"type": "drov.memory", "record": memory_source(record, "retrieved").content},
    )
