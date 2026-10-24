"""The wiki as a graph: pages are nodes, the links between them are edges.

``build_graph`` is pure. It gets the pages the caller may see (restricted pages the caller may not see are never
passed in, so they cannot show up as a node, as the far end of an edge, or in a count), the page links, and the models
of ``models.toml``; it returns the nodes and edges the web client draws.

Extra, optional layers (each edge has a ``kind``):

* ``link``        a page links to another page (directed)
* ``tag``         a page carries a tag (``include_tags`` adds one node per tag used by at least two pages)
* ``shared_tag``  two pages share at least ``SHARED_TAG_MIN`` tags and do not link each other (``shared_tags``)
* ``mention``     a page's text names a model or model family of ``models.toml`` (``include_models``)

A model is found in a page when its family name or one of its refs appears in the title, the tags or the text (link
targets are not text) with no letter or digit right before or after it; case is ignored and spaces or underscores count
as hyphens, so "LTX 2.5" finds ``ltx-2.5``. Names shorter than ``MIN_NEEDLE`` characters are not searched for.
"""
from __future__ import annotations

import re
from collections import Counter, deque
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field

from storyagent.config.settings import ModelSpec

from .store import Page

TAG_PREFIX = "tag:"
MODEL_PREFIX = "model:"
MIN_NEEDLE = 4
SHARED_TAG_MIN = 2
SHARED_TAG_MAX_PAGES = 40       # a tag on more pages than this is too broad to relate pages by
DEFAULT_LIMIT = 400
MAX_LIMIT = 1000

_LINK_TARGET = re.compile(r"\]\([^)]*\)")
_SPACES = re.compile(r"[\s_]+")


@dataclass
class GraphNode:
    id: str
    kind: str                     # "page" | "tag" | "model"
    label: str
    slug: str = ""
    source: str = ""
    rating: str = "general"
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    links_in: int = 0             # links from other visible pages, over the whole wiki (not only this view)
    links_out: int = 0
    pages: int = 0                # tag / model nodes: how many pages in this view carry or mention it
    refs: list[str] = field(default_factory=list)   # model nodes: the models of the family
    model_kind: str = ""
    isolated: bool = False        # a page with no edge at all in this view
    match: bool = True            # false for the context pages around the hits of a search


@dataclass
class GraphEdge:
    source: str
    target: str
    kind: str
    weight: int = 1


@dataclass
class Graph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    matched: int                  # pages that passed the filters, before the cap
    truncated: bool
    focus: str


class FocusNotFound(LookupError):
    pass


def _norm(text: str) -> str:
    return _SPACES.sub("-", text.lower())


def find_mentions(pages: Iterable[Page], models: Mapping[str, ModelSpec]) -> dict[str, dict[str, int]]:
    """family -> {page slug: number of hits}; families nobody mentions are absent."""
    names: dict[str, set[str]] = {}
    for ref, spec in models.items():
        family = spec.family or ref
        names.setdefault(family, set()).update(n for n in (family, ref) if len(n) >= MIN_NEEDLE)
    patterns = {
        family: re.compile(
            r"(?<![a-z0-9])(?:" + "|".join(re.escape(_norm(n)) for n in sorted(ns, key=len, reverse=True))
            + r")(?![a-z0-9])")
        for family, ns in names.items() if ns
    }
    out: dict[str, dict[str, int]] = {}
    for page in pages:
        text = _norm("\n".join([page.title, " ".join(page.tags), _LINK_TARGET.sub("]", page.body)]))
        for family, pattern in patterns.items():
            hits = len(pattern.findall(text))
            if hits:
                out.setdefault(family, {})[page.slug] = hits
    return out


def _neighbours(adj: Mapping[str, set[str]], seeds: Collection[str], depth: int) -> set[str]:
    seen = set(seeds)
    queue = deque((s, 0) for s in seeds)
    while queue:
        node, d = queue.popleft()
        if d >= depth:
            continue
        for nxt in adj.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, d + 1))
    return seen


