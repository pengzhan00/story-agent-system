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
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
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


EDITABLE = ("role", "system", "prompt", "temperature", "max_tokens", "attempts", "model")


def user_file(settings: Settings, name: str) -> Path:
    return settings.config_dir / "agents" / f"{name}.toml"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)     # JSON string escapes are valid TOML basic strings


def write_override(settings: Settings, name: str, changes: dict[str, Any]) -> AgentSpec:
    """Merge ``changes`` into the user's override file for agent ``name`` and return the resulting spec.

    The result is validated by loading every spec again; on any error the previous file content is restored and
    ``AgentSpecError`` is raised, so a bad edit can never leave the pipeline unable to start.
    """
    if name not in load_specs(settings):
        raise AgentSpecError(f"unknown agent {name!r}")
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise AgentSpecError(f"cannot edit {sorted(unknown)}; editable: {list(EDITABLE)}")
    path = user_file(settings, name)
    before = path.read_text("utf-8") if path.is_file() else None
    current = _read(path) if before is not None else {}
    merged = {**current, **{k: v for k, v in changes.items() if v is not None}, "name": name}
    lines = [f"{k} = {_toml_value(v)}" for k, v in merged.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        return load_specs(settings)[name]
    except AgentSpecError:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(before, encoding="utf-8")
        raise


def remove_override(settings: Settings, name: str) -> AgentSpec:
    """Back to the packaged prompt."""
    user_file(settings, name).unlink(missing_ok=True)
    return load_specs(settings)[name]


def packaged_spec(name: str) -> AgentSpec:
    return load_specs(None)[name]


def with_overrides(specs: Mapping[str, AgentSpec], overrides: Mapping[str, Any]) -> dict[str, AgentSpec]:
    """A project's own edits to some agent prompts: ``name -> {system?, prompt?, temperature?, max_tokens?}``.
    Anything else is ignored, and an unknown agent name is skipped (the packaged spec stays)."""
    out = dict(specs)
    for name, patch in overrides.items():
        spec = out.get(str(name))
        if spec is None or not isinstance(patch, Mapping):
            continue
        allowed = {k: v for k, v in patch.items() if k in ("system", "prompt", "temperature", "max_tokens")}
        if allowed:
            out[str(name)] = replace(spec, **allowed)
    return out
