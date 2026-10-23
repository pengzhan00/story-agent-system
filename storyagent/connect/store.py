"""Saving what the operator imported into their own config folder: ``workflows/<name>.json`` and an entry in
``models.toml``. Nothing is overwritten without ``force``, and the packaged defaults are never touched."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storyagent.config import Settings

from .comfy_import import ImportResult, model_entry

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
HEADER = re.compile(r'^\[(models|services)\.(?:"([^"]+)"|([A-Za-z0-9_-]+))(?:\.[^\]]*)?\]\s*$')


class ConnectError(Exception):
    """``code`` is an i18n key suffix: ``connect.error.<code>``."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class Saved:
    workflow_path: Path
    models_path: Path
    entry: str


def workflows_dir(settings: Settings) -> Path:
    return settings.config_dir / "workflows"


def models_file(settings: Settings) -> Path:
    return settings.config_dir / "models.toml"


def without_table(text: str, root: str, name: str) -> tuple[str, bool]:
    """``text`` minus every table of ``[<root>."name"...]`` (the block ends at the next other header)."""
    kept: list[str] = []
    skipping = removed = False
    for line in text.splitlines(keepends=True):
        m = HEADER.match(line.strip())
        if m:
            skipping = m.group(1) == root and (m.group(2) or m.group(3)) == name
            removed = removed or skipping
        elif line.lstrip().startswith("[") and line.strip().endswith("]"):
            skipping = False
        if not skipping:
            kept.append(line)
    return "".join(kept).rstrip("\n") + ("\n" if kept else ""), removed


def save_comfy(settings: Settings, *, name: str, ref: str, kind: str, result: ImportResult, family: str = "",
               heavy: bool = False, timeout_sec: int = 1800, force: bool = False) -> Saved:
    if not NAME.match(name):
        raise ConnectError("bad_name", name=name)
    if not REF.match(ref):
        raise ConnectError("bad_ref", ref=ref)
    target = workflows_dir(settings) / f"{name}.json"
    models = models_file(settings)
    current = models.read_text("utf-8") if models.is_file() else ""
    mine = without_table(current, "models", ref)[1]
    if ref in settings.models and not mine:
        raise ConnectError("ref_packaged", ref=ref)
    if (mine or target.exists()) and not force:
        raise ConnectError("exists", ref=ref, name=name)
    entry = model_entry(ref, kind, name, result, family=family, heavy=heavy, timeout_sec=timeout_sec)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result.template, ensure_ascii=False, indent=1) + "\n", "utf-8")
    base = without_table(current, "models", ref)[0] if current else ""
    models.parent.mkdir(parents=True, exist_ok=True)
    models.write_text((base + "\n" if base else "") + entry, "utf-8")
    return Saved(target, models, entry)


def describe(result: ImportResult) -> list[dict[str, Any]]:
    """Rows for a table: binding name, where it lands, the workflow's own value."""
    return [{"name": d.name, "targets": [f"{n}.{i}" for n, i in d.targets], "classes": list(d.classes),
             "value": d.value} for d in result.detected]
