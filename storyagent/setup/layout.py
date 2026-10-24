"""Where things live: the paths of the configuration, optionally moved below one install root.

A step or a check names a path by reference (``tool:cosyvoice.root``, ``model:flux2-klein-base-4b.weights``,
``launch:acestep.cwd``, ``raw:~/pinokio``) instead of writing it out. Without a root a reference is whatever the
configuration says; with ``--root DIR`` every reference a component lists in its ``layout`` is that component's folder
below DIR, and ``overrides`` says which keys of the configuration have to change for that to be true.
"""
from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import storyagent
from storyagent.config import Settings
from storyagent.config.setup import SetupComponent

RECIPES = Path(__file__).resolve().parent / "recipes"
_REF = re.compile(r"\{((?:tool|model|launch):.+?\.\w+)((?:\|dir)*)\}")
_RAW = re.compile(r"\{(raw:[^}]+)\}")
_PLAIN = {"python", "repo", "recipes", "item"}
_PLAIN_RE = re.compile(r"\{(python|repo|recipes|item)\}")


class SetupError(ValueError):
    """A catalog reference that cannot be resolved (carries an i18n-able ``code``)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def repo_root() -> Path:
    return Path(storyagent.__file__).resolve().parents[1]


def tilde(path: Path) -> str:
    """``~/x`` for a path below the home folder, else the absolute path (what the config files carry)."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


@dataclass(frozen=True)
class Override:
    file: str                       # services | models
    path: tuple[str, ...]           # table path + key inside that file
    value: str


class Layout:
    def __init__(self, settings: Settings, root: Path | None = None) -> None:
        self.settings = settings
        self.root = root.expanduser().resolve() if root else None
        self._sub: dict[str, str] = {}
        for comp in settings.setup.components.values():
            self._sub.update(comp.layout)

    # -- references --------------------------------------------------------------------------------
    @staticmethod
    def split(ref: str) -> tuple[str, str, str]:
        """``tool:cosyvoice.root`` -> (``tool``, ``cosyvoice``, ``root``); ``raw:PATH`` -> (``raw``, PATH, ``)``."""
        kind, _, rest = ref.partition(":")
        if kind == "raw":
            return kind, rest, ""
        name, _, key = rest.rpartition(".")
        return kind, name, key

    def configured(self, ref: str) -> Path | None:
        kind, name, key = self.split(ref)
        s = self.settings
        if kind == "raw":
            return Path(name).expanduser()
        if kind == "tool":
            return s.tool_path(name, key)
        if kind == "model":
            spec = s.models.get(name)
            raw = str(spec.extra.get(key, "")) if spec else ""
            return Path(raw).expanduser() if raw else None
        if kind == "launch":
            raw = str(s.launch.get(name, {}).get(key, ""))
            return Path(raw).expanduser() if raw else None
        raise SetupError("bad_ref", ref)

    def path(self, ref: str) -> Path | None:
        if self.root is not None and ref in self._sub:
            return self.root / self._sub[ref]
        return self.configured(ref)

    # -- placeholders ------------------------------------------------------------------------------
    def expand(self, text: str, *, item: str = "") -> str:
        def ref(m: re.Match[str]) -> str:
            p = self.path(m.group(1))
            if p is None:
                raise SetupError("unset_ref", m.group(1))
            for _ in m.group(2).split("|")[1:]:
                p = p.parent
            return str(p)

        def raw(m: re.Match[str]) -> str:
            return str(self.path(m.group(1)))

        def plain(m: re.Match[str]) -> str:
            name = m.group(1)
            if name == "python":
                return sys.executable
            if name == "repo":
                return str(repo_root())
            if name == "recipes":
                return str(RECIPES)
            return item

        out = _PLAIN_RE.sub(plain, _RAW.sub(raw, _REF.sub(ref, text)))
        if "{" in out and re.search(r"\{[a-z]+:", out):
            raise SetupError("bad_placeholder", text)
        return out

    # -- configuration changes a root implies ------------------------------------------------------
    def overrides(self, components: Iterable[SetupComponent]) -> list[Override]:
        if self.root is None:
            return []
        out: list[Override] = []
        for comp in components:
            for ref in comp.layout:
                new = self.path(ref)
                if new is None:
                    continue
                kind, name, key = self.split(ref)
                value = tilde(new)
                if kind == "model":
                    out.append(Override("models", ("models", name, key), value))
                elif kind == "launch":
                    out.append(Override("services", ("launch", name, key), value))
                else:
                    out.append(Override("services", ("tools", name, key), value))
        return out


def nested(overrides: Iterable[Override]) -> dict[str, dict[str, Any]]:
    """``{file: {table: {...}}}`` from flat overrides."""
    out: dict[str, dict[str, Any]] = {}
    for o in overrides:
        node: dict[str, Any] = out.setdefault(o.file, {})
        for part in o.path[:-1]:
            node = node.setdefault(part, {})
        node[o.path[-1]] = o.value
    return out


def merge_into(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            merge_into(base[key], value)
        else:
            base[key] = dict(value) if isinstance(value, Mapping) else value
    return base