def build_graph(
    pages: list[Page],
    links: Iterable[tuple[str, str]],
    models: Mapping[str, ModelSpec],
    *,
    source: str = "",
    tag: str = "",
    matches: Collection[str] | None = None,
    focus: str = "",
    depth: int = 1,
    include_tags: bool = False,
    shared_tags: bool = False,
    include_models: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> Graph:
    """``pages`` are the visible ones. ``matches``: slugs that match a search (None = no search): the matches and the
    pages linked to them are shown, the latter flagged ``match = False``. ``focus`` is a page slug or
    ``model:<family>``: the page (or the pages that mention the model) and everything within ``depth`` links of it;
    the filters then narrow that neighbourhood and a search only flags ``match``. Raises :class:`FocusNotFound`."""
    limit = max(1, min(limit, MAX_LIMIT))
    depth = 2 if depth >= 2 else 1
    by_slug = {p.slug: p for p in pages}
    pairs = {(s, d) for s, d in links if s in by_slug and d in by_slug and s != d}
    adj: dict[str, set[str]] = {}
    links_in: Counter[str] = Counter()
    links_out: Counter[str] = Counter()
    for s, d in pairs:
        adj.setdefault(s, set()).add(d)
        adj.setdefault(d, set()).add(s)
        links_out[s] += 1
        links_in[d] += 1

    focus_model = focus[len(MODEL_PREFIX):] if focus.startswith(MODEL_PREFIX) else ""
    if focus_model in models:                      # a model ref stands for its family
        focus_model = models[focus_model].family or focus_model
        focus = MODEL_PREFIX + focus_model
    mentions = find_mentions(pages, models) if (include_models or focus_model) else {}
    allowed = {s for s, p in by_slug.items() if (not source or p.source == source) and (not tag or tag in p.tags)}

    flagged = set(matches) if matches is not None else None
    if focus_model:
        seeds = set(mentions.get(focus_model, {}))
        if not seeds and not any((m.family or r) == focus_model for r, m in models.items()):
            raise FocusNotFound(focus)
        pool = _neighbours(adj, seeds, 1) if depth >= 2 else seeds
        pool &= allowed
    elif focus:
        if focus not in by_slug:
            raise FocusNotFound(focus)
        pool = _neighbours(adj, {focus}, depth) & allowed | {focus}
    elif flagged is not None:
        hits = flagged & allowed
        pool = _neighbours(adj, hits, 1) & allowed
    else:
        pool = set(allowed)

    matched = len(pool)
    ordered = sorted(pool, key=lambda s: (s != focus, -(links_in[s] + links_out[s]), by_slug[s].title, s))
    truncated = len(ordered) > limit
    kept = set(ordered[:limit])

    edges = [GraphEdge(s, d, "link") for s, d in sorted(pairs) if s in kept and d in kept]
    linked: set[str] = {e.source for e in edges} | {e.target for e in edges}
    nodes: list[GraphNode] = []

    tag_pages: dict[str, list[str]] = {}
    for s in sorted(kept):
        for t in by_slug[s].tags:
            tag_pages.setdefault(t, []).append(s)
    if include_tags:
        for t, members in sorted(tag_pages.items()):
            if len(members) >= 2:
                nodes.append(GraphNode(id=TAG_PREFIX + t, kind="tag", label=t, pages=len(members)))
                edges += [GraphEdge(s, TAG_PREFIX + t, "tag") for s in members]
                linked.update(members)
    if shared_tags:
        shared: Counter[tuple[str, str]] = Counter()
        for members in tag_pages.values():
            if 2 <= len(members) <= SHARED_TAG_MAX_PAGES:
                for i, a in enumerate(members):
                    for b in members[i + 1:]:
                        shared[(a, b)] += 1
        for (a, b), n in sorted(shared.items()):
            if n >= SHARED_TAG_MIN and (a, b) not in pairs and (b, a) not in pairs:
                edges.append(GraphEdge(a, b, "shared_tag", n))
                linked.update((a, b))
    if mentions:
        refs_of: dict[str, list[str]] = {}
        for ref, spec in models.items():
            refs_of.setdefault(spec.family or ref, []).append(ref)
        for family, counts in sorted(mentions.items()):
            here = {s: n for s, n in counts.items() if s in kept}
            if not here:
                continue
            refs = sorted(refs_of.get(family, [family]))
            nodes.append(GraphNode(id=MODEL_PREFIX + family, kind="model", label=family, pages=len(here), refs=refs,
                                   model_kind=models[refs[0]].kind if refs[0] in models else ""))
            edges += [GraphEdge(s, MODEL_PREFIX + family, "mention", n) for s, n in sorted(here.items())]
            linked.update(here)

    pages_out = [
        GraphNode(id=s, kind="page", label=by_slug[s].title, slug=s, source=by_slug[s].source, rating=by_slug[s].rating,
                  tags=list(by_slug[s].tags), summary=by_slug[s].summary, links_in=links_in[s], links_out=links_out[s],
                  isolated=s not in linked and s != focus,
                  match=flagged is None or s in flagged)
        for s in ordered[:limit]
    ]
    return Graph(nodes=pages_out + nodes, edges=edges, matched=matched, truncated=truncated, focus=focus)
