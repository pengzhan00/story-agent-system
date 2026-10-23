"""Obsidian notes -> plain Markdown for the wiki: frontmatter, [[links]], ![[embeds]], callouts, comments, marks.

Pure functions over text; the caller passes in what depends on the rest of the vault (how a link target resolves to a
page, where an embedded picture was put). Code (fenced blocks and inline code) is never touched.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
WIKI_SCHEME = "wiki:"

_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_FENCE = re.compile(r"^(```|~~~)")
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1")
_COMMENT = re.compile(r"%%.*?%%", re.S)
_EMBED = re.compile(r"!\[\[([^\]|#]+)(#[^\]|]*)?(?:\|([^\]]*))?\]\]")
_LINK = re.compile(r"\[\[([^\]|#]*)(#[^\]|]*)?(?:\|([^\]]*))?\]\]")
_MDLINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+\.md)(#[^)\s]*)?\)")
_MARK = re.compile(r"==(?=\S)(.+?)(?<=\S)==")
_CALLOUT = re.compile(r"^(\s*(?:>\s?)+)\[!([A-Za-z-]+)\][+-]?[ \t]*(.*)$")
_BLOCK_ID = re.compile(r"[ \t]+\^[A-Za-z0-9-]+[ \t]*$")
_TAG_SPLIT = re.compile(r"\s*,\s*")


def norm(name: str) -> str:
    """The key a link target is looked up under: case, spaces and Unicode form do not matter."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", name)).strip().lower()


def slug_of(source: str, relative: str) -> str:
    """``source/path/without-extension`` with spaces turned into ``-`` (stable, readable, unique per source)."""
    stem = unicodedata.normalize("NFC", relative)
    if stem.lower().endswith(".md"):
        stem = stem[:-3]
    return f"{source}/" + re.sub(r"\s+", "-", stem.strip("/"))


def wiki_href(slug: str) -> str:
    return WIKI_SCHEME + quote(slug, safe="/")


# -- frontmatter ----------------------------------------------------------------------------------------
def _scalar(raw: str) -> Any:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return [_scalar(x) for x in _TAG_SPLIT.split(value[1:-1].strip()) if x.strip()]
    return value


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """``(meta, body)``. Only the small YAML subset notes use: ``key: value``, ``key: [a, b]`` and ``- item`` lists."""
    match = _FRONT.match(text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    key: str | None = None
    for line in match.group(1).splitlines():
        item = re.match(r"^\s+-\s+(.*)$", line) or re.match(r"^-\s+(.*)$", line)
        if item and key is not None:
            current = meta.get(key)
            meta[key] = [*current, _scalar(item.group(1))] if isinstance(current, list) else [_scalar(item.group(1))]
            continue
        pair = re.match(r"^([^\s:#][^:]*):[ \t]*(.*)$", line)
        if pair:
            key = pair.group(1).strip()
            meta[key] = _scalar(pair.group(2)) if pair.group(2).strip() else ""
    return meta, text[match.end():]


def tags_of(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("tags", meta.get("tag", []))
    if isinstance(raw, str):
        raw = [x for x in _TAG_SPLIT.split(raw.strip()) if x]
    return [str(x).strip().lstrip("#") for x in raw if str(x).strip()]


# -- text -----------------------------------------------------------------------------------------------
def first_heading(body: str) -> str:
    in_fence = False
    for line in body.splitlines():
        if _FENCE.match(line.strip()):
            in_fence = not in_fence
        elif not in_fence:
            match = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
            if match:
                return match.group(1).strip()
    return ""


def summary_of(body: str, limit: int = 200) -> str:
    """The first paragraph of running text (headings, tables, rules, list marks and quote marks skipped)."""
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if _FENCE.match(stripped):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or stripped.startswith(("#", "|", "---", "***", "```")):
            continue
        text = re.sub(r"^(?:>\s*)+|^[-*+]\s+|^\d+[.)]\s+", "", stripped)
        text = re.sub(r"\*\*|__|`|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", text).strip()
        if text:
            return text[:limit]
    return ""


def _outside_code(text: str, fn: Callable[[str], str]) -> str:
    """Apply ``fn`` to every stretch of ``text`` that is not inside a fenced block or an inline code span."""
    out: list[str] = []
    chunk: list[str] = []
    in_fence = False

    def flush() -> None:
        if not chunk:
            return
        block = "\n".join(chunk)
        pieces: list[str] = []
        last = 0
        for m in _INLINE_CODE.finditer(block):
            pieces.append(fn(block[last:m.start()]))
            pieces.append(m.group(0))
            last = m.end()
        pieces.append(fn(block[last:]))
        out.append("".join(pieces))
        chunk.clear()

    for line in text.split("\n"):
        if _FENCE.match(line.strip()):
            flush()
            out.append(line)
            in_fence = not in_fence
        elif in_fence:
            out.append(line)
        else:
            chunk.append(line)
    flush()
    return "\n".join(out)


def convert(
    body: str,
    *,
    resolve: Callable[[str], str | None],
    resolve_md: Callable[[str], str | None] = lambda _p: None,
    asset: Callable[[str], str | None] = lambda _n: None,
    links: set[str] | None = None,
) -> str:
    """Plain Markdown. ``resolve(target)`` -> slug of the page a [[target]] names (None = no such page),
    ``resolve_md(path)`` the same for a relative ``[x](other.md)`` link, ``asset(name)`` -> URL of an embedded
    picture (None = not available). The slugs linked to are added to ``links``."""
    seen = links if links is not None else set()

    def internal(slug: str, label: str) -> str:
        seen.add(slug)
        return f"[{label}]({wiki_href(slug)})"

    def embed(m: re.Match[str]) -> str:
        target, alias = m.group(1).strip(), (m.group(3) or "").strip()
        if "." + target.rsplit(".", 1)[-1].lower() in IMAGE_EXT:
            url = asset(target)
            label = alias if alias and not alias.isdigit() else target
            return f"![{label}]({url})" if url else f"*{label}*"
        slug = resolve(target)
        return internal(slug, alias or target) if slug else (alias or target)

    def link(m: re.Match[str]) -> str:
        target, alias = m.group(1).strip(), (m.group(3) or "").strip()
        if not target:                                     # [[#Heading]]: a link within the page
            return alias or (m.group(2) or "").lstrip("#")
        slug = resolve(target)
        return internal(slug, alias or target) if slug else (alias or target)

    def mdlink(m: re.Match[str]) -> str:
        slug = resolve_md(m.group(2))
        return internal(slug, m.group(1)) if slug else m.group(0)

    def prose(text: str) -> str:
        text = _EMBED.sub(embed, text)
        text = _LINK.sub(link, text)
        text = _MDLINK.sub(mdlink, text)
        return _MARK.sub(r"**\1**", text)

    body = _COMMENT.sub("", body)
    body = _outside_code(body, prose)
    lines: list[str] = []
    in_fence = False
    for line in body.split("\n"):
        if _FENCE.match(line.strip()):
            in_fence = not in_fence
        if not in_fence:
            line = _BLOCK_ID.sub("", line)
            callout = _CALLOUT.match(line)
            if callout:
                title = callout.group(3).strip()
                line = f"{callout.group(1)}**{callout.group(2).upper()}**" + (f" {title}" if title else "")
        lines.append(line)
    return "\n".join(lines).strip("\n") + "\n"
