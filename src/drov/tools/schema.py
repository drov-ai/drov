"""Closed JSON schemas and strict codecs for the supported Python subset."""

import dataclasses
import inspect
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel, JsonValue, TypeAdapter
from pydantic_core import PydanticUndefined

from drov._json import canonical_json, check_json, load_json
from drov.errors import UnsupportedSchema
from drov.types import Effect, RunContext, ScopedIdentifier


@dataclass(frozen=True, kw_only=True)
class ToolContext:
    tenant_id: str
    user_id: str
    run_id: str
    operation_id: str
    deps: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from drov.types import Identifier, RunRef

        RunContext(tenant_id=self.tenant_id, user_id=self.user_id)
        RunRef(run_id=self.run_id, state_version=1)
        TypeAdapter(Identifier).validate_python(self.operation_id, strict=True)
        object.__setattr__(self, "deps", types.MappingProxyType(dict(self.deps)))


@dataclass(frozen=True)
class JsonCodec:
    python_type: Any
    schema: dict[str, Any]
    children: Mapping[str, "JsonCodec"] = field(default_factory=dict)
    item: "JsonCodec | None" = None
    nullable: bool = False
    required: frozenset[str] = frozenset()

    def decode(self, value: JsonValue) -> Any:
        check_json(value)
        target = self.python_type
        if self.nullable:
            if value is None:
                return None
            assert self.item is not None
            return self.item.decode(value)
        if target in (str, bool, int, float, type(None)):
            allowed = (int, float) if target is float else (target,)
            if type(value) not in allowed:
                raise ValueError("JSON value has the wrong scalar type")
            return float(cast(int | float, value)) if target is float else value
        if isinstance(target, type) and issubclass(target, Enum):
            for member in target:
                if type(value) is type(member.value) and value == member.value:
                    return member
            raise ValueError("Value is not in the enum")
        if get_origin(target) is Literal:
            if not any(type(value) is type(item) and value == item for item in get_args(target)):
                raise ValueError("Value is not in the literal enum")
            return value
        if get_origin(target) is list:
            if type(value) is not list:
                raise ValueError("Expected JSON array")
            assert self.item is not None
            return [self.item.decode(item) for item in value]
        if get_origin(target) is dict:
            if type(value) is not dict:
                raise ValueError("Expected JSON object")
            assert self.item is not None
            return {key: self.item.decode(item) for key, item in value.items()}
        if type(value) is not dict:
            raise ValueError("Expected JSON object")
        if set(value) - self.children.keys() or self.required - value.keys():
            raise ValueError("Object contains extra fields or is missing required fields")
        decoded = {key: self.children[key].decode(item) for key, item in value.items()}
        if issubclass(target, BaseModel):
            result = target.model_validate(decoded, strict=True)
        else:
            result = target(**decoded)
        # Defaults participate in validation too, even if user models do not validate them.
        self.encode(result)
        return result

    def encode(self, value: Any) -> JsonValue:
        target = self.python_type
        if self.nullable:
            if value is None:
                return None
            assert self.item is not None
            return self.item.encode(value)
        if isinstance(target, type) and issubclass(target, Enum):
            if not isinstance(value, target):
                raise ValueError("Expected enum instance")
            return self.decode(value.value).value  # type: ignore[no-any-return]
        if self.children or (
            isinstance(target, type)
            and (issubclass(target, BaseModel) or dataclasses.is_dataclass(target))
        ):
            if type(value) is not target:
                raise ValueError("Expected declared object type")
            result = {
                name: codec.encode(getattr(value, name)) for name, codec in self.children.items()
            }
            if issubclass(target, BaseModel):
                target.model_validate(value.model_dump(mode="python"), strict=True)
            return result
        if get_origin(target) in (list, dict):
            expected = get_origin(target)
            if type(value) is not expected:
                raise ValueError("Expected declared container type")
            assert self.item is not None
            if expected is list:
                result_list = [self.item.encode(item) for item in value]
                check_json(result_list)
                return result_list
            check_json({key: None for key in value})
            return {key: self.item.encode(item) for key, item in value.items()}
        return self.decode(value)  # type: ignore[no-any-return]

    def parse(self, data: str | bytes, *, max_bytes: int = 65536) -> Any:
        return self.decode(load_json(data, max_bytes=max_bytes))


