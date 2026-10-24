"""Questions to the knowledge base: retrieval of numbered passages, an answer that cites them, the restricted gate."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings, load_settings
from storyagent.wiki import Page, ask

from .fakes.http_server import FakeServer
from .test_wiki import make_vault


def page(slug: str, body: str, title: str = "T", tags: list[str] | None = None) -> Page:
    return Page(slug=slug, source="docs", path=f"{slug}.md", title=title, rating="general", tags=tags or [], summary="",
                body=body, meta={})


# -- retrieval --------------------------------------------------------------------------------------------------------
def test_terms_cover_latin_words_and_overlapping_chinese_pairs() -> None:
    assert ask.terms_of("How do I start the ACE-Step server?") == ["start", "ace-step", "server"]
    assert ask.terms_of("视频怎么出声音") == ["视频", "频怎", "么出", "出声", "声音"]
    assert ask.terms_of("一") == ["一"]


def test_a_page_is_cut_at_headings_but_not_inside_code() -> None:
    body = "intro\n\n## Run\nstart it\n\n```bash\n# not a heading\nrun --now\n```\n\n## Fix\nrestart\n"
    cut = ask.sections(page("p", body))
    assert [s.heading for s in cut] == ["", "Run", "Fix"]
    assert "# not a heading" in cut[1].text and "restart" in cut[2].text


def test_passages_are_the_sections_that_hold_the_words_numbered_from_one() -> None:
    pages = [page("a", "# A\n\n## Install\nbrew install ffmpeg\n\n## Speed\nfast enough\n", "Tool A"),
             page("b", "# B\n\nunrelated text about cats\n", "Cats"),
             page("c", "# C\n\nffmpeg is also here\n", "Tool C", ["ffmpeg"])]
    found = ask.passages(pages, ask.terms_of("how to install ffmpeg"))
    assert [p.n for p in found] == list(range(1, len(found) + 1))
    assert found[0].page.slug == "a" and found[0].heading == "Install"
    assert "cats" not in {p.page.slug for p in found}
    assert ask.passages(pages, ask.terms_of("quantum chromodynamics")) == []
    text = ask.excerpts_text(found)
    assert text.startswith("[1] Tool A / Install\n") and "brew install ffmpeg" in text


# -- API --------------------------------------------------------------------------------------------------------------
@pytest.fixture
def server() -> Iterator[FakeServer]:
    def chat(req: Any) -> dict[str, Any]:
        answer = {"found": True, "answer": "Speeds are measured in the matrix [1].", "cited": [1, 9]}
        if "nothing here" in req.json["messages"][-1]["content"]:
            answer = {"found": False, "answer": "The excerpts do not say.", "cited": []}
        return {"choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}]}

    with FakeServer() as srv:
        srv.route("GET", "/v1/models", {"data": [{"id": "m1"}, {"id": "m2"}]})
        srv.route("POST", "/v1/chat/completions", chat)
        yield srv


@pytest.fixture
def client(tmp_path: Path, server: FakeServer) -> Iterator[TestClient]:
    vault = make_vault(tmp_path)
    cfg = tmp_path / "home" / "config"
    cfg.mkdir(parents=True)
    (cfg / "wiki.toml").write_text(
        f'[wiki]\ndocs = false\n[[wiki.sources]]\nname = "vault"\npath = "{vault}"\n'
        'include = ["Skills/Model matrix.md", "Skills/音视频*.md", "Skills/Adult*.md"]\n'
        'restricted = ["Skills/Adult*"]\n')
    (cfg / "llm.toml").write_text(
        f'[llm.backends.fake]\nkind = "openai_compat"\nurl = "{server.url}"\n'
        '[llm.roles.knowledge]\nbackend = "fake"\nmodel = "m1"\n'
        '[llm.ratings.restricted]\nbackend = "fake"\nmodel = "m2"\n')
    settings: Settings = load_settings({"STORY_AGENT_HOME": str(tmp_path / "home"), "STORY_AGENT_LOCALE": "en"})
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        c.post("/api/wiki/sync")
        yield c


def asked(server: FakeServer) -> list[Any]:
    return [r for r in server.requests if r.path == "/v1/chat/completions"]


def test_an_answer_cites_the_excerpts_it_was_built_from(client: TestClient, server: FakeServer) -> None:
    r = client.post("/api/wiki/ask", json={"question": "What speeds were measured?", "language": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] and body["grounded"] and "[1]" in body["answer"] and body["model"] == "m1"
    first = body["sources"][0]
    assert first["slug"] == "vault/Skills/Model-matrix" and first["cited"] is True and first["n"] == 1
    assert all(s["n"] != 9 for s in body["sources"])                     # a number the model made up is dropped
    sent = asked(server)[-1].json["messages"]
    assert "Speeds measured here" in sent[-1]["content"] and "[1] Model matrix" in sent[-1]["content"]
    assert "English" in sent[0]["content"]                               # the answer language reaches the prompt


def test_a_question_nothing_mentions_is_not_sent_to_the_model(client: TestClient, server: FakeServer) -> None:
    r = client.post("/api/wiki/ask", json={"question": "quantum chromodynamics"})
    assert r.json()["reason"] == "no_match" and r.json()["sources"] == [] and not r.json()["found"]
    assert asked(server) == []


def test_chinese_questions_find_chinese_notes(client: TestClient) -> None:
    r = client.post("/api/wiki/ask", json={"question": "本机跑视频有什么经验", "language": "zh-CN"}).json()
    assert r["sources"][0]["slug"] == "vault/Skills/音视频-notes"


def test_a_model_that_finds_nothing_is_reported_as_such(client: TestClient, server: FakeServer) -> None:
    r = client.post("/api/wiki/ask", json={"question": "nothing here about the speeds measured"}).json()
    assert r["found"] is False and r["grounded"] is False and r["sources"]       # the excerpts that were read are shown


def test_restricted_notes_are_read_only_when_unlocked_and_then_by_their_own_model(
        client: TestClient, server: FakeServer) -> None:
    question = {"question": "what is the restricted text?"}
    assert client.post("/api/wiki/ask", json=question).json()["reason"] == "no_match"      # locked: not even read
    assert client.post("/api/content/unlock", json={"confirm_adult": True}).status_code == 200
    r = client.post("/api/wiki/ask", json=question).json()
    assert r["sources"][0]["rating"] == "restricted" and r["model"] == "m2"
    assert asked(server)[-1].json["model"] == "m2"
