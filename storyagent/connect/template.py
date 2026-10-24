"""The ``{placeholder}`` language of the declarative connectors (``provider = "cli"`` / ``"http"``).

A template is a string, a list or a dict that may contain ``{name}`` placeholders. Names are looked up in the *values*
of a job (see ``job_values``): ``{prompt}``, ``{negative_prompt}``, ``{text}``, ``{seed}``, ``{ref}`` (first reference
file), ``{refs.0}``, ``{param.steps}`` or just ``{steps}``, ``{out}`` / ``{work}`` / ``{id}`` where the connector gives
them, and ``{env.NAME}`` (an environment variable: keeps keys out of the config files). ``{name|fallback}`` uses the
fallback (a JSON value or plain text) when the job has no such value.

What the job does not have is *left out*, not guessed: a dict entry or a list item whose placeholder cannot be filled
is dropped; in an argv list a nested list is one optional group (``[["--steps", "{steps}"]]`` vanishes as a whole).
Only a placeholder that is required (``required=True``, e.g. in a URL path) raises ``Missing``.

Two argv forms read the job's files and switches (they are what the command-line wizard writes):
``["--ref-image", "{images.*}"]`` is repeated for every element of ``images`` (and absent when there is none), and
``{ if = "token_reduction", args = ["--token-reduction"] }`` adds its ``args`` only when ``token_reduction`` is on.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*\*?)(?:\|([^{}]*))?\}")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
NOTHING = object()


class Missing(Exception):
    """A required placeholder has no value; ``name`` is the placeholder."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def lookup(values: Mapping[str, Any], name: str) -> Any:
    """``values[name]``, following dots through dicts (keys) and lists (indexes); ``NOTHING`` when absent."""
    if name.startswith("env."):
        return os.environ.get(name[4:], NOTHING)
    cur: Any = values
    for part in name.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        elif isinstance(cur, Sequence) and not isinstance(cur, str) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return NOTHING
    return NOTHING if cur is None else cur


def _fallback(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def render_string(text: str, values: Mapping[str, Any]) -> Any:
    """A string that is exactly one placeholder gives the value itself (a number stays a number); otherwise the
    placeholders are replaced by their text. Raises ``Missing`` for the first placeholder without a value."""
    whole = PLACEHOLDER.fullmatch(text)
    if whole:
        value = lookup(values, whole.group(1))
        if value is NOTHING:
            if whole.group(2) is None:
                raise Missing(whole.group(1))
            return _fallback(whole.group(2))
        return value

    def sub(m: re.Match[str]) -> str:
        value = lookup(values, m.group(1))
        if value is NOTHING:
            if m.group(2) is None:
                raise Missing(m.group(1))
            return m.group(2)
        return value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)

    return PLACEHOLDER.sub(sub, text)


def render(obj: Any, values: Mapping[str, Any], *, required: bool = False) -> Any:
    """Render a nested template. Entries that cannot be filled are dropped unless ``required`` (then ``Missing``)."""
    if isinstance(obj, str):
        return render_string(obj, values)
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            try:
                out[str(key)] = render(value, values, required=required)
            except Missing:
                if required:
                    raise
        return out
    if isinstance(obj, list):
        items: list[Any] = []
        for item in obj:
            try:
                items.append(render(item, values, required=required))
            except Missing:
                if required:
                    raise
        return items
    return obj


def render_argv(argv: Sequence[Any], values: Mapping[str, Any]) -> list[str]:
    """The command line. A string is one argument (a missing placeholder is an error: ``Missing``); a nested list is
    an optional group of arguments that is left out entirely when any of its placeholders is missing."""
    out: list[str] = []
    for item in argv:
        if isinstance(item, str):
            out.append(str(render_string(item, values)))
            continue
        if isinstance(item, Mapping):
            value = lookup(values, str(item.get("if", "")))
            if value is not NOTHING and value not in (False, 0, "", "0", "false", "off", "no"):
                out += [str(render_string(str(x), values)) for x in item.get("args", [])]
            continue
        if isinstance(item, list):
            repeated = [m.group(1)[:-2] for x in item for m in PLACEHOLDER.finditer(str(x))
                        if m.group(1).endswith(".*")]
            if repeated:
                found = lookup(values, repeated[0])
                for i in range(len(found) if isinstance(found, list) else 0):
                    star, nth = "{" + repeated[0] + ".*}", "{" + f"{repeated[0]}.{i}" + "}"
                    out += [str(render_string(str(x).replace(star, nth), values)) for x in item]
                continue
            try:
                out += [str(render_string(str(x), values)) for x in item]
            except Missing:
                continue
            continue
        out.append(str(item))
    return out


def job_values(inputs: Mapping[str, Any], params: Mapping[str, Any], seed: int | None, home: Path,
               extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What a connector template can refer to. Reference files become absolute paths (``refs`` / ``ref``); params win
    over inputs of the same name for the bare ``{name}`` form."""
    refs = []
    for raw in inputs.get("refs", []) or []:
        p = Path(str(raw)).expanduser()
        refs.append(str(p if p.is_absolute() else home / p))
    values: dict[str, Any] = {k: v for k, v in inputs.items() if k != "refs" and v is not None and v != ""}
    values.update({k: v for k, v in params.items() if v is not None})
    values["param"] = dict(params)
    values["refs"] = refs
    if refs:
        values["ref"] = refs[0]
    for role, exts in (("image", IMAGE_EXT), ("audio", AUDIO_EXT), ("video", VIDEO_EXT)):
        files = [r for r in refs if Path(r).suffix.lower() in exts]
        if files:
            values[f"{role}s"] = files
            values.setdefault(role, files[0])
    if seed is not None:
        values["seed"] = seed
    values.update(extra or {})
    return values


def get_path(data: Any, path: str) -> Any:
    """``a.b.0.c`` through dicts and lists; ``None`` when any step is absent. An empty path is the data itself."""
    if not path:
        return data
    cur = data
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur
