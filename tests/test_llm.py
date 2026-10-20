from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from storyagent.config import LLMBackend, Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.llm import ChatResult, LLMError, LLMRouter, OllamaClient, OpenAICompatClient, build_client
from storyagent.llm.jsonfix import close_truncated, parse_object, sanitize_control_chars
from storyagent.llm.schema import validate

from .fakes.http_server import FakeServer


# ---- JSON recovery ---------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('<think>hmm {"x":0}</think>{"a": 2}', {"a": 2}),
    ('Sure! Here it is: {"a": {"b": [1, 2]}} hope it helps', {"a": {"b": [1, 2]}}),
    ('{"a": "line one\nline two"}', {"a": "line one\nline two"}),
    ('{"items": [{"n": 1}, {"n": 2}, {"n": 3', {"items": [{"n": 1}, {"n": 2}]}),
])
def test_parse_object_recovers_what_models_emit(text: str, expected: dict) -> None:
    assert parse_object(text) == expected


def test_parse_object_rejects_non_objects() -> None:
    assert parse_object("no json here") is None
    assert parse_object("[1, 2, 3]") is None


def test_helpers() -> None:
    assert sanitize_control_chars('{"a": "x\ty"}') == '{"a": "x\\ty"}'
    assert close_truncated('{"a": [1, 2, {"b": 3') == '{"a": [1, 2]}'


