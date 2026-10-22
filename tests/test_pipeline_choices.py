"""A project's pipeline: the plan's recommended models, single models changed by hand, the companions that follow a
changed picture / video model, and the one-click way back to the plan's defaults."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import ConfigError, Settings, load_settings
from storyagent.pipelines import check_choices, describe, effective_models, settings_for

MODELS = """
[models."alt-img"]
kind = "image.generate"
provider = "fake"
family = "alt"
rating = "restricted"
timeout_sec = 30

[models."alt-img-edit"]
kind = "image.edit"
provider = "fake"
family = "alt"
timeout_sec = 30

[models."other-img"]
kind = "image.generate"
provider = "fake"
family = "other"
timeout_sec = 30
"""


@pytest.fixture
def s(tmp_path: Path) -> Settings:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text(MODELS)
    return load_settings({"STORY_AGENT_HOME": str(home)})


def test_choices_are_checked_against_the_plan_and_the_kind(s: Settings) -> None:
    assert check_choices(s, "vertical_drama", {"image.generate": "alt-img"}) == {"image.generate": "alt-img"}
    for bad in ({"image.generate": "nope"}, {"image.generate": "cosyvoice3-0.5b"}, {"audio.convert": "seed-vc-v2"}):
        with pytest.raises(ConfigError):
            check_choices(s, "vertical_drama", bad)


def test_a_new_picture_model_pairs_the_edit_model_of_its_family_again(s: Settings) -> None:
    models, patch, notes = effective_models(s, "vertical_drama", {"image.generate": "alt-img"})
    assert models["image.generate"] == "alt-img" and models["image.edit"] == "alt-img-edit" and patch == {}
    assert notes == [{"code": "auto_paired", "kind": "image.edit", "model": "alt-img-edit"}]
    eff = settings_for(s, "vertical_drama", {"image.generate": "alt-img"})
    assert eff.default_models["image.generate"] == "alt-img" and eff.default_models["image.edit"] == "alt-img-edit"
    assert eff.default_models["video.generate"] == "ltx-2.5-video"           # everything else stays the plan's


def test_without_an_edit_model_of_the_family_keyframes_are_made_directly(s: Settings) -> None:
    models, patch, notes = effective_models(s, "vertical_drama", {"image.generate": "other-img"})
    assert "image.edit" not in models and patch == {"keyframe_from_references": False}
    assert notes[0]["code"] == "dropped" and notes[0]["kind"] == "image.edit"
    direct = settings_for(s, "vertical_drama", {"image.generate": "other-img"})
    assert direct.workflow["keyframe_from_references"] is False
    assert s.for_pipeline("vertical_drama").workflow["keyframe_from_references"] is True    # the plan itself unchanged


def test_no_choices_is_exactly_the_plan(s: Settings) -> None:
    plain = s.for_pipeline("vertical_drama")
    again = settings_for(s, "vertical_drama", {})
    assert again.default_models == plain.default_models and again.workflow == plain.workflow


def test_the_view_marks_what_pairs_and_warns_about_rated_models_and_hand_picked_mismatches(s: Settings) -> None:
    picked = {"image.generate": "alt-img", "image.edit": "flux2-klein-base-4b-edit"}
    view = describe(s, "vertical_drama", picked, "general")
    rows = {r["kind"]: r for r in view["rows"]}
    assert rows["image.generate"]["overridden"] and rows["image.generate"]["default"] == "flux2-klein-base-4b"
    edit = {c["ref"]: c["paired"] for c in rows["image.edit"]["candidates"]}
    assert edit["alt-img-edit"] is True and edit["flux2-klein-base-4b-edit"] is False
    codes = {(n["code"], n["kind"]) for n in view["notes"]}
    assert ("family_mismatch", "image.edit") in codes and ("rated_model", "image.generate") in codes
    assert describe(s, "vertical_drama", {"image.generate": "alt-img"}, "restricted")["notes"] == [
        {"code": "auto_paired", "kind": "image.edit", "model": "alt-img-edit"}]


@pytest.fixture
def client(s: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(s), raise_server_exceptions=False) as c:
        yield c


def test_choose_then_reset_over_the_api(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "P", "pipeline": "vertical_drama"}).json()["id"]
    base = client.get(f"/api/projects/{pid}/pipeline").json()
    assert not base["overridden"] and all(not r["overridden"] for r in base["rows"])
    r = client.put(f"/api/projects/{pid}/pipeline", json={"models": {"image.generate": "alt-img"}})
    assert r.status_code == 200 and r.json()["overridden"]
    rows = {x["kind"]: x for x in r.json()["rows"]}
    assert rows["image.generate"]["chosen"] == "alt-img" and rows["image.edit"]["chosen"] == "alt-img-edit"
    assert rows["image.edit"]["auto"] is True and rows["image.edit"]["overridden"] is False
    assert client.get(f"/api/projects/{pid}").json()["settings"]["pipeline_models"] == {"image.generate": "alt-img"}
    one = client.put(f"/api/projects/{pid}/pipeline", json={"models": {}}).json()           # one row back to default
    assert not one["overridden"]
    client.put(f"/api/projects/{pid}/pipeline", json={"models": {"image.generate": "alt-img"}})
    reset = client.delete(f"/api/projects/{pid}/pipeline").json()
    assert not reset["overridden"]
    assert {x["kind"]: x["chosen"] for x in reset["rows"]}["image.generate"] == "flux2-klein-base-4b"
    assert "pipeline_models" not in client.get(f"/api/projects/{pid}").json()["settings"]


def test_a_wrong_choice_is_refused_and_changing_the_plan_drops_the_choices(client: TestClient) -> None:
    pid = client.post("/api/projects", json={"name": "P", "pipeline": "vertical_drama"}).json()["id"]
    bad = client.put(f"/api/projects/{pid}/pipeline", json={"models": {"image.generate": "cosyvoice3-0.5b"}})
    assert bad.status_code == 422
    client.put(f"/api/projects/{pid}/pipeline", json={"models": {"audio.music": "acestep-1.5-xl-sft"}})
    assert client.patch(f"/api/projects/{pid}", json={"pipeline": "vertical_drama_fast"}).status_code == 200
    assert not client.get(f"/api/projects/{pid}/pipeline").json()["overridden"]


def test_the_plans_say_which_rating_they_start_with(client: TestClient) -> None:
    plans = {p["name"]: p for p in client.get("/api/meta").json()["pipelines"]}
    assert plans["vertical_drama"]["rating"] == ""
