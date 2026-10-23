"""Wiki configuration (``wiki.toml``): where the pages come from. Nothing is read until the operator runs a sync."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WikiSource:
    name: str
    path: Path
    include: tuple[str, ...] = ()       # globs relative to ``path``; a note matching none of them is not read
    exclude: tuple[str, ...] = ()
    restricted: tuple[str, ...] = ()    # notes that get ``rating`` (hidden like adult content)
    rating: str = "restricted"          # the rating those notes get; must be a rating of content.toml


@dataclass(frozen=True)
class WikiConfig:
    docs: bool = True                   # the repository's own docs/ folder becomes the ``docs`` source
    sources: tuple[WikiSource, ...] = field(default_factory=tuple)


def build_wiki(raw: Mapping[str, Any], fail: Callable[[str], Exception]) -> WikiConfig:
    wiki = dict(raw.get("wiki", {}))
    sources: list[WikiSource] = []
    seen: set[str] = {"docs"}
    for item in wiki.get("sources", []):
        name = str(item.get("name", "")).strip()
        if not name or "/" in name:
            raise fail(f"[[wiki.sources]] needs a name without '/': {name!r}")
        if name in seen:
            raise fail(f"[[wiki.sources]] name used twice (or reserved): {name!r}")
        seen.add(name)
        path = str(item.get("path", "")).strip()
        if not path:
            raise fail(f"[[wiki.sources]] {name}: path is empty")
        sources.append(WikiSource(
            name=name, path=Path(path).expanduser(), include=tuple(str(x) for x in item.get("include", ())),
            exclude=tuple(str(x) for x in item.get("exclude", ())),
            restricted=tuple(str(x) for x in item.get("restricted", ())),
            rating=str(item.get("rating", "restricted"))))
    return WikiConfig(docs=bool(wiki.get("docs", True)), sources=tuple(sources))