def _enum_schema(values: tuple[Any, ...]) -> dict[str, Any]:
    if not values or any(
        type(value) not in (str, int, bool, float, type(None)) for value in values
    ):
        raise UnsupportedSchema("Enum must contain finite JSON scalars")
    for value in values:
        check_json(value)
    if len({type(value) for value in values}) != 1:
        raise UnsupportedSchema("Enum must use a single JSON scalar type")
    kind = {str: "string", int: "integer", bool: "boolean", float: "number", type(None): "null"}
    return {"type": kind[type(values[0])], "enum": list(values)}


def _merge_constraints(base: dict[str, Any], constrained: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key in (
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
    ):
        if key in constrained:
            result[key] = constrained[key]
    if "anyOf" in base and "anyOf" in constrained:
        if len(base["anyOf"]) != len(constrained["anyOf"]):
            raise UnsupportedSchema("Field constraint changed the nullable shape")
        result["anyOf"] = [
            _merge_constraints(left, right)
            for left, right in zip(base["anyOf"], constrained["anyOf"], strict=True)
        ]
    return result


def compile_type(
    annotation: Any, *, allow_dict: bool = False, _stack: tuple[Any, ...] = ()
) -> JsonCodec:
    if annotation in _stack:
        raise UnsupportedSchema("Recursive types are not supported in v0.1")
    scalar = {str: "string", int: "integer", float: "number", bool: "boolean", type(None): "null"}
    if annotation in scalar:
        return JsonCodec(annotation, {"type": scalar[annotation]})
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType):
        non_null = [arg for arg in args if arg is not type(None)]
        if len(args) != 2 or len(non_null) != 1:
            raise UnsupportedSchema("Only nullable unions are supported")
        inner = compile_type(non_null[0], allow_dict=allow_dict, _stack=_stack)
        return JsonCodec(
            annotation, {"anyOf": [inner.schema, {"type": "null"}]}, item=inner, nullable=True
        )
    if origin is Literal:
        return JsonCodec(annotation, _enum_schema(args))
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return JsonCodec(annotation, _enum_schema(tuple(member.value for member in annotation)))
    if origin is list and len(args) == 1:
        inner = compile_type(args[0], allow_dict=allow_dict, _stack=_stack)
        return JsonCodec(annotation, {"type": "array", "items": inner.schema}, item=inner)
    if origin is dict and len(args) == 2 and args[0] is str and allow_dict:
        inner = compile_type(args[1], allow_dict=True, _stack=_stack)
        return JsonCodec(
            annotation, {"type": "object", "additionalProperties": inner.schema}, item=inner
        )
    is_model = isinstance(annotation, type) and issubclass(annotation, BaseModel)
    if not isinstance(annotation, type) or not (is_model or dataclasses.is_dataclass(annotation)):
        raise UnsupportedSchema("Type is outside the supported JSON subset")
    try:
        hints = get_type_hints(annotation, include_extras=True)
    except (NameError, TypeError) as exc:
        raise UnsupportedSchema("Type annotations could not be resolved") from exc
    children: dict[str, JsonCodec] = {}
    required: set[str] = set()
    properties: dict[str, Any] = {}
    if is_model:
        model_type = cast(type[BaseModel], annotation)
        if model_type.__pydantic_root_model__:
            raise UnsupportedSchema("RootModel does not declare an object shape")
        if model_type.model_config.get("json_schema_extra") is not None:
            raise UnsupportedSchema("Custom model schema is not supported")
        decorators = model_type.__pydantic_decorators__
        if any(
            (
                decorators.validators,
                decorators.field_validators,
                decorators.root_validators,
                decorators.model_validators,
                decorators.field_serializers,
                decorators.model_serializers,
            )
        ):
            raise UnsupportedSchema(
                "Custom validators/serializers require a separate tool boundary"
            )
        if model_type.model_computed_fields:
            raise UnsupportedSchema("Computed fields are not supported")
        if (
            model_type.__pydantic_custom_init__
            or model_type.model_post_init is not BaseModel.model_post_init
        ):
            raise UnsupportedSchema("Custom model construction is not supported")
        fields: dict[str, Any] = dict(model_type.model_fields)
    else:
        if any(isinstance(hint, dataclasses.InitVar) for hint in hints.values()):
            raise UnsupportedSchema("InitVar dataclass fields are not supported")
        if hasattr(annotation, "__post_init__"):
            raise UnsupportedSchema("Dataclass construction hooks are not supported")
        fields = {item.name: item for item in dataclasses.fields(annotation)}
        if any(not item.init for item in fields.values()):
            raise UnsupportedSchema("Non-init dataclass fields are not supported")
    for name, info in fields.items():
        if is_model and (info.alias or info.validation_alias or info.serialization_alias):
            raise UnsupportedSchema("Aliased object fields are not supported")
        if is_model and (info.json_schema_extra is not None or info.exclude or info.exclude_if):
            raise UnsupportedSchema("Custom field schema or exclusion is not supported")
        field_type = info.annotation if is_model else hints[name]
        child = compile_type(field_type, allow_dict=allow_dict, _stack=(*_stack, annotation))
        children[name] = child
        schema = dict(child.schema)
        if is_model:
            if info.is_required():
                required.add(name)
            if info.metadata:
                for metadata in info.metadata:
                    module = type(metadata).__module__
                    basic_constraint = module == "annotated_types" and type(metadata).__name__ in {
                        "Ge",
                        "Gt",
                        "Le",
                        "Lt",
                        "MultipleOf",
                        "MinLen",
                        "MaxLen",
                    }
                    if not basic_constraint and not (
                        module == "pydantic._internal._fields"
                        and set(vars(metadata)) <= {"pattern"}
                        and isinstance(getattr(metadata, "pattern", None), str)
                    ):
                        raise UnsupportedSchema("Unsupported field validation metadata")
                constrained = TypeAdapter(info.rebuild_annotation()).json_schema()
                accepted = {
                    "type",
                    "title",
                    "description",
                    "default",
                    "anyOf",
                    "$defs",
                    "$ref",
                    "enum",
                    "const",
                    "minimum",
                    "maximum",
                    "exclusiveMinimum",
                    "exclusiveMaximum",
                    "multipleOf",
                    "minLength",
                    "maxLength",
                    "pattern",
                    "minItems",
                    "maxItems",
                    "items",
                    "properties",
                    "required",
                    "additionalProperties",
                }
                if set(constrained) - accepted:
                    raise UnsupportedSchema("Unsupported object field constraint")
                schema = _merge_constraints(schema, constrained)
            default = info.default
            if default is not PydanticUndefined:
                schema["default"] = child.encode(default)
            if info.description:
                schema["description"] = info.description
        else:
            if info.default is dataclasses.MISSING and info.default_factory is dataclasses.MISSING:
                required.add(name)
            if info.default is not dataclasses.MISSING:
                schema["default"] = child.encode(info.default)
        properties[name] = schema
    return JsonCodec(
        annotation,
        {
            "type": "object",
            "properties": properties,
            "required": sorted(required),
            "additionalProperties": False,
        },
        children=children,
        required=frozenset(required),
    )


