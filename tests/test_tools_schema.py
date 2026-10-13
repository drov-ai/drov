from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

import pytest
from pydantic import AfterValidator, BaseModel, Field, RootModel, ValidationError, field_validator

from drov.errors import UnsupportedSchema
from drov.tools.schema import ToolContext, compile_output, compile_tool, compile_type, strict_schema


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class Item(BaseModel):
    name: str
    count: int = Field(ge=1)
    color: Color = Color.RED


@dataclass
class Report:
    title: str
    items: list[Item]
    note: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Recursive:
    children: list["Recursive"]


def query(item: Item, ctx: ToolContext, limit: int = 5, note: str | None = None) -> Report:
    raise AssertionError("T-02 must not execute a tool")


def context() -> ToolContext:
    return ToolContext(
        tenant_id="tenant",
        user_id="user",
        run_id="run",
        operation_id="op",
        deps={"service": object()},
    )


def test_schema_injection_and_default_export() -> None:
    definition = compile_tool(query, name="query", revision="1", effect="read")
    assert "ctx" not in definition.parameters["properties"]
    assert "deps" not in str(definition.parameters)
    assert definition.parameters["required"] == ["item"]
    strict = strict_schema(definition.parameters)
    assert strict["required"] == ["item", "limit", "note"]
    assert "default" not in strict["properties"]["limit"]
    assert "5" in strict["properties"]["limit"]["description"]
    assert strict["properties"]["item"]["additionalProperties"] is False
    bound = definition.bind_arguments('{"item":{"name":"x","count":1}}', context=context())
    assert isinstance(bound["item"], Item)
    assert bound["limit"] == 5 and bound["note"] is None
    assert isinstance(bound["ctx"], ToolContext)
    with pytest.raises(TypeError):
        context().deps["extra"] = object()  # type: ignore[index]


@pytest.mark.parametrize(
    "arguments",
    [
        '{"item":{"name":"x","count":"1"}}',
        '{"item":{"name":"x","count":true}}',
        '{"item":{"name":"x","count":0}}',
        '{"item":{"name":"x","count":1,"extra":1}}',
        '{"item":{"name":"x","count":1},"ctx":{}}',
        '{"item":{"name":"x","count":1},"tenant_id":"other"}',
        '{"item":{"name":"x","count":1},"limit":null}',
        '{"item":{"name":"x","count":1},"item":{}}',
        "{}",
    ],
)
def test_arguments_reject_coercion_extra_fields_and_injection(arguments: str) -> None:
    definition = compile_tool(query, name="query", revision="1")
    with pytest.raises((ValueError, ValidationError)):
        definition.bind_arguments(arguments, context=context())


def test_nested_output_codec_and_result_boundary() -> None:
    codec = compile_output(Report)
    parsed = codec.parse('{"title":"r","items":[{"name":"a","count":1,"color":"blue"}]}')
    assert isinstance(parsed, Report)
    assert parsed.items[0].color is Color.BLUE
    assert codec.encode(parsed) == {
        "title": "r",
        "items": [{"name": "a", "count": 1, "color": "blue"}],
        "note": None,
        "tags": [],
    }
    for data in (
        '{"title":"r","items":[],"extra":1}',
        '{"title":"r","items":[{"name":"a","count":1,"color":"invalid"}]}',
    ):
        with pytest.raises(ValueError):
            codec.parse(data)
    with pytest.raises(ValueError):
        codec.encode({"title": "r", "items": []})
    result = compile_type(dict[str, list[int]], allow_dict=True)
    assert result.parse('{"ids":[1,2]}') == {"ids": [1, 2]}
    with pytest.raises(ValueError):
        result.encode({1: [1]})
    with pytest.raises(UnsupportedSchema):
        strict_schema(result.schema)


@pytest.mark.parametrize(
    "annotation",
    [
        Any,
        datetime,
        bytes,
        object,
        tuple[str, int],
        set[str],
        dict[str, int],
        dict[int, str],
        str | int,
        "Unknown",
        list[Any],
    ],
)
def test_unsupported_types_rejected(annotation: Any) -> None:
    with pytest.raises(UnsupportedSchema):
        compile_type(annotation)


