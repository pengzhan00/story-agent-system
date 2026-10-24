"""The wiki graph: nodes and edges, filters, focus, tag and model layers, the cap, and the restricted-note gate."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings, load_settings
from storyagent.config.settings import ModelSpec
from storyagent.wiki import FocusNotFound, Page, build_graph
from storyagent.wiki.graph import find_mentions

V = "vault/Notes/"
SECRET = "Secret roadmap"


def make_vault(root: Path) -> tuple[Path, Path]:
    vault = root / "vault"
    (vault / "Notes").mkdir(parents=True)
    notes = {
        "A": ("tags: [AI, video]", "# A\n\nRuns on LTX 2.5. See [[B]] and [[C]] and [[Secret roadmap]].\n"),
        "B": ("tags: [AI, video]", "# B\n\nBack to [[A]]. Also [[D2]].\n"),
        "C": ("tags: [other]", "# C\n\nUses ltx-2.5-av and flux2-klein. Back to [[A]].\n"),
        "D2": ("tags: [AI]", "# D2\n\nSecond hop from A through B.\n"),
        "Lonely": ("tags: [AI, video]", "# Lonely\n\nNobody links here and it links nowhere.\n"),
        "Secret roadmap": ("tags: [AI, video]", "# Secret roadmap\n\nflux2-klein plans. Goes to [[A]].\n"),
    }
    for name, (front, body) in notes.items():
        (vault / "Notes" / f"{name}.md").write_text(f"---\n{front}\n---\n{body}", "utf-8")
    other = root / "other"
    other.mkdir()
    (other / "E.md").write_text("# E\n\nA page of the other source.\n", "utf-8")
    return vault, other


@pytest.fixture
def gsettings(tmp_path: Path) -> Settings:
    vault, other = make_vault(tmp_path)
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "wiki.toml").write_text(
        f'[wiki]\ndocs = false\n[[wiki.sources]]\nname = "vault"\npath = "{vault}"\ninclude = ["Notes/*.md"]\n'
        'restricted = ["Notes/Secret*"]\n'
        f'[[wiki.sources]]\nname = "other"\npath = "{other}"\ninclude = ["*.md"]\n')
    return load_settings({"STORY_AGENT_HOME": str(tmp_path / "home"), "STORY_AGENT_LOCALE": "en"})


@pytest.fixture
def gclient(gsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(gsettings), raise_server_exceptions=False) as c:
        assert c.post("/api/wiki/sync").json()["added"] == 7
        yield c


def graph(client: TestClient, **params: object) -> dict:
    r = client.get("/api/wiki/graph", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def ids(g: dict, kind: str = "page") -> set[str]:
    return {n["id"] for n in g["nodes"] if n["kind"] == kind}


def edge_set(g: dict, kind: str = "link") -> set[tuple[str, str]]:
    return {(e["source"], e["target"]) for e in g["edges"] if e["kind"] == kind}


def test_full_graph_has_pages_links_degrees_and_isolated_pages(gclient: TestClient) -> None:
    g = graph(gclient)
    assert ids(g) == {V + n for n in ("A", "B", "C", "D2", "Lonely")} | {"other/E"}     # the restricted note is absent
    assert edge_set(g) == {(V + "A", V + "B"), (V + "A", V + "C"), (V + "B", V + "A"), (V + "B", V + "D2"),
                           (V + "C", V + "A")}
    a = next(n for n in g["nodes"] if n["id"] == V + "A")
    assert (a["links_out"], a["links_in"], a["label"], a["source"], a["tags"]) == (2, 2, "A", "vault", ["AI", "video"])
    assert {n["id"] for n in g["nodes"] if n["isolated"]} == {V + "Lonely", "other/E"}
    assert (g["total"], g["hidden"], g["matched"], g["truncated"]) == (6, 1, 6, False)
    assert {c["name"]: c["count"] for c in g["sources"]} == {"other": 1, "vault": 5}


def test_locked_restricted_pages_leave_no_trace_and_unlock_brings_them_back(gclient: TestClient) -> None:
    for params in ({}, {"include_tags": True, "include_models": True, "shared_tags": True}, {"q": "flux2-klein plans"},
                   {"focus": V + "A", "depth": 2}, {"tag": "video"}):
        g = graph(gclient, **params)
        text = json.dumps(g, ensure_ascii=False)
        assert "Secret-roadmap" not in text and "Secret%20" not in text               # no id, no edge end, no slug
        assert all(n["label"] != SECRET and n["slug"] != V + "Secret-roadmap" for n in g["nodes"])
        assert g["hidden"] == 1 and g["total"] == 6
    g = graph(gclient, include_tags=True, include_models=True)
    video = next(n for n in g["nodes"] if n["id"] == "tag:video")
    assert video["pages"] == 3                                    # A, B and Lonely: the hidden page is not counted
    a = next(n for n in g["nodes"] if n["id"] == V + "A")
    assert a["links_out"] == 2                                    # the link to the restricted note is not counted
    klein = next(n for n in g["nodes"] if n["id"] == "model:flux2-klein")
    assert klein["pages"] == 1                                    # only C; the restricted page also names it
    assert gclient.get("/api/wiki/graph", params={"focus": V + "Secret-roadmap"}).status_code == 403

    assert gclient.post("/api/content/unlock", json={"confirm_adult": True}).status_code == 200
    g = graph(gclient, include_models=True)
    assert V + "Secret-roadmap" in ids(g) and g["hidden"] == 0 and g["total"] == 7
    assert (V + "A", V + "Secret-roadmap") in edge_set(g)
    assert next(n for n in g["nodes"] if n["id"] == "model:flux2-klein")["pages"] == 2
    assert graph(gclient, focus=V + "Secret-roadmap")["focus"] == V + "Secret-roadmap"


def test_source_tag_and_search_filters(gclient: TestClient) -> None:
    assert ids(graph(gclient, source="other")) == {"other/E"}
    only_ai = graph(gclient, tag="AI")
    assert ids(only_ai) == {V + n for n in ("A", "B", "D2", "Lonely")}
    assert edge_set(only_ai) == {(V + "A", V + "B"), (V + "B", V + "A"), (V + "B", V + "D2")}
    found = graph(gclient, q="second hop")          # the hit plus the pages linked to it, for context
    assert ids(found) == {V + "D2", V + "B"}
    assert {n["id"]: n["match"] for n in found["nodes"]} == {V + "D2": True, V + "B": False}
    assert edge_set(found) == {(V + "B", V + "D2")}
    assert ids(graph(gclient, q="nonsense words")) == set()
    assert ids(graph(gclient, q="D2", source="other")) == set()    # filters narrow the hits too


def test_focus_depth_one_and_two(gclient: TestClient) -> None:
    one = graph(gclient, focus=V + "A", depth=1)
    assert ids(one) == {V + "A", V + "B", V + "C"} and one["focus"] == V + "A"
    assert not any(n["isolated"] for n in one["nodes"])
    two = graph(gclient, focus=V + "A", depth=2)
    assert ids(two) == {V + n for n in ("A", "B", "C", "D2")}
    assert ids(graph(gclient, focus=V + "D2", depth=1)) == {V + "D2", V + "B"}      # links count both ways
    assert ids(graph(gclient, focus=V + "A", depth=2, source="other")) == {V + "A"}  # the focus stays
    assert gclient.get("/api/wiki/graph", params={"focus": "vault/Notes/missing"}).status_code == 404
    assert gclient.get("/api/wiki/graph", params={"focus": "model:no-such-model"}).status_code == 404
    assert gclient.get("/api/wiki/graph", params={"depth": 3}).status_code == 422


def test_tag_nodes_shared_tag_edges_and_model_nodes(gclient: TestClient) -> None:
    g = graph(gclient, include_tags=True)
    assert ids(g, "tag") == {"tag:AI", "tag:video"}                # a tag on a single page is not a node
    assert {s for s, t in edge_set(g, "tag") if t == "tag:video"} == {V + "A", V + "B", V + "Lonely"}
    assert next(n for n in g["nodes"] if n["id"] == V + "Lonely")["isolated"] is False   # the tag connects it

    g = graph(gclient, shared_tags=True)
    shared = {frozenset(e) for e in edge_set(g, "shared_tag")}
    assert shared == {frozenset((V + "A", V + "Lonely")), frozenset((V + "B", V + "Lonely"))}   # A-B are linked already
    assert all(e["weight"] == 2 for e in g["edges"] if e["kind"] == "shared_tag")

    g = graph(gclient, include_models=True)
    assert ids(g, "model") == {"model:ltx-2.5", "model:flux2-klein"}
    assert edge_set(g, "mention") == {(V + "A", "model:ltx-2.5"), (V + "C", "model:ltx-2.5"),
                                      (V + "C", "model:flux2-klein")}
    ltx = next(n for n in g["nodes"] if n["id"] == "model:ltx-2.5")
    assert ltx["refs"] == ["ltx-2.5-av", "ltx-2.5-lipsync", "ltx-2.5-video"] and ltx["model_kind"] == "video.av"
    assert graph(gclient)["nodes"] and "model:ltx-2.5" not in ids(graph(gclient), "model")   # off unless asked for

    by_model = graph(gclient, focus="model:ltx-2.5")               # the notes that mention a model
    assert ids(by_model) == {V + "A", V + "C"} and "model:ltx-2.5" in ids(by_model, "model")
    assert ids(graph(gclient, focus="model:ltx-2.5-av")) == ids(by_model)         # a ref stands for its family
    assert graph(gclient, focus="model:ltx-2.5-av")["focus"] == "model:ltx-2.5"
    assert ids(graph(gclient, focus="model:ltx-2.5", depth=2)) == {V + n for n in ("A", "B", "C")}


def test_cap_keeps_the_best_connected_pages(gclient: TestClient) -> None:
    g = graph(gclient, limit=3)
    assert g["truncated"] is True and g["matched"] == 6 and g["limit"] == 3
    assert ids(g) == {V + "A", V + "B", V + "C"}                   # A (4 links), B (3), C (2)
    assert edge_set(g) == {(V + "A", V + "B"), (V + "A", V + "C"), (V + "B", V + "A"), (V + "C", V + "A")}
    assert gclient.get("/api/wiki/graph", params={"limit": 5000}).status_code == 422


# -- the pure function --------------------------------------------------------------------------------------------
def page(slug: str, body: str = "", tags: list[str] | None = None, title: str = "") -> Page:
    return Page(slug=slug, source=slug.split("/")[0], path=slug, title=title or slug, rating="general",
                tags=tags or [], summary="", body=body, meta={})


def spec(ref: str, family: str = "") -> ModelSpec:
    return ModelSpec(ref=ref, kind="video.generate", provider="x", family=family or ref)


def test_model_names_match_whole_words_only() -> None:
    models = {"ltx-2.5-video": spec("ltx-2.5-video", "ltx-2.5"), "abc": spec("abc"), "seed-vc": spec("seed-vc")}
    pages = [
        page("s/one", "LTX 2.5 and LTX_2.5 again"),
        page("s/two", "ltx-2.55 is another thing; abc is too short; xseed-vc is not it"),
        page("s/three", "see [ltx-2.5](wiki:s/ltx-2.5-notes)", tags=["Seed-VC"]),
        page("s/four", "the model ltx-2.5-video, run.", title="Plain"),
    ]
    found = find_mentions(pages, models)
    assert found == {"ltx-2.5": {"s/one": 2, "s/three": 1, "s/four": 1}, "seed-vc": {"s/three": 1}}


def test_build_graph_ignores_links_to_pages_it_was_not_given() -> None:
    pages = [page("s/a"), page("s/b")]
    g = build_graph(pages, [("s/a", "s/b"), ("s/a", "s/hidden"), ("s/hidden", "s/b"), ("s/a", "s/a")], {})
    assert [(e.source, e.target) for e in g.edges] == [("s/a", "s/b")]
    a = next(n for n in g.nodes if n.id == "s/a")
    b = next(n for n in g.nodes if n.id == "s/b")
    assert (a.links_out, b.links_in, b.links_out) == (1, 1, 0)
    with pytest.raises(FocusNotFound):
        build_graph(pages, [], {}, focus="s/hidden")
