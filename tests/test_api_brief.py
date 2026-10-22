"""The creation wizard over HTTP: kinds and plans in the meta, estimate, create with a plan, change brief/plan."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings


@pytest.fixture
def client(fsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as c:
        yield c


def test_meta_lists_kinds_of_work_and_pipeline_plans_with_labels(client: TestClient) -> None:
    m = client.get("/api/meta?lang=en").json()
    kinds = {k["name"]: k for k in m["work_kinds"]}
    assert {"drama_series", "drama_single", "tv_series", "advert", "feature_film", "custom"} <= set(kinds)
    series = kinds["drama_series"]
    assert series["label"] == "Short-drama series" and series["pipeline"] == "vertical_drama" and series["seasons"]
    assert series["episodes"] == {"min": 2.0, "default": 12.0, "max": 200.0} and "episode_sec" in series["ask"]
    assert kinds["advert"]["seasons"] is False and m["default_work_kind"] == "drama_series"
    plans = {p["name"]: p for p in m["pipelines"]}
    assert plans["advert"]["width"] == 1920 and plans["vertical_drama"]["height"] == 1280
    assert plans["vertical_drama"]["models"]["video.generate"] == "ltx-2.5-video" and plans["advert"]["label"]
    assert m["default_pipeline"] == "vertical_drama"


def test_estimate_before_creating_anything(client: TestClient) -> None:
    r = client.post("/api/projects/estimate", json={
        "kind": "drama_series", "brief": {"seasons": 2, "episodes_per_season": 5, "episode_sec": 90}})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["brief"]["episodes_total"] == 10 and e["episodes"] == 10 and e["pipeline"] == "vertical_drama"
    assert e["shots"] == 10 * 22 and {t["kind"] for t in e["tasks"]} >= {"video.generate", "audio.tts", "image.edit"}
    assert e["total_sec"] > 0 and client.get("/api/projects").json() == []                # nothing was created
    ad = client.post("/api/projects/estimate", json={"kind": "advert"}).json()
    assert ad["pipeline"] == "advert" and ad["episodes"] == 1
    bad = client.post("/api/projects/estimate", json={"kind": "advert", "brief": {"seasons": 3}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid"


def test_create_with_kind_and_brief_then_change_the_brief_and_the_plan(client: TestClient) -> None:
    r = client.post("/api/projects", json={"name": "Saga", "kind": "drama_series",
                                           "brief": {"seasons": 2, "episodes_per_season": 6, "episode_sec": 60}})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["kind"] == "drama_series" and p["pipeline"] == "vertical_drama" and p["settings"]["target_sec"] == 60
    assert p["brief"]["episodes_total"] == 12 and p["aspect_ratio"] == "9:16"
    board = client.get(f"/api/projects/{p['id']}/series").json()
    assert [(s["number"], s["planned_episodes"]) for s in board["seasons"]] == [(1, 6), (2, 6)]

    more = client.patch(f"/api/projects/{p['id']}/brief", json={"episodes_per_season": 8}).json()
    assert more["brief"]["episodes_total"] == 16 and more["brief"]["episode_sec"] == 60
    wide = client.patch(f"/api/projects/{p['id']}", json={"pipeline": "landscape_drama"})
    assert wide.status_code == 200 and wide.json()["aspect_ratio"] == "16:9"
    assert client.patch(f"/api/projects/{p['id']}", json={"pipeline": "no_such"}).status_code == 422
    assert client.post("/api/projects", json={"name": "X", "kind": "no_such"}).status_code == 422
    assert client.post("/api/projects", json={"name": "Y", "pipeline": "advert",
                                              "delivery_profile": "series_vertical_720"}).status_code == 422


def test_the_health_report_can_be_judged_by_what_a_plan_needs(client: TestClient) -> None:
    base = client.get("/api/health").json()
    planned = client.get("/api/health?pipeline=advert").json()
    required = {e["name"] for e in planned["entries"] if e["required"]}
    assert {"tool.mflux", "tool.cosyvoice", "phosphene"} <= required
    assert len({e["name"] for e in base["entries"] if e["required"]}) < len(required)
    assert client.get("/api/health?pipeline=nope").status_code == 422


def test_the_workshop_starts_from_a_plan_and_marks_models_outside_it(client: TestClient) -> None:
    plain = client.get("/api/workshop").json()
    planned = client.get("/api/workshop?pipeline=advert").json()
    assert all(m["in_plan"] for mod in plain for k in mod["kinds"] for m in k["models"])
    kinds = {k["kind"]: k for mod in planned for k in mod["kinds"]}
    video = kinds["video.generate"]
    assert video["default_model"] == "ltx-2.5-video"
    assert {m["ref"]: m["in_plan"] for m in video["models"]}["ltx-2.5-video"] is True
    others = [m for m in video["models"] if m["ref"] != "ltx-2.5-video"]
    assert others and all(m["in_plan"] is False for m in others)           # fake-video etc.: a trial, not for a project
    assert client.get("/api/workshop?pipeline=nope").status_code == 422