# ---- schema ---------------------------------------------------------------------------------------
SCHEMA = {"type": "object", "required": ["name", "age", "tags"],
          "properties": {"name": {"type": "string", "minLength": 1}, "age": {"type": "integer"},
                         "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                         "mood": {"type": "string", "enum": ["calm", "tense"]}}}


def test_schema_coerces_harmless_mismatches() -> None:
    value, problems = validate({"name": ["a", "b"], "age": "42", "tags": "solo", "mood": "calm"}, SCHEMA)
    assert problems == [] and value == {"name": "a\nb", "age": 42, "tags": ["solo"], "mood": "calm"}


def test_schema_reports_what_cannot_be_fixed() -> None:
    _, problems = validate({"name": "", "tags": [], "mood": "odd"}, SCHEMA)
    assert any("age: missing" in p for p in problems) and any("shorter" in p for p in problems)
    assert any("at least 1" in p for p in problems) and any("not one of" in p for p in problems)
    _, problems = validate({"name": "x", "age": "old", "tags": ["t"]}, SCHEMA)
    assert problems == ["$.age: expected integer, got str"]


# ---- router -------------------------------------------------------------------------------------------
class ScriptedClient:
    name = "scripted"

    def __init__(self, replies: list[str], models: list[str] | None = None) -> None:
        self.replies, self.calls, self.models = list(replies), [], models or ["m1"]

    def chat(self, model: str, system: str, prompt: str, **kw: Any) -> ChatResult:
        self.calls.append({"model": model, "system": system, "prompt": prompt, **kw})
        return ChatResult(self.replies.pop(0) if len(self.replies) > 1 else self.replies[0], model, 5, 7, 12)

    def list_models(self) -> list[str]:
        return self.models


@pytest.fixture
def lsettings(tmp_path: Path) -> Settings:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "llm.toml").write_text(
        '[llm.roles.default]\nbackend = "ollama"\nmodel = "m1"\n'
        '[llm.roles.writer]\nbackend = "ollama"\nmodel = "m2"\n[llm.roles.writer.params]\ntemperature = 0.9\n'
        'max_tokens = 999\n')
    return load_settings({"STORY_AGENT_HOME": str(home)})


def router(settings: Settings, client: ScriptedClient, db: Database | None = None) -> LLMRouter:
    return LLMRouter(settings, db, clients={"ollama": client})


def test_role_routing_and_params(lsettings: Settings) -> None:
    c = ScriptedClient(['{"a": 1}'])
    r = router(lsettings, c)
    assert r.resolve("writer")[:2] == ("ollama", "m2") and r.resolve("nobody")[:2] == ("ollama", "m1")
    r.json("writer", "p", schema=None, temperature=0.1)
    call = c.calls[0]
    assert call["model"] == "m2" and call["max_tokens"] == 999 and call["temperature"] == 0.1
    assert "ONE valid JSON object" in call["system"]


def test_json_repairs_by_asking_again_with_the_problem(lsettings: Settings) -> None:
    c = ScriptedClient(["not json at all", '{"name": "x", "age": "n/a", "tags": ["t"]}',
                        '{"name": "x", "age": 3, "tags": ["t"]}'])
    out = router(lsettings, c).json("writer", "make one", schema=SCHEMA)
    assert out["age"] == 3 and len(c.calls) == 3
    assert "was rejected" in c.calls[1]["prompt"] and "not a JSON object" in c.calls[1]["prompt"]
    assert "$.age" in c.calls[2]["prompt"]
    assert c.calls[1]["schema"] == SCHEMA


def test_json_gives_up_with_a_typed_error(lsettings: Settings) -> None:
    c = ScriptedClient(["garbage"])
    with pytest.raises(LLMError) as exc:
        router(lsettings, c).json("writer", "p", attempts=2)
    assert exc.value.code == "bad_output" and len(c.calls) == 2


def test_check_role_names_the_missing_model(lsettings: Settings) -> None:
    r = router(lsettings, ScriptedClient(['{}'], models=["other:1b"]))
    with pytest.raises(LLMError) as exc:
        r.check_role("writer")
    assert exc.value.code == "model_missing" and exc.value.params["model"] == "m2"
    assert router(lsettings, ScriptedClient(['{}'], models=["m2", "x"])).check_role("writer") == ("ollama", "m2")


def test_calls_are_audited(lsettings: Settings, tmp_path: Path) -> None:
    db = Database(lsettings.db_path)
    migrate(db)
    db.conn.execute("INSERT INTO projects(slug, name) VALUES ('p', 'P')")
    r = router(lsettings, ScriptedClient(['{"a": 1}']), db)
    r.json("writer", "the prompt", project_id=1)
    row = db.conn.execute("SELECT agent, model, tokens_in, tokens_out, prompt FROM generation_logs").fetchone()
    assert tuple(row) == ("writer", "m2", 5, 7, "the prompt")


def test_unknown_backend_is_an_error(lsettings: Settings) -> None:
    with pytest.raises(LLMError) as exc:
        LLMRouter(lsettings).client("nope")
    assert exc.value.code == "no_backend"


# ---- real HTTP clients against a fake server ---------------------------------------------------------------
def backend(url: str, kind: str, **options: Any) -> LLMBackend:
    return LLMBackend("b", kind, url, "", options)


def test_ollama_client_payload_and_think_fallback() -> None:
    with FakeServer() as srv:
        srv.route("GET", "/api/tags", {"models": [{"name": "gemma4:31b"}, {"name": "qwen3:8b"}]})
        replies = [(400, {"error": "\"gemma4:31b\" does not support thinking"}),
                   {"message": {"content": "<think>x</think>{\"a\": 1}"}, "prompt_eval_count": 4, "eval_count": 9,
                    "done_reason": "length"}]
        srv.route("POST", "/api/chat", replies)
        c = OllamaClient(backend(srv.url, "ollama", keep_alive="5m"))
        assert c.list_models() == ["gemma4:31b", "qwen3:8b"]
        res = c.chat("gemma4:31b", "sys", "hello", temperature=0.2, max_tokens=50, schema={"type": "object"})
        assert res.text == '{"a": 1}' and (res.tokens_in, res.tokens_out) == (4, 9) and res.truncated
        first, second = [r.json for r in srv.requests if r.path == "/api/chat"]
        assert first["think"] is False and "think" not in second
        assert first["format"] == {"type": "object"} and first["keep_alive"] == "5m"
        assert first["messages"][0] == {"role": "system", "content": "sys"}
        assert first["options"]["num_predict"] == 50 and first["options"]["temperature"] == 0.2


def test_ollama_errors_are_typed() -> None:
    with FakeServer() as srv:
        srv.route("POST", "/api/chat", (404, {"error": "model 'zzz' not found"}))
        with pytest.raises(LLMError) as exc:
            OllamaClient(backend(srv.url, "ollama")).chat("zzz", "", "hi")
        assert exc.value.code == "model_missing"
    with pytest.raises(LLMError) as exc:
        OllamaClient(backend("http://127.0.0.1:9", "ollama")).chat("m", "", "hi")
    assert exc.value.code == "unreachable"


def test_openai_compat_client_uses_response_format_and_bearer() -> None:
    with FakeServer() as srv:
        srv.route("GET", "/v1/models", {"data": [{"id": "qwen-gguf"}]})
        srv.route("POST", "/v1/chat/completions", {
            "choices": [{"message": {"content": "{\"ok\": true}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5}})
        c = build_client(backend(srv.url, "llamacpp", api_key="k"))
        assert isinstance(c, OpenAICompatClient) and c.list_models() == ["qwen-gguf"]
        res = c.chat("qwen-gguf", "", "hi", schema={"type": "object", "properties": {}})
        assert res.text == '{"ok": true}' and (res.tokens_in, res.tokens_out) == (3, 5) and not res.truncated
        req = next(r for r in srv.requests if r.path == "/v1/chat/completions")
        assert req.json["response_format"]["json_schema"]["schema"] == {"type": "object", "properties": {}}
        assert req.json["max_tokens"] == 4096 and "messages" in req.json


# ---- memory safety: no language-model call next to a heavy media job ---------------------------------
def _heavy_job(db: Database, status: str) -> None:
    db.conn.execute("INSERT INTO projects(slug, name) VALUES ('p', 'P')")
    db.conn.execute("INSERT INTO media_jobs(id, project_id, kind, provider, model_ref, heavy, status)"
                    " VALUES ('j1', 1, 'video.avatar', 'longcat', 'm', 1, ?)", (status,))


def test_a_call_is_refused_at_once_while_a_heavy_job_runs(lsettings: Settings, tmp_path: Path) -> None:
    db = Database(tmp_path / "x.db")
    migrate(db)
    _heavy_job(db, "running")
    c = ScriptedClient(['{"a": 1}'])
    r = router(lsettings, c, db)
    r.heavy_wait_sec = 0
    with pytest.raises(LLMError) as err:
        r.json("writer", "p", schema=None)
    assert err.value.code == "heavy_busy" and c.calls == []            # the model was never asked


def test_a_call_waits_for_the_heavy_job_to_finish(lsettings: Settings, tmp_path: Path) -> None:
    import threading
    import time

    db = Database(tmp_path / "y.db")
    migrate(db)
    _heavy_job(db, "running")
    c = ScriptedClient(['{"a": 1}'])
    r = router(lsettings, c, db)
    r.heavy_wait_sec = 20

    def finish() -> None:
        time.sleep(0.5)
        other = Database(tmp_path / "y.db")
        other.conn.execute("UPDATE media_jobs SET status='completed' WHERE id='j1'")

    threading.Thread(target=finish, daemon=True).start()
    assert r.json("writer", "p", schema=None) == {"a": 1} and len(c.calls) == 1


def test_light_or_finished_jobs_do_not_block_the_model(lsettings: Settings, tmp_path: Path) -> None:
    db = Database(tmp_path / "z.db")
    migrate(db)
    _heavy_job(db, "completed")
    r = router(lsettings, ScriptedClient(['{"a": 1}']), db)
    r.heavy_wait_sec = 0
    assert r.json("writer", "p", schema=None) == {"a": 1}
