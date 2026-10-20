"""A small JSON-Schema subset validator (type, properties, required, items, enum, min/max length) for agent output.

It is deliberately forgiving about extra keys and strict about shape, and it *coerces* the harmless mismatches
models make all the time (a list where a string is wanted, a number as a string) so that one sloppy field does
not throw away a whole script. ``validate`` returns the coerced value and a list of problems that could not be fixed.
"""
from __future__ import annotations

from typing import Any

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,), "array": (list,),
    "object": (dict,), "null": (type(None),),
}


def _coerce(value: Any, want: str) -> Any:
    if want == "string":
        if isinstance(value, list):
            return "\n".join(str(v) for v in value)
        if isinstance(value, int | float | bool):
            return str(value)
        if value is None:
            return ""
    elif want == "integer":
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
    elif want == "number":
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return value
    elif want == "boolean":
        if isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no"):
            return value.strip().lower() in ("true", "yes")
    elif want == "array":
        if value is None:
            return []
        if isinstance(value, str | dict):
            return [value]
    return value


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> tuple[Any, list[str]]:
    problems: list[str] = []
    want = schema.get("type")
    if want:
        value = _coerce(value, want)
        ok = isinstance(value, _TYPES[want]) and not (want in ("integer", "number") and isinstance(value, bool))
        if not ok:
            return value, [f"{path}: expected {want}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            problems.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            value = value[: int(schema["maxLength"])]
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}.{key}: missing")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                value[key], sub_problems = validate(value[key], sub, f"{path}.{key}")
                problems += sub_problems
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            problems.append(f"{path}: needs at least {schema['minItems']} items (has {len(value)})")
        if "items" in schema:
            for i, item in enumerate(value):
                value[i], sub_problems = validate(item, schema["items"], f"{path}[{i}]")
                problems += sub_problems
    return value, problems