def compile_output(output_type: Any) -> JsonCodec:
    codec = compile_type(output_type)
    if output_type is not str and codec.schema.get("type") != "object":
        raise UnsupportedSchema("Final output must be str or a declared object type")
    return codec


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Generic closed-object export; provider-specific support is checked by its adapter."""
    supported = {
        "type",
        "enum",
        "properties",
        "items",
        "required",
        "additionalProperties",
        "anyOf",
        "description",
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
    }
    if set(schema) - supported:
        raise UnsupportedSchema("Unsupported strict schema keyword")
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "default":
            continue
        if key == "properties":
            result[key] = {name: strict_schema(child) for name, child in value.items()}
        elif key == "items":
            result[key] = strict_schema(value)
        elif key == "anyOf":
            result[key] = [strict_schema(child) for child in value]
        else:
            result[key] = value
    if schema.get("type") == "object":
        if schema.get("additionalProperties") is not False:
            raise UnsupportedSchema(
                "Dynamic dictionaries cannot be exported as closed strict objects"
            )
        result["required"] = sorted(schema["properties"])
    if "default" in schema:
        note = "Default: " + canonical_json(schema["default"]) + "; provide explicitly."
        result["description"] = (schema.get("description", "") + " " + note).strip()
    return result


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    revision: str
    description: str
    effect: Effect
    approval: Literal["required", "policy"]
    parameters: dict[str, Any]
    result_codec: JsonCodec
    argument_codecs: Mapping[str, JsonCodec]
    defaults: Mapping[str, Any]
    context_parameter: str | None

    def bind_arguments(
        self, data: str | bytes, *, context: ToolContext, max_bytes: int = 65536
    ) -> dict[str, object]:
        value = load_json(data, max_bytes=max_bytes)
        if not isinstance(value, dict) or set(value) - self.argument_codecs.keys():
            raise ValueError("Tool arguments contain extra or injected fields")
        result: dict[str, object] = {}
        for name, codec in self.argument_codecs.items():
            if name in value:
                result[name] = codec.decode(value[name])
            elif name in self.defaults:
                # Encode/decode makes a fresh validated default, not a shared mutable object.
                result[name] = codec.decode(codec.encode(self.defaults[name]))
            else:
                raise ValueError("Tool arguments are missing a required field")
        if self.context_parameter is not None:
            result[self.context_parameter] = context
        return result


def compile_tool(
    function: Callable[..., Any],
    *,
    name: str,
    revision: str,
    effect: Effect = "unknown",
    approval: Literal["required", "policy"] = "policy",
    description: str | None = None,
) -> ToolDefinition:
    TypeAdapter(ScopedIdentifier).validate_python(name, strict=True)
    TypeAdapter(ScopedIdentifier).validate_python(revision, strict=True)
    if effect not in ("read", "write", "unknown") or approval not in ("required", "policy"):
        raise UnsupportedSchema("Invalid tool effect or approval")
    try:
        hints = get_type_hints(function, include_extras=True)
        signature = inspect.signature(function)
    except (NameError, TypeError, ValueError) as exc:
        raise UnsupportedSchema("Tool annotations could not be resolved") from exc
    codecs: dict[str, JsonCodec] = {}
    defaults: dict[str, Any] = {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    context_name = None
    for parameter in signature.parameters.values():
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise UnsupportedSchema(
                "Positional-only and variadic tool parameters are not supported"
            )
        annotation = hints.get(parameter.name)
        if annotation is ToolContext:
            if context_name is not None or parameter.default is not inspect.Parameter.empty:
                raise UnsupportedSchema("Exactly one required ToolContext parameter is permitted")
            context_name = parameter.name
            continue
        codec = compile_type(annotation)
        codecs[parameter.name] = codec
        schema = dict(codec.schema)
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter.name)
        else:
            defaults[parameter.name] = parameter.default
            schema["default"] = codec.encode(parameter.default)
        properties[parameter.name] = schema
    result = compile_type(hints.get("return"), allow_dict=True)
    return ToolDefinition(
        name,
        revision,
        description if description is not None else inspect.getdoc(function) or "",
        effect,
        approval,
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        result,
        codecs,
        defaults,
        context_name,
    )
