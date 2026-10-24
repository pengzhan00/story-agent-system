"""The wiki: pages copied in from the repository's docs and from the notes folders listed in ``wiki.toml``.

``WikiStore.sync`` reads (never writes) the sources, turns each note into plain Markdown (``obsidian.convert``), and
keeps ``wiki_pages`` / ``wiki_links`` in step with them: new and changed notes are stored, notes that
are gone (or no longer allowed by the include list) are removed. A note is read only when an ``include`` glob of its
source names it, so a vault full of unrelated notes stays unread.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shutil
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import storyagent
from storyagent.config import Settings
from storyagent.config.wiki import WikiSource
from storyagent.db import Database

from . import obsidian

DOCS_SOURCE = "docs"
MAX_NOTE_BYTES = 2 * 1024 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024
SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}
GENERAL = "general"


@dataclass
class Page:
    slug: str
    source: str
    path: str
    title: str
    rating: str
    tags: list[str]
    summary: str
    body: str
    meta: dict[str, Any]
    synced_at: str = ""
    source_mtime: str | None = None


@dataclass
class _Entry:
    source: str
    rel: str
    file: Path
    rating: str
    root: Path


@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"added": self.added, "updated": self.updated, "unchanged": self.unchanged, "removed": self.removed,
                "sources": dict(self.sources), "warnings": list(self.warnings)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_page(r: Any) -> Page:
    return Page(slug=r["slug"], source=r["source"], path=r["path"], title=r["title"], rating=r["rating"],
                tags=json.loads(r["tags"]), summary=r["summary"], body=r["body"], meta=json.loads(r["meta"]),
                synced_at=r["synced_at"], source_mtime=r["source_mtime"])


def _matches(rel: str, patterns: tuple[str, ...]) -> bool:
    """A pattern is a glob (``*``, ``?``, ``[abc]``) or simply the note's own path: a title like ``[AI] notes.md``
    works as written."""
    for raw in patterns:
        pattern = unicodedata.normalize("NFC", raw)
        if rel == pattern or fnmatch.fnmatchcase(rel, pattern):
            return True
    return False


def _walk(root: Path, suffixes: set[str]) -> Iterator[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in suffixes and not (set(p.relative_to(root).parts[:-1]) & SKIP_DIRS):
            yield p


def repo_docs_dir() -> Path:
    return Path(storyagent.__file__).resolve().parents[1] / "docs"


class WikiStore:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    @property
    def assets_dir(self) -> Path:
        return self.settings.home / "wiki" / "assets"

    # -- reading the sources ------------------------------------------------------------------------
    def _entries(self, report: SyncReport) -> list[_Entry]:
        out: list[_Entry] = []
        cfg = self.settings.wiki
        docs = repo_docs_dir()
        if cfg.docs and docs.is_dir():
            out += [_Entry(DOCS_SOURCE, unicodedata.normalize("NFC", p.relative_to(docs).as_posix()), p, GENERAL, docs)
                    for p in _walk(docs, {".md"})]
        for src in cfg.sources:
            if not src.path.is_dir():
                report.warnings.append({"code": "source_missing", "source": src.name, "detail": str(src.path)})
                continue
            if not src.include:
                report.warnings.append({"code": "no_include", "source": src.name, "detail": ""})
                continue
            for p in _walk(src.path, {".md"}):
                rel = unicodedata.normalize("NFC", p.relative_to(src.path).as_posix())
                if not _matches(rel, src.include) or _matches(rel, src.exclude):
                    continue
                if p.stat().st_size > MAX_NOTE_BYTES:
                    report.warnings.append({"code": "too_large", "source": src.name, "detail": rel})
                    continue
                out.append(_Entry(src.name, rel, p, self._rating(src, rel), src.path))
        return out

    def _rating(self, src: WikiSource, rel: str) -> str:
        return src.rating if _matches(rel, src.restricted) else GENERAL

    def _copy_asset(self, entry: _Entry, name: str, images: dict[str, Path]) -> str | None:
        file = images.get(obsidian.norm(Path(name).name))
        if file is None or file.stat().st_size > MAX_ASSET_BYTES:
            return None
        digest = hashlib.sha256(file.read_bytes()).hexdigest()[:10]
        safe = re.sub(r"[^\w.-]+", "-", file.name, flags=re.UNICODE)
        dest = self.assets_dir / f"{digest}-{safe}"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, dest)
        return f"/api/wiki/assets/{quote(dest.name)}"

    # -- sync ---------------------------------------------------------------------------------------
    def sync(self) -> SyncReport:
        report = SyncReport()
        entries = self._entries(report)
        raws: dict[str, tuple[_Entry, dict[str, Any], str]] = {}
        by_name: dict[str, str] = {}          # normalised file name / title / relative path -> slug
        by_path: dict[tuple[str, str], str] = {}
        for e in entries:
            text = e.file.read_text("utf-8", errors="replace")
            meta, body = obsidian.split_frontmatter(text)
            slug = obsidian.slug_of(e.source, e.rel)
            raws[slug] = (e, meta, body)
            stem = e.rel[:-3]
            by_path[(e.source, obsidian.norm(stem))] = slug
            for key in (stem.rsplit("/", 1)[-1], str(meta.get("title", "")), obsidian.first_heading(body)):
                if key.strip():
                    by_name.setdefault(f"{e.source}\0{obsidian.norm(key)}", slug)
        images: dict[str, dict[str, Path]] = {}
        for src in self.settings.wiki.sources:
            if src.path.is_dir():
                images[src.name] = {obsidian.norm(p.name): p for p in _walk(src.path, obsidian.IMAGE_EXT)}

        pages: dict[str, Page] = {}
        link_sets: dict[str, set[str]] = {}
        for slug, (e, meta, body) in raws.items():
            def resolve(target: str, _e: _Entry = e) -> str | None:
                t = target.strip()
                return by_path.get((_e.source, obsidian.norm(t))) or by_name.get(f"{_e.source}\0{obsidian.norm(t)}")

            def resolve_md(path: str, _e: _Entry = e) -> str | None:
                if "://" in path:
                    return None
                base = _e.rel.rsplit("/", 1)[0] if "/" in _e.rel else ""
                parts: list[str] = []
                for part in (f"{base}/{path}" if base else path).split("/"):
                    if part == "..":
                        if parts:
                            parts.pop()
                    elif part and part != ".":
                        parts.append(part)
                return by_path.get((_e.source, obsidian.norm("/".join(parts)[:-3])))

            def asset(name: str, _e: _Entry = e) -> str | None:
                if _e.rating != GENERAL or _e.source == DOCS_SOURCE:   # a restricted page never publishes files
                    return None
                return self._copy_asset(_e, name, images.get(_e.source, {}))

            links: set[str] = set()
            converted = obsidian.convert(body, resolve=resolve, resolve_md=resolve_md, asset=asset, links=links)
            links.discard(slug)
            title = str(meta.get("title") or obsidian.first_heading(body) or e.rel.rsplit("/", 1)[-1][:-3]).strip()
            tags = obsidian.tags_of(meta)
            clean_meta = {k: v for k, v in meta.items() if k not in ("tags", "tag", "title")}
            mtime = datetime.fromtimestamp(e.file.stat().st_mtime, UTC).isoformat(timespec="seconds")
            pages[slug] = Page(slug=slug, source=e.source, path=e.rel, title=title, rating=e.rating, tags=tags,
                               summary=obsidian.summary_of(converted), body=converted, meta=clean_meta,
                               source_mtime=mtime)
            link_sets[slug] = links
            report.sources[e.source] = report.sources.get(e.source, 0) + 1

        with self.db.transaction() as conn:
            existing = {r["slug"]: r for r in conn.execute("SELECT * FROM wiki_pages")}
            for slug in set(existing) - set(pages):
                conn.execute("DELETE FROM wiki_pages WHERE slug=?", (slug,))
                report.removed += 1
            now = _now()
            for slug, page in pages.items():
                digest = hashlib.sha256(json.dumps(
                    [page.title, page.rating, page.tags, page.body, page.meta, page.path], sort_keys=True,
                    ensure_ascii=False).encode()).hexdigest()
                old = existing.get(slug)
                if old is not None and old["content_hash"] == digest:
                    report.unchanged += 1
                else:
                    tags_json = json.dumps(page.tags, ensure_ascii=False)
                    meta_json = json.dumps(page.meta, ensure_ascii=False, default=str)
                    values = (page.source, page.path, page.title, page.rating, tags_json, page.summary, page.body,
                              meta_json, digest, page.source_mtime, now)
                    if old is None:
                        conn.execute(
                            "INSERT INTO wiki_pages(source, path, title, rating, tags, summary, body, meta,"
                            " content_hash, source_mtime, synced_at, slug) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (*values, slug))
                        report.added += 1
                    else:
                        conn.execute(
                            "UPDATE wiki_pages SET source=?, path=?, title=?, rating=?, tags=?, summary=?, body=?,"
                            " meta=?, content_hash=?, source_mtime=?, synced_at=? WHERE slug=?", (*values, slug))
                        report.updated += 1
            conn.execute("DELETE FROM wiki_links")
            conn.executemany("INSERT INTO wiki_links(src, dst) VALUES (?,?)",
                             [(s, d) for s, dsts in link_sets.items() for d in sorted(dsts) if d in pages])
        return report

    # -- reading ------------------------------------------------------------------------------------
    def get(self, slug: str) -> Page | None:
        row = self.db.conn.execute("SELECT * FROM wiki_pages WHERE slug=?", (slug,)).fetchone()
        return _row_page(row) if row else None

    def all(self) -> list[Page]:
        rows = self.db.conn.execute("SELECT * FROM wiki_pages ORDER BY source, path").fetchall()
        return [_row_page(r) for r in rows]

    def backlinks(self, slug: str) -> list[Page]:
        rows = self.db.conn.execute(
            "SELECT p.* FROM wiki_links l JOIN wiki_pages p ON p.slug = l.src WHERE l.dst=? ORDER BY p.title",
            (slug,)).fetchall()
        return [_row_page(r) for r in rows]

    def outgoing(self, slug: str) -> list[Page]:
        rows = self.db.conn.execute(
            "SELECT p.* FROM wiki_links l JOIN wiki_pages p ON p.slug = l.dst WHERE l.src=? ORDER BY p.title",
            (slug,)).fetchall()
        return [_row_page(r) for r in rows]

    def links(self) -> list[tuple[str, str]]:
        rows = self.db.conn.execute("SELECT src, dst FROM wiki_links ORDER BY src, dst").fetchall()
        return [(r["src"], r["dst"]) for r in rows]

    def search(self, query: str, pages: list[Page] | None = None) -> list[tuple[Page, str, int]]:
        """Pages that contain every word of ``query`` (case ignored) in the title, the tags or the text, best first,
        each with a snippet around the first hit. The notes are few and short, so this reads them; no word splitter is
        needed, which is what makes Chinese text searchable."""
        terms = [t.lower() for t in query.split() if t.strip()]
        if not terms:
            return []
        pool = pages if pages is not None else self.all()
        scored: list[tuple[Page, str, int]] = []
        for page in pool:
            title, body, tags = page.title.lower(), page.body.lower(), " ".join(page.tags).lower()
            if not all(t in title or t in body or t in tags for t in terms):
                continue
            score = sum((10 if t in title else 0) + (5 if t in tags else 0) + min(body.count(t), 5) for t in terms)
            scored.append((page, _snippet(page.body, terms), score))
        scored.sort(key=lambda x: (-x[2], x[0].title))
        return scored

    def counts(self) -> dict[str, int]:
        rows = self.db.conn.execute("SELECT source, COUNT(*) AS n FROM wiki_pages GROUP BY source").fetchall()
        return {r["source"]: r["n"] for r in rows}

    def last_sync(self) -> str | None:
        row = self.db.conn.execute("SELECT MAX(synced_at) AS t FROM wiki_pages").fetchone()
        return row["t"] if row else None


def _snippet(body: str, terms: list[str], radius: int = 70) -> str:
    flat = re.sub(r"\s+", " ", re.sub(r"[#>*`|]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", body))
    low = flat.lower()
    hits = [i for i in (low.find(t) for t in terms) if i >= 0]
    if not hits:
        return flat[: radius * 2].strip()
    start = max(0, min(hits) - radius)
    end = min(len(flat), min(hits) + radius)
    return (("..." if start else "") + flat[start:end].strip() + ("..." if end < len(flat) else ""))

