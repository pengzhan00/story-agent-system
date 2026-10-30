"""Genre craft notes: how a kind of story is written (``genres.toml``), handed to the writing agents by genre text."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Genre:
    name: str
    aliases: tuple[str, ...]
    guidance: str


@dataclass(frozen=True)
class Genres:
    items: Mapping[str, Genre] = field(default_factory=dict)
    max_matches: int = 2

    def match(self, text: str) -> list[Genre]:
        """Genres whose alias occurs in ``text`` (a project's genre, free text in any language), in file order."""
        low = (text or "").lower()
        if not low.strip():
            return []
        found = [g for g in self.items.values() if any(a in low for a in g.aliases)]
        return found[: self.max_matches]

    def guidance_for(self, text: str) -> str:
        return "\n".join(f"- {g.guidance}" for g in self.match(text))


def build_genres(raw: Mapping[str, Any], error: type[Exception]) -> Genres:
    items: dict[str, Genre] = {}
    for name, spec in raw.get("genres", {}).items():
        aliases = tuple(str(a).strip().lower() for a in spec.get("aliases", ()) if str(a).strip())
        guidance = str(spec.get("guidance", "")).strip()
        if not aliases:
            raise error(f"genres.{name}: aliases is empty (the words that select this genre)")
        if not guidance:
            raise error(f"genres.{name}: guidance is empty")
        items[name] = Genre(name, aliases, guidance)
    limit = raw.get("default", {}).get("max_matches", 2)
    if not isinstance(limit, int) or limit < 1:
        raise error("genres.toml [default] max_matches must be a positive integer")
    return Genres(items, limit)