@pytest.mark.parametrize("annotation", [int, float, list[str], dict[str, str], str | None])
def test_final_output_requires_string_or_object(annotation: Any) -> None:
    with pytest.raises(UnsupportedSchema):
        compile_output(annotation)


def test_tool_signature_rejections() -> None:
    def positional(value: str, /) -> str:
        raise AssertionError

    def variadic(*values: str) -> str:
        raise AssertionError

    def keyword_variadic(**values: str) -> str:
        raise AssertionError

    def duplicate_context(first: ToolContext, second: ToolContext) -> str:
        raise AssertionError

    def missing_annotation(value: Any) -> Any:
        raise AssertionError

    def unresolved(value: "MissingType") -> str:  # type: ignore[name-defined]  # noqa: F821
        raise AssertionError

    for function in (
        positional,
        variadic,
        keyword_variadic,
        duplicate_context,
        missing_annotation,
        unresolved,
    ):
        with pytest.raises(UnsupportedSchema):
            compile_tool(function, name="invalid", revision="1")


def test_defaults_are_validated_and_not_shared() -> None:
    def function(values: list[str] = ["a"]) -> str:  # noqa: B006
        raise AssertionError

    definition = compile_tool(function, name="defaults", revision="1")
    first = definition.bind_arguments("{}", context=context())
    second = definition.bind_arguments("{}", context=context())
    assert first["values"] == second["values"]
    assert first["values"] is not second["values"]
    with pytest.raises(ValueError):
        compile_type(float).parse("NaN")
    with pytest.raises(ValueError):
        compile_type(int).decode(True)


def test_unsupported_pydantic_features_are_not_silently_exported() -> None:
    class Aliased(BaseModel):
        value: str = Field(alias="other")

    class Custom(BaseModel):
        value: str

        @field_validator("value")
        @classmethod
        def change(cls, value: str) -> str:
            return value.upper()

    for model in (Aliased, Custom):
        with pytest.raises(UnsupportedSchema):
            compile_output(model)
    with pytest.raises(UnsupportedSchema):
        strict_schema({"allOf": [{"type": "string"}]})


def test_nested_constraints_and_construction_hooks_are_rejected() -> None:
    class NestedConstraint(BaseModel):
        values: list[Annotated[int, Field(ge=0)]]

    class HiddenValidator(BaseModel):
        value: Annotated[int, AfterValidator(lambda value: value + 1)]

    class ExtraSchema(BaseModel):
        value: int = Field(json_schema_extra={"unknown_constraint": True})

    @dataclass
    class Hooked:
        value: int

        def __post_init__(self) -> None:
            raise AssertionError("Must be rejected before construction")

    for annotation in (NestedConstraint, HiddenValidator, ExtraSchema, Hooked, RootModel[str]):
        with pytest.raises(UnsupportedSchema):
            compile_output(annotation)


def test_declared_field_constraints_are_preserved_and_validated() -> None:
    class Constrained(BaseModel):
        code: str = Field(pattern=r"^[a-z]+$", min_length=2, max_length=4)
        values: list[int] = Field(min_length=1, max_length=3)

    codec = compile_output(Constrained)
    assert codec.schema["properties"]["code"]["pattern"] == r"^[a-z]+$"
    assert codec.schema["properties"]["values"]["minItems"] == 1
    assert codec.parse('{"code":"ab","values":[1]}').code == "ab"
    with pytest.raises(ValueError):
        codec.parse('{"code":"A","values":[]}')


def test_nullable_constraints_remain_in_schema() -> None:
    class Nullable(BaseModel):
        count: int | None = Field(ge=1)

    codec = compile_output(Nullable)
    assert codec.schema["properties"]["count"]["anyOf"][0]["minimum"] == 1
    assert codec.parse('{"count":null}').count is None
    with pytest.raises(ValueError):
        codec.parse('{"count":0}')


def test_recursive_schema_is_rejected_before_parsing() -> None:
    with pytest.raises(UnsupportedSchema, match="Recursive"):
        compile_output(Recursive)
