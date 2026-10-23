"""The wiki: Obsidian syntax -> Markdown, the allow-listed sync, search, links, and the restricted-note gate."""
from __future__ import annotations

import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.wiki import WikiStore
from storyagent.wiki import obsidian as ob


def _resolve(target: str) -> str | None:
    return {"model matrix": "v/Model-matrix", "音视频": "v/音视频"}.get(ob.norm(target))


# -- Obsidian syntax ----------------------------------------------------------------------------------------------
def test_frontmatter_lists_and_inline_tags() -> None:
    meta, body = ob.split_frontmatter(
        "---\ntags: [AI, 视频, \"two words\"]\ntype: matrix\ncreated: 2026-10-20\n---\n# T\ntext")
    assert ob.tags_of(meta) == ["AI", "视频", "two words"]
    assert meta["type"] == "matrix" and body.startswith("# T")
    meta, _ = ob.split_frontmatter("---\ntags:\n  - a\n  - b\n---\nx")
    assert ob.tags_of(meta) == ["a", "b"]
    assert ob.split_frontmatter("no front matter\n") == ({}, "no front matter\n")


def test_links_embeds_callouts_comments_and_code() -> None:
    links: set[str] = set()
    text = "\n".join([
        "See [[Model matrix|the matrix]] and [[Model matrix#Speeds]], also [[Missing note]] and [[音视频]].",
        "Inline `[[Model matrix]]` stays. ==important== %%hidden%% text ^block-1",
        "```",
        "[[Model matrix]] ==x==",
        "```",
        "> [!tip] Remember",
        "> body of the callout",
        "![[shot.png|300]] and ![[Model matrix]] and ![[gone.png]]",
        "[other](../docs/other.md#a)",
    ])
    out = ob.convert(text, resolve=_resolve, resolve_md=lambda p: "docs/other" if p.endswith("other.md") else None,
                     asset=lambda n: "/api/wiki/assets/x-shot.png" if n == "shot.png" else None, links=links)
    assert "[the matrix](wiki:v/Model-matrix)" in out and "[Model matrix](wiki:v/Model-matrix)" in out
    assert "Missing note" in out and "[[Missing" not in out                       # unresolved: plain text
    assert "(wiki:v/%E9%9F%B3%E8%A7%86%E9%A2%91)" in out                           # slugs are URL-encoded
    assert "`[[Model matrix]]`" in out and "```\n[[Model matrix]] ==x==\n```" in out
    assert "**important**" in out and "hidden" not in out and "^block-1" not in out
    assert "> **TIP** Remember" in out
    assert "![shot.png](/api/wiki/assets/x-shot.png)" in out and "*gone.png*" in out
    assert "[other](wiki:docs/other)" in out
    assert links == {"v/Model-matrix", "v/音视频", "docs/other"}


def test_title_and_summary() -> None:
    body = "# The title\n\n> note\n\n| a | b |\n\nFirst **real** paragraph with [a link](x).\n"
    assert ob.first_heading(body) == "The title"
    assert ob.summary_of(body) == "note"            # a quote line is text too; headings and tables are not
    assert ob.summary_of("# H\n\n| a |\n\nPlain text here") == "Plain text here"


# -- the store ----------------------------------------------------------------------------------------------------
def make_vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / "Skills").mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / "Work").mkdir()
    (vault / "Skills" / "Model matrix.md").write_text(
        "---\ntags: [AI, models]\n---\n# Model matrix\n\nSpeeds measured here. See [[音视频 notes]].\n", "utf-8")
    (vault / "Skills" / "音视频 notes.md").write_text(
        "# 音视频 notes\n\n本机跑视频的经验。Back to [[Model matrix]].\n", "utf-8")
    (vault / "Skills" / "Adult setup.md").write_text("# Adult setup\n\nrestricted text ![[pic.png]]\n", "utf-8")
    (vault / "Skills" / "pic.png").write_bytes(b"\x89PNG")
    (vault / "Skills" / "[AI] bracket title.md").write_text("# Bracket\n", "utf-8")
    (vault / "Work" / "Private plan.md").write_text("# Private plan\n\nnot for the wiki\n", "utf-8")
    (vault / ".obsidian" / "x.md").write_text("# config\n", "utf-8")
    return vault


@pytest.fixture
def wsettings(tmp_path: Path) -> Settings:
    vault = make_vault(tmp_path)
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "wiki.toml").write_text(
        f'[wiki]\ndocs = false\n[[wiki.sources]]\nname = "vault"\npath = "{vault}"\n'
        'include = ["Skills/Model matrix.md", "Skills/音视频*.md", "Skills/Adult*.md",\n'
        '           "Skills/[AI] bracket title.md"]\n'
        'exclude = ["Skills/Excluded*.md"]\nrestricted = ["Skills/Adult*"]\n')
    return load_settings({"STORY_AGENT_HOME": str(tmp_path / "home"), "STORY_AGENT_LOCALE": "en"})


@pytest.fixture
def wstore(wsettings: Settings) -> Iterator[WikiStore]:
    db = Database(wsettings.db_path)
    migrate(db)
    yield WikiStore(db, wsettings)
    db.close()


