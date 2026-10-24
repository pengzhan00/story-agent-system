"""A small TOML writer for the configuration files the guide produces (``tomllib`` only reads).

Handles what configuration files hold: strings, booleans, numbers, lists of those, nested tables and lists of tables.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE.match(k) else json.dumps(k, ensure_ascii=False)


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, Sequence) and not isinstance(v, str | bytes):
        return "[" + ", ".join(_scalar(x) for x in v) + "]"
    if isinstance(v, Mapping):
        return "{ " + ", ".join(f"{_key(str(k))} = {_scalar(x)}" for k, x in v.items()) + " }"
    return json.dumps(str(v), ensure_ascii=False)


def _is_table_list(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, Mapping) for x in v)


def _emit(path: tuple[str, ...], table: Mapping[str, Any], out: list[str], *, array: bool = False) -> None:
    plain = {k: v for k, v in table.items() if not isinstance(v, Mapping) and not _is_table_list(v)}
    subs = {k: v for k, v in table.items() if isinstance(v, Mapping)}
    arrays = {k: v for k, v in table.items() if _is_table_list(v)}
    if path and (plain or array or not (subs or arrays)):
        out.append(("[[" if array else "[") + ".".join(_key(p) for p in path) + ("]]" if array else "]"))
    for k, v in plain.items():
        out.append(f"{_key(str(k))} = {_scalar(v)}")
    if plain or array:
        out.append("")
    for k, v in subs.items():
        _emit((*path, str(k)), v, out)
    for k, items in arrays.items():
        for item in items:
            _emit((*path, str(k)), item, out, array=True)


def dumps(data: Mapping[str, Any]) -> str:
    out: list[str] = []
    _emit((), data, out)
    return "\n".join(out).strip() + "\n"
