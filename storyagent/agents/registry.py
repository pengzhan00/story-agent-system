"""Agent specs: packaged ``specs/<name>.toml`` plus your own ``$STORY_AGENT_HOME/config/agents/<name>.toml``.

A user file with the same ``name`` overrides only the keys it sets (prompt, system, role, temperature, ...), so
tuning a prompt means editing one small file. ``schema = "<file>"`` names ``schemas/<file>.json`` (packaged or in
``config/agents/schemas/``). Placeholders are ``$name`` / ``${name}`` (``string.Template``): braces stay free for JSON
examples. Every placeholder used must be declared in ``inputs``.
"""
from __future__ import annotations

import json
import string
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storyagent.config import Settings

SPEC_DIR = Path(__file__).resolve().parent / "specs"
_KNOWN = {"name", "role", "system", "prompt", "temperature", "max_tokens", "schema", "inputs", "attempts", "model",
          "description"}


class AgentSpecError(ValueError):
    pass


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str
    system: str
    prompt: str
    inputs: tuple[str, ...]
    temperature: float = 0.7
    max_tokens: int = 4096
    schema: dict[str, Any] | None = None
    attempts: int = 3
    model: str = ""            # pin a model for this agent regardless of the role routing
    description: str = ""
    source: str = field(default="", compare=False)


def _placeholders(text: str) -> set[str]:
    return {m.group("named") or m.group("braced") for m in string.Template.pattern.finditer(text)
            if (m.group("named") or m.group("braced"))}


def _read(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise AgentSpecError(f"invalid TOML in {path}: {exc}") from exc


def _schema(name: str, dirs: list[Path]) -> dict[str, Any]:
    for d in dirs:
        f = d / f"{name}.json"
        if f.is_file():
            try:
                value: dict[str, Any] = json.loads(f.read_text("utf-8"))
            except ValueError as exc:
                raise AgentSpecError(f"invalid JSON schema {f}: {exc}") from exc
            return value
    raise AgentSpecError(f"unknown schema {name!r}")


def load_specs(settings: Settings | None = None) -> dict[str, AgentSpec]:
    user_dir = (settings.config_dir / "agents") if settings is not None else None
    raw: dict[str, dict[str, Any]] = {}
    origin: dict[str, str] = {}
    sources: list[tuple[Path | None, str]] = [(SPEC_DIR, "packaged"), (user_dir, "user")]
    for d, label in sources:
        if d is None or not d.is_dir():
            continue
        for f in sorted(d.glob("*.toml")):
            data = _read(f)
            name = data.get("name") or f.stem
            unknown = set(data) - _KNOWN
            if unknown:
                raise AgentSpecError(f"{f.name}: unknown keys {sorted(unknown)}")
            if label == "user" and name not in raw:
                raise AgentSpecError(f"{f.name}: overrides unknown agent {name!r}")
            raw[name] = {**raw.get(name, {}), **data, "name": name}
            origin[name] = label if name not in origin else f"{origin[name]}+{label}"
    schema_dirs = ([user_dir / "schemas"] if user_dir else []) + [SPEC_DIR / "schemas"]
    out: dict[str, AgentSpec] = {}
    for name, entry in raw.items():
        for key in ("role", "system", "prompt", "inputs"):
            if key not in entry:
                raise AgentSpecError(f"agent {name!r}: {key} is required")
        inputs = tuple(entry["inputs"])
        used = _placeholders(entry["prompt"]) | _placeholders(entry["system"])
        if used - set(inputs):
            raise AgentSpecError(f"agent {name!r}: placeholders not declared in inputs: {sorted(used - set(inputs))}")
        out[name] = AgentSpec(
            name=name, role=str(entry["role"]), system=str(entry["system"]), prompt=str(entry["prompt"]), inputs=inputs,
            temperature=float(entry.get("temperature", 0.7)), max_tokens=int(entry.get("max_tokens", 4096)),
            schema=_schema(entry["schema"], schema_dirs) if entry.get("schema") else None,
            attempts=int(entry.get("attempts", 3)), model=str(entry.get("model", "")),
            description=str(entry.get("description", "")), source=origin[name],
        )
    return out