def test_sync_reads_only_the_allow_list_and_links_the_pages(wstore: WikiStore) -> None:
    report = wstore.sync()
    assert (report.added, report.updated, report.removed) == (4, 0, 0)
    slugs = {p.slug for p in wstore.all()}
    assert slugs == {"vault/Skills/Model-matrix", "vault/Skills/音视频-notes", "vault/Skills/Adult-setup",
                     "vault/Skills/[AI]-bracket-title"}                        # a path with [..] works as written
    matrix = wstore.get("vault/Skills/Model-matrix")
    assert matrix is not None and matrix.tags == ["AI", "models"] and matrix.title == "Model matrix"
    assert "(wiki:vault/Skills/%E9%9F%B3%E8%A7%86%E9%A2%91-notes)" in matrix.body
    assert [p.slug for p in wstore.backlinks("vault/Skills/Model-matrix")] == ["vault/Skills/音视频-notes"]
    assert [p.slug for p in wstore.outgoing("vault/Skills/Model-matrix")] == ["vault/Skills/音视频-notes"]
    adult = wstore.get("vault/Skills/Adult-setup")
    assert adult is not None and adult.rating == "restricted"
    assert "pic.png" in adult.body and "/api/wiki/assets/" not in adult.body      # a restricted page publishes no file


def test_sync_is_idempotent_and_follows_the_notes(wstore: WikiStore, wsettings: Settings) -> None:
    wstore.sync()
    again = wstore.sync()
    assert (again.added, again.updated, again.unchanged, again.removed) == (0, 0, 4, 0)
    vault = wsettings.wiki.sources[0].path
    (vault / "Skills" / "Model matrix.md").write_text("# Model matrix\n\nchanged\n", "utf-8")
    (vault / "Skills" / "音视频 notes.md").unlink()
    third = wstore.sync()
    assert (third.updated, third.removed, third.unchanged) == (1, 1, 2)
    assert wstore.get("vault/Skills/音视频-notes") is None
    assert "wiki:" not in (wstore.get("vault/Skills/Model-matrix").body)          # type: ignore[union-attr]


def test_nfd_file_names_and_empty_include_list(tmp_path: Path) -> None:
    vault = tmp_path / "v"
    vault.mkdir()
    name = unicodedata.normalize("NFD", "音视频é.md")
    (vault / name).write_text("# T\n", "utf-8")
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "wiki.toml").write_text(f'[wiki]\ndocs = false\n[[wiki.sources]]\nname="n"\npath="{vault}"\ninclude=["*"]\n')
    s = load_settings({"STORY_AGENT_HOME": str(tmp_path / "home")})
    db = Database(s.db_path)
    migrate(db)
    store = WikiStore(db, s)
    store.sync()
    assert [p.slug for p in store.all()] == [unicodedata.normalize("NFC", "n/音视频é")]
    (cfg / "wiki.toml").write_text(f'[wiki]\ndocs = false\n[[wiki.sources]]\nname="n"\npath="{vault}"\n')
    empty = WikiStore(db, load_settings({"STORY_AGENT_HOME": str(tmp_path / "home")})).sync()
    assert empty.removed == 1 and [w["code"] for w in empty.warnings] == ["no_include"]


def test_search_finds_chinese_words_and_ranks_titles_first(wstore: WikiStore) -> None:
    wstore.sync()
    hits = wstore.search("视频")
    assert [p.slug for p, _, _ in hits] == ["vault/Skills/音视频-notes", "vault/Skills/Model-matrix"]  # title first
    assert "视频" in hits[0][1]
    both = wstore.search("matrix speeds")
    assert [p.slug for p, _, _ in both] == ["vault/Skills/Model-matrix"]
    assert wstore.search("nonsense words") == [] and wstore.search("  ") == []
    assert [p.slug for p, _, _ in wstore.search("ai")][:1] == ["vault/Skills/Model-matrix"]   # a tag counts


def test_source_config_is_checked(tmp_path: Path) -> None:
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "wiki.toml").write_text('[wiki]\n[[wiki.sources]]\nname = "docs"\npath = "x"\n')
    with pytest.raises(Exception, match="reserved"):
        load_settings({"STORY_AGENT_HOME": str(tmp_path / "home")})


# -- API ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def wclient(wsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(wsettings), raise_server_exceptions=False) as c:
        yield c


def test_api_lists_searches_and_gates_restricted_notes(wclient: TestClient) -> None:
    assert wclient.get("/api/wiki/pages").json()["total"] == 0
    synced = wclient.post("/api/wiki/sync").json()
    assert synced["added"] == 4 and synced["sources"] == {"vault": 4}
    listing = wclient.get("/api/wiki/pages").json()
    assert listing["configured"] and listing["synced_at"]
    assert listing["hidden"] == 1 and listing["total"] == 3                       # the restricted note is masked
    assert {p["slug"] for p in listing["pages"]} == {"vault/Skills/Model-matrix", "vault/Skills/音视频-notes",
                                                     "vault/Skills/[AI]-bracket-title"}
    assert {c["name"] for c in listing["tags"]} == {"AI", "models"}
    found = wclient.get("/api/wiki/pages", params={"q": "视频", "source": "vault"}).json()
    assert found["pages"][0]["slug"] == "vault/Skills/音视频-notes" and "视频" in found["pages"][0]["snippet"]
    by_tag = wclient.get("/api/wiki/pages", params={"tag": "models"}).json()["pages"]
    assert by_tag[0]["slug"] == "vault/Skills/Model-matrix"

    page = wclient.get("/api/wiki/pages/vault/Skills/Model-matrix").json()
    assert page["page"]["title"] == "Model matrix"
    assert [b["slug"] for b in page["backlinks"]] == ["vault/Skills/音视频-notes"]
    assert wclient.get("/api/wiki/pages/vault/Skills/Adult-setup").status_code == 403
    assert wclient.get("/api/wiki/pages/vault/Skills/nope").status_code == 404

    assert wclient.post("/api/content/unlock", json={"confirm_adult": True}).status_code == 200
    unlocked = wclient.get("/api/wiki/pages").json()
    assert unlocked["hidden"] == 0 and unlocked["total"] == 4
    assert wclient.get("/api/wiki/pages/vault/Skills/Adult-setup").json()["page"]["rating"] == "restricted"
    assert wclient.get("/api/wiki/assets/..%2F..%2Fetc%2Fpasswd").status_code == 404
