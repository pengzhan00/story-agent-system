"""Characters without a voice get one from the library by gender, one each; their spoken lines follow."""
from __future__ import annotations

import subprocess
from pathlib import Path

from storyagent import voices
from storyagent.media.ledger import Ledger
from storyagent.media.library import Library, import_file

from .story_kit import NAMES, make_engine, new_project

FFMPEG = "ffmpeg"


def _voice(engine, tmp_path: Path, name: str, description: str, tags=(), consent=True) -> None:  # noqa: ANN001
    wav = tmp_path / f"{name}.wav"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=4", str(wav)],
                   check=True)
    asset = import_file(engine.settings, Ledger(engine.db, engine.settings), wav, project_id=None, slot=name)
    Library(engine.db, engine.settings).publish(asset.id, "voice", name, description=description, tags=tags,
                                                meta={"consent": consent, "text": "reference words"})


def test_gender_is_read_from_the_words_and_leans_one_way_only() -> None:
    assert voices.gender_of_text("a woman in her thirties, she smiles") == "female"
    assert voices.gender_of_text("deep voice, he speaks slowly") == "male"
    assert voices.gender_of_text("她很温柔") == "female" and voices.gender_of_text("其他人都走了") == ""
    assert voices.gender_of_text("a calm voice") == ""
    assert voices.gender_of_text("female and male chorus") == ""


def test_each_character_gets_its_own_voice_of_its_gender_and_the_lines_follow(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})
    a, b = (engine.store.find_character(pid, n) for n in NAMES)
    engine.store.update_character(a["id"], {"gender": "female"})
    engine.store.update_character(b["id"], {"gender": "male"})
    _voice(engine, tmp_path, "Mei", "young female, northern accent")
    _voice(engine, tmp_path, "Lan", "adult female", tags=["female"])
    _voice(engine, tmp_path, "Reed", "synthetic male")
    _voice(engine, tmp_path, "NoConsent", "adult male", consent=False)
    lib = Library(engine.db, engine.settings)
    done = voices.auto_assign(engine.store, lib, engine.spec_store, pid)
    assert [(x.character, x.voice) for x in done.assigned] == [(NAMES[0], "Lan"), (NAMES[1], "Reed")]
    assert not done.unresolved
    assert engine.store.find_character(pid, NAMES[0])["voice_ref_audio"] == "library:voice/Lan"
    assert engine.store.find_character(pid, NAMES[1])["voice_ref_consent"] == 1
    shot = next(s for s in engine.store.list_shots(pid) if s["dialogue"])
    spec = engine.spec_store.current(pid, "dialogue.line", f"{shot['id']}#0")
    assert spec.params["voice_ref"].startswith("library:voice/") and spec.version == 2
    again = voices.auto_assign(engine.store, lib, engine.spec_store, pid)        # nothing left to do
    assert not again.assigned and not again.unresolved
    assert engine.spec_store.current(pid, "dialogue.line", f"{shot['id']}#0").version == 2


def test_an_unknown_gender_or_a_missing_voice_is_reported_not_guessed(tmp_path: Path) -> None:
    engine, _, _ = make_engine(tmp_path)
    pid = new_project(engine)
    engine.run(pid, None, {"visual_prompts": False})
    a, b = (engine.store.find_character(pid, n) for n in NAMES)
    engine.store.update_character(a["id"], {"gender": "", "appearance": "", "voice_profile": "", "personality": "",
                                            "role": "", "background": ""})
    engine.store.update_character(b["id"], {"gender": "male"})
    _voice(engine, tmp_path, "Mei", "adult female")
    done = voices.auto_assign(engine.store, Library(engine.db, engine.settings), engine.spec_store, pid)
    assert sorted((x.character, x.reason) for x in done.unresolved) == sorted(
        [(NAMES[0], "unknown_gender"), (NAMES[1], "no_voice_left")])
    assert not done.assigned


def test_the_api_reports_who_got_a_voice_and_who_did_not(fsettings) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    from storyagent.api.app import create_app
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as client:
        pid = client.post("/api/projects", json={"name": "Voices"}).json()["id"]
        client.post(f"/api/projects/{pid}/characters", json={"name": "Ann", "gender": "female"})
        client.post(f"/api/projects/{pid}/characters", json={"name": "Bo"})
        body = client.post(f"/api/projects/{pid}/voices/auto").json()
        assert body["assigned"] == []
        assert sorted((u["character"], u["reason"]) for u in body["unresolved"]) == [
            ("Ann", "no_voice_left"), ("Bo", "unknown_gender")]
        assert client.post("/api/projects/999/voices/auto").status_code == 404
