"""Strict JSON boundaries shared by wire models and tool contracts."""

import json
import math
from typing import Annotated, Any, TypeAlias, cast

from pydantic import BeforeValidator, JsonValue


def check_json(value: object) -> object:
    """Reject coercible Python objects, non-string keys and non-finite numbers."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        for item in value:
            check_json(item)
        return value
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            check_json(item)
        return value
    raise ValueError("Expected a finite JSON value")


StrictJson: TypeAlias = Annotated[JsonValue, BeforeValidator(check_json)]


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("Non-finite JSON number")


def load_json(data: str | bytes, *, max_bytes: int | None = None) -> JsonValue:
    if (
        max_bytes is not None
        and len(data.encode("utf-8") if isinstance(data, str) else data) > max_bytes
    ):
        raise ValueError("JSON exceeds byte limit")
    value = json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
    check_json(value)
    return cast(JsonValue, value)


def canonical_json(value: object) -> str:
    check_json(value)
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
