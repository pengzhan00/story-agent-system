"""Questions to the knowledge base: find the passages that bear on a question, number them for the model.

The pages are few and short, so retrieval reads them (as ``WikiStore.search`` does): no index and no word splitter.
Chinese text is cut into overlapping pairs of characters, other text into lower-case words; a term that occurs in few
pages counts for more than one that occurs in most. Pages are cut at their headings, so an answer cites the section
that holds the fact, not the whole page.
"""
from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .store import Page

CJK = re.compile(f"[{chr(0x3400)}-{chr(0x9fff)}]+")   # a run of Chinese characters
WORD = re.compile(r"[a-z0-9][a-z0-9._+\-]*")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.M)
FENCE = re.compile(r"^```")

MAX_SECTION_CHARS = 1400
MAX_CONTEXT_CHARS = 9000
MAX_PASSAGES_PER_PAGE = 2
_STOP = tomllib.loads((Path(__file__).parent / "stopwords.toml").read_text("utf-8"))
STOP = frozenset(_STOP["words"])
STOP_CHARS = frozenset(_STOP["chars"])
STOP_PAIRS = frozenset(_STOP["pairs"])


def terms_of(question: str) -> list[str]:
    """Search terms of a question, without duplicates, in order of appearance."""
    text = question.lower()
    out: list[str] = []
    for m in WORD.finditer(text):
        w = m.group(0).strip(".-_+")
        if len(w) >= 2 and w not in STOP:
            out.append(w)
    for run in CJK.findall(text):
        if len(run) == 1:
            if run not in STOP_CHARS:
                out.append(run)
        out.extend(pair for pair in (run[i:i + 2] for i in range(len(run) - 1)) if pair not in STOP_PAIRS)
    return list(dict.fromkeys(out))


@dataclass(frozen=True)
class Section:
    slug: str
    heading: str
    text: str


@dataclass(frozen=True)
class Passage:
    n: int
    page: Page
    heading: str
    text: str


def sections(page: Page) -> list[Section]:
    """The page cut at its headings (a heading inside a code fence is not one); the heading stays with its text."""
    lines, cuts, in_fence = page.body.splitlines(), [], False
    for i, line in enumerate(lines):
        if FENCE.match(line.strip()):
            in_fence = not in_fence
        elif not in_fence and HEADING.match(line):
            cuts.append(i)
    bounds = [0] + [c for c in cuts if c > 0] + [len(lines)]
    out: list[Section] = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        chunk = "\n".join(lines[a:b]).strip()
        if not chunk:
            continue
        m = HEADING.match(lines[a]) if a < len(lines) else None
        heading = m.group(2).strip() if m else ""
        for k in range(0, len(chunk), MAX_SECTION_CHARS):          # a long section is read in pieces
            out.append(Section(page.slug, heading, chunk[k:k + MAX_SECTION_CHARS]))
    return out


def weights(pages: list[Page], terms: list[str]) -> dict[str, float]:
    """How much each term says: the fewer pages hold it, the more (a term no page holds says nothing)."""
    low = [(p.title.lower(), " ".join(p.tags).lower(), p.body.lower()) for p in pages]
    out = {}
    for t in terms:
        df = sum(1 for title, tags, body in low if t in title or t in tags or t in body)
        out[t] = math.log(1 + len(pages) / df) if df else 0.0
    return out


def rank(pages: list[Page], terms: list[str], limit: int = 5, weight: dict[str, float] | None = None) -> list[Page]:
    """The pages that bear on the terms, best first (rarer terms and title / tag hits weigh more)."""
    if not terms or not pages:
        return []
    low = {p.slug: (p.title.lower(), " ".join(p.tags).lower(), p.body.lower()) for p in pages}
    weight = weight if weight is not None else weights(pages, terms)
    scored: list[tuple[float, Page]] = []
    for p in pages:
        title, tags, body = low[p.slug]
        score = sum(weight[t] * ((6 if t in title else 0) + (3 if t in tags else 0) + min(body.count(t), 4))
                    for t in terms)
        if score > 0:
            scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], x[1].title))
    return [p for _, p in scored[:limit]]


def passages(pages: list[Page], terms: list[str], *, limit: int = 5) -> list[Passage]:
    """The best sections of the best pages, numbered from 1, within the context budget."""
    out: list[Passage] = []
    used = 0
    weight = weights(pages, terms)
    for page in rank(pages, terms, limit, weight):
        scored = []
        for s in sections(page):
            low = s.text.lower()
            score = sum(weight[t] * (min(low.count(t), 3) + (2 if t in s.heading.lower() else 0)) for t in terms)
            if score > 0:
                scored.append((score, s))
        scored.sort(key=lambda x: -x[0])
        for _, s in scored[:MAX_PASSAGES_PER_PAGE]:
            if used + len(s.text) > MAX_CONTEXT_CHARS and out:
                return out
            out.append(Passage(len(out) + 1, page, s.heading, s.text))
            used += len(s.text)
    return out


def excerpts_text(found: list[Passage]) -> str:
    """The numbered excerpts as the model reads them."""
    blocks = []
    for p in found:
        where = p.page.title + (f" / {p.heading}" if p.heading and p.heading != p.page.title else "")
        blocks.append(f"[{p.n}] {where}\n{p.text}")
    return "\n\n".join(blocks)
