"""HTTP API: every route against a real (temp) SQLite ledger, with the fake adapter doing the work."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.contracts import HealthState
from storyagent.media.healthcheck import Entry, Report
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker


@pytest.fixture
def client(fsettings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(fsettings), raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def run_worker(fsettings: Settings):  # noqa: ANN201
    """Drain the queue with the fake adapter, the way a separate worker process would."""

    def run() -> None:
        reg = AdapterRegistry()
        reg.register(FakeAdapter())
        w = Worker(fsettings, Database(fsettings.db_path), reg, probes=[])
        assert w.drain(15)

    return run


def project(client: TestClient, name: str = "Demo") -> dict[str, Any]:
    r = client.post("/api/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def make_shots(fsettings: Settings, project_id: int, n: int) -> list[int]:
    """Shot rows come from the workflow milestone; until then tests insert them directly."""
    db = Database(fsettings.db_path)
    db.conn.execute("INSERT INTO episodes(project_id, number) VALUES (?, 1)", (project_id,))
    ep = db.conn.execute("SELECT MAX(id) FROM episodes").fetchone()[0]
    ids = []
    for _ in range(n):
        cur = db.conn.execute("INSERT INTO shots(project_id, episode_id) VALUES (?, ?)", (project_id, ep))
        assert cur.lastrowid is not None
        ids.append(cur.lastrowid)
    return ids


def job_body(project_id: int, **over: Any) -> dict[str, Any]:
    return {"kind": "video.generate", "model_ref": "fake-video", "project_id": project_id, "tier": "draft",
            "inputs": {"prompt": "a cat"}, "seed": 5, **over}


# ---- basics ---------------------------------------------------------------------------------------
def test_ready_and_meta(client: TestClient) -> None:
    assert client.get("/api/ready").json() == {"ok": True, "schema_version": 19}
    m = client.get("/api/meta").json()
    assert {"value": "video.generate", "label": "Video"} in [
        {"value": k["value"], "label": k["label"]} for k in m["kinds"]
    ] or any(k["value"] == "video.generate" for k in m["kinds"])
    assert {t["value"] for t in m["tiers"]} == {"draft", "preview", "final"}
    assert any(s["value"] == "completed" for s in m["statuses"])
    assert any(s["value"] == "music.theme_song" for s in m["subject_types"])
    assert any(x["ref"] == "fake-video" and x["tiers"]["draft"]["width"] == 50 for x in m["models"])
    assert {"quick_draft", "standard_shot"} <= {r["name"] for r in m["recipes"]}
    assert m["compliance_enabled"] is True and m["queue"] == {"queued": 0, "running": 0, "waiting": {}}


def test_i18n_catalog_and_language_negotiation(client: TestClient) -> None:
    en = client.get("/api/i18n/en").json()
    zh = client.get("/api/i18n/zh-CN").json()
    assert en.keys() == zh.keys() and "api.error.not_found" in en
    assert client.get("/api/i18n/xx").status_code == 404

    r = client.get("/api/jobs/nope", headers={"Accept-Language": "zh-CN,zh;q=0.9"})
    assert r.status_code == 404 and r.json()["error"]["message"] == zh["api.error.not_found"]
    r = client.get("/api/jobs/nope", headers={"Accept-Language": "en-US"})
    assert r.json()["error"]["message"] == en["api.error.not_found"]
    r = client.get("/api/jobs/nope?lang=zh-CN", headers={"Accept-Language": "en"})
    assert r.json()["error"]["message"] == zh["api.error.not_found"]  # ?lang= wins


def test_error_envelope_shape(client: TestClient) -> None:
    body = client.get("/api/jobs/nope").json()["error"]
    assert set(body) == {"code", "message", "remediation", "detail"}
    assert body["code"] == "not_found" and body["remediation"]
    bad = client.post("/api/jobs", json={"kind": "bogus"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid"
    assert client.get("/api/nothing-here").json()["error"]["code"] == "not_found"


def test_health_endpoint_uses_the_healthcheck(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    rep = Report([
        Entry("comfy", "service", HealthState.OK, "HTTP 200", required=True),
        Entry("tool.h3", "tool", HealthState.MISSING_MODEL, "", ("MiniMax-H3 weights",)),
    ])
    monkeypatch.setattr("storyagent.api.routes_system.health.run_checks", lambda *a, **k: rep)
    body = client.get("/api/health", headers={"Accept-Language": "en"}).json()
    assert body["exit_code"] == 0
    comfy, h3 = body["entries"]
    assert comfy["ok"] and comfy["required"] and comfy["label"] == "Available"
    assert h3["state"] == "missing_model" and h3["missing"] == ["MiniMax-H3 weights"] and not h3["ok"]


# ---- projects -------------------------------------------------------------------------------------
def test_project_crud_and_settings(client: TestClient) -> None:
    p = project(client, "My Film")
    assert p["slug"] == "my-film" and p["settings"] == {}
    assert project(client, "My Film")["slug"] == "my-film-2"
    assert [x["id"] for x in client.get("/api/projects").json()][-1] > p["id"] - 1
    assert client.patch(f"/api/projects/{p['id']}", json={"genre": "wuxia"}).json()["genre"] == "wuxia"
    assert client.get("/api/projects/9999").status_code == 404

    r = client.patch(f"/api/projects/{p['id']}/settings", json={"compliance": {"enabled": False}})
    assert r.json()["settings"] == {"compliance": {"enabled": False}}
    r = client.patch(f"/api/projects/{p['id']}/settings",
                     json={"models": {"fake-video": {"fps": 24}}, "compliance": None})
    assert r.json()["settings"] == {"models": {"fake-video": {"fps": 24}}}
    bad = client.patch(f"/api/projects/{p['id']}/settings", json={"models": {"ghost": {"x": 1}}})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid"
    bad = client.patch(f"/api/projects/{p['id']}/settings", json={"compliance": {"bogus": 1}})
    assert bad.status_code == 422


def test_project_settings_override_reaches_the_job_params(client: TestClient) -> None:
    p = project(client)
    client.patch(f"/api/projects/{p['id']}/settings", json={"models": {"fake-video": {"fps": 30}}})
    job = client.post("/api/jobs", json=job_body(p["id"])).json()["job"]
    assert job["params"]["fps"] == 30 and job["params"]["width"] == 50  # project + draft tier layers


# ---- jobs & assets --------------------------------------------------------------------------------
def test_job_lifecycle_through_the_api(client: TestClient, run_worker) -> None:  # noqa: ANN001
    p = project(client)
    r = client.post("/api/jobs", json=job_body(p["id"]))
    assert r.status_code == 201
    created = r.json()
    assert created["created"] and created["job"]["status"] == "queued" and created["job"]["seed"] == 5
    dup = client.post("/api/jobs", json=job_body(p["id"]))
    assert dup.status_code == 200 and dup.json()["created"] is False and dup.json()["job"]["id"] == created["job"]["id"]
    assert client.get("/api/queue").json()["queued"] == 1

    run_worker()
    job = client.get(f"/api/jobs/{created['job']['id']}").json()
    assert job["status"] == "completed" and job["progress"] == 1 and job["error_code"] is None
    events = [e["event"] for e in client.get(f"/api/jobs/{job['id']}/events").json()]
    assert events[0] == "enqueued" and events[-1] == "completed"
    assert client.get(f"/api/jobs/{job['id']}/events?after=9999").json() == []

    [asset] = client.get("/api/assets", params={"project_id": p["id"]}).json()
    assert asset["is_current"] and asset["job_id"] == job["id"] and asset["sha256"] and asset["bytes"] > 0
    assert asset["provenance"]["model"]["ref"] == "fake-video"
    f = client.get(f"/api/assets/{asset['id']}/file")
    assert f.status_code == 200 and f.content.startswith(b"fake:")
    assert client.get("/api/jobs", params={"project_id": p["id"], "status": "completed"}).json()[0]["id"] == job["id"]
    assert client.get("/api/jobs", params={"status": ["failed_validation", "cancelled"]}).json() == []
    assert client.get("/api/queue").json() == {"queued": 0, "running": 0, "waiting": {}}


def test_cancel_and_retry(client: TestClient) -> None:
    p = project(client)
    job = client.post("/api/jobs", json=job_body(p["id"])).json()["job"]
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409  # still active
    assert client.post(f"/api/jobs/{job['id']}/cancel").json() == {"status": "cancelled"}
    r = client.post(f"/api/jobs/{job['id']}/retry", json={"new_seed": True})
    assert r.status_code == 201 and r.json()["parent_job_id"] == job["id"] and r.json()["seed"] != 5
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_unknown_model_or_wrong_kind_is_422(client: TestClient) -> None:
    p = project(client)
    assert client.post("/api/jobs", json=job_body(p["id"], model_ref="ghost")).status_code == 422
    assert client.post("/api/jobs", json=job_body(p["id"], kind="audio.tts")).status_code == 422
    assert client.post("/api/jobs", json=job_body(9999)).status_code == 404  # unknown project


def test_candidates_adopt_and_trash(client: TestClient, fsettings: Settings, run_worker) -> None:  # noqa: ANN001
    p = project(client)
    [shot] = make_shots(fsettings, p["id"], 1)
    for seed in (1, 2):
        client.post("/api/jobs", json=job_body(p["id"], seed=seed, shot_id=shot))
    run_worker()
    assets = client.get("/api/assets", params={"project_id": p["id"], "shot_id": shot}).json()
    assert len(assets) == 2 and sum(a["is_current"] for a in assets) == 1  # auto_adopt: the newest wins
    older = next(a for a in assets if not a["is_current"])
    adopted = client.post(f"/api/assets/{older['id']}/adopt").json()
    assert adopted["is_current"]
    only_current = client.get("/api/assets", params={"project_id": p["id"], "current_only": True}).json()
    assert [a["id"] for a in only_current] == [older["id"]]

    trashed = client.post(f"/api/assets/{older['id']}/trash").json()
    assert trashed["trashed_at"] and not trashed["is_current"]
    assert len(client.get("/api/assets", params={"project_id": p["id"]}).json()) == 1
    assert len(client.get("/api/assets", params={"project_id": p["id"], "include_trashed": True}).json()) == 2
    assert client.post(f"/api/assets/{older['id']}/adopt").status_code == 409  # trashed assets cannot be adopted
    assert client.post("/api/assets/nope/adopt").status_code == 404


def test_asset_file_is_confined_to_the_data_home(client: TestClient, fsettings: Settings, run_worker) -> None:  # noqa: ANN001
    p = project(client)
    client.post("/api/jobs", json=job_body(p["id"]))
    run_worker()
    [asset] = client.get("/api/assets").json()
    db = Database(fsettings.db_path)
    db.conn.execute("UPDATE media_assets SET path='../outside.txt' WHERE id=?", (asset["id"],))
    assert client.get(f"/api/assets/{asset['id']}/file").status_code == 404
    db.conn.execute("UPDATE media_assets SET path='projects/none/missing.bin' WHERE id=?", (asset["id"],))
    r = client.get(f"/api/assets/{asset['id']}/file", headers={"Accept-Language": "en"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "asset_file_missing"


# ---- specs ----------------------------------------------------------------------------------------
SUBJ = "character.portrait/hero"


def spec_body(**over: Any) -> dict[str, Any]:
    return {"subject_type": "character.portrait", "subject_id": "hero", "kind": "video.generate",
            "model_ref": "fake-video", "prompt": "a knight", "seed": 7, **over}


def test_spec_edit_history_diff_revert(client: TestClient) -> None:
    pid = project(client)["id"]
    base = f"/api/projects/{pid}/specs"
    r = client.post(base, json=spec_body())
    assert r.status_code == 201 and r.json()["version"] == 1 and r.json()["locked"] is False
    assert client.post(base, json=spec_body()).status_code == 409  # already exists

    edit = {"prompt": "a knight at dusk", "params_patch": {"fps": 12}, "note": "mood"}
    v2 = client.patch(f"{base}/{SUBJ}", json=edit)
    assert v2.json()["version"] == 2 and v2.json()["params"] == {"fps": 12} and v2.json()["parent_version"] == 1
    same = client.patch(f"{base}/{SUBJ}", json={"prompt": "a knight at dusk"})
    assert same.json()["version"] == 2  # unchanged content does not create a version
    cleared = client.patch(f"{base}/{SUBJ}", json={"seed": None}).json()
    assert cleared["version"] == 3 and cleared["seed"] is None  # explicit null clears; omitted means untouched

    versions = client.get(f"{base}/{SUBJ}/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3] and versions[1]["note"] == "mood"
    rows = client.get(f"{base}/{SUBJ}/diff", params={"v1": 1, "v2": 2}).json()
    diff = {d["field"]: (d["before"], d["after"]) for d in rows}
    assert diff["prompt"] == ("a knight", "a knight at dusk") and diff["params"] == ({}, {"fps": 12})
    assert client.get(f"{base}/{SUBJ}/diff", params={"v1": 1, "v2": 9}).status_code == 404

    rv = client.post(f"{base}/{SUBJ}/revert", json={"version": 1}).json()
    assert rv["version"] == 4 and rv["prompt"] == "a knight" and rv["seed"] == 7
    assert client.post(f"{base}/{SUBJ}/revert", json={"version": 99}).status_code == 404
    assert client.get(f"{base}/{SUBJ}").json()["version"] == 4
    assert client.get(f"{base}/character.portrait/ghost").status_code == 404
    assert [s["subject_id"] for s in client.get(base, params={"subject_type": "character.portrait"}).json()] == ["hero"]


def test_spec_lock_blocks_edits(client: TestClient) -> None:
    pid = project(client)["id"]
    base = f"/api/projects/{pid}/specs"
    client.post(base, json=spec_body())
    locked = client.put(f"{base}/{SUBJ}/lock", json={"note": "final"}).json()
    assert locked["locked"] is True
    r = client.patch(f"{base}/{SUBJ}", json={"prompt": "x"}, headers={"Accept-Language": "en"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "spec_locked"
    assert client.delete(f"{base}/{SUBJ}/lock").json()["locked"] is False
    assert client.patch(f"{base}/{SUBJ}", json={"prompt": "x"}).status_code == 200
    assert client.put(f"{base}/character.portrait/ghost/lock").status_code == 404


def test_spec_validation_errors(client: TestClient) -> None:
    pid = project(client)["id"]
    base = f"/api/projects/{pid}/specs"
    assert client.post(base, json=spec_body(subject_type="nodot")).status_code == 422
    assert client.post(base, json=spec_body(model_ref="fake-tts")).status_code == 422  # wrong kind for the model
    assert client.post("/api/projects/9999/specs", json=spec_body()).status_code == 404
    client.post(base, json=spec_body())
    assert client.patch(f"{base}/{SUBJ}", json={"model_ref": "fake-tts"}).status_code == 422


def test_regenerate_candidates_stale_and_review(client: TestClient, fsettings: Settings, run_worker) -> None:  # noqa: ANN001
    pid = project(client)["id"]
    [shot] = make_shots(fsettings, pid, 1)
    base = f"/api/projects/{pid}/specs"
    client.post(base, json=spec_body())
    jobs = client.post(f"{base}/{SUBJ}/regenerate", json={"n_candidates": 3, "tier": "draft", "shot_id": shot}).json()
    assert len(jobs) == 3 and len({j["seed"] for j in jobs}) == 3
    assert all(j["spec_version"] == 1 and j["shot_id"] == shot for j in jobs)
    assert client.post(f"{base}/{SUBJ}/regenerate", json={"n_candidates": 2, "vary_seed": False}).status_code == 422
    assert client.post(f"{base}/{SUBJ}/regenerate", json={"n_candidates": 99}).status_code == 422

    run_worker()
    assets = client.get("/api/assets", params={"project_id": pid}).json()
    assert len(assets) == 3 and sum(a["is_current"] for a in assets) == 1
    assert client.get(f"/api/projects/{pid}/stale").json() == []
    client.patch(f"{base}/{SUBJ}", json={"prompt": "now at night"})
    stale = client.get(f"/api/projects/{pid}/stale").json()
    assert [s["latest_spec_version"] for s in stale] == [2] and stale[0]["asset"]["spec_version"] == 1

    # a shot that depends on the portrait is flagged until the creator acknowledges the change
    shot = spec_body(subject_type="shot.video", subject_id="s1", depends_on=[
        {"subject_type": "character.portrait", "subject_id": "hero", "version": 1}])
    client.post(base, json=shot)
    review = client.get(f"{base}/review").json()
    assert [r["spec"]["subject_id"] for r in review] == ["s1"]
    dep = review[0]["changed_dependencies"][0]
    assert (dep["recorded"], dep["current"]) == (1, 2)
    client.post(f"{base}/shot.video/s1/acknowledge")
    assert client.get(f"{base}/review").json() == []


def test_in_flight_job_is_a_snapshot_of_the_spec(client: TestClient) -> None:
    pid = project(client)["id"]
    base = f"/api/projects/{pid}/specs"
    client.post(base, json=spec_body(prompt="original"))
    [job] = client.post(f"{base}/{SUBJ}/regenerate", json={}).json()
    client.patch(f"{base}/{SUBJ}", json={"prompt": "changed later"})
    assert client.get(f"/api/jobs/{job['id']}").json()["inputs"]["prompt"] == "original"


# ---- server-sent events ---------------------------------------------------------------------------
def sse_events(client: TestClient, url: str) -> list[tuple[str, dict[str, Any]]]:
    import json

    out: list[tuple[str, dict[str, Any]]] = []
    with client.stream("GET", url) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        event = ""
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                out.append((event, json.loads(line.split(":", 1)[1])))
    return out


def test_sse_snapshot_and_filter(client: TestClient) -> None:
    a, b = project(client, "A"), project(client, "B")
    ja = client.post("/api/jobs", json=job_body(a["id"])).json()["job"]
    client.post("/api/jobs", json=job_body(b["id"]))
    events = sse_events(client, f"/api/events?project_id={a['id']}&timeout=0.6")
    assert [e for e, _ in events] == ["job"] and events[0][1]["id"] == ja["id"]
    assert events[0][1]["status"] == "queued"
    assert len(sse_events(client, "/api/events?timeout=0.6")) == 2


def test_sse_pushes_changes_and_resumes(client: TestClient, run_worker) -> None:  # noqa: ANN001
    p = project(client)
    job = client.post("/api/jobs", json=job_body(p["id"])).json()["job"]
    last_id = client.app.state.ctx.ledger.last_event_id()  # type: ignore[attr-defined]
    run_worker()
    # reconnecting with ?after= replays what happened while the client was away
    events = sse_events(client, f"/api/events?after={last_id - 1}&timeout=0.6")
    final = [d for _, d in events if d["id"] == job["id"]][-1]
    assert final["status"] == "completed" and final["progress"] == 1
    assert sse_events(client, "/api/events?timeout=0.6") == []  # nothing active any more


# ---- contract -------------------------------------------------------------------------------------
def test_openapi_lists_the_contract(client: TestClient) -> None:
    spec = client.get("/api/openapi.json").json()
    paths = spec["paths"]
    for p in ("/api/jobs", "/api/jobs/{job_id}", "/api/assets/{asset_id}/adopt", "/api/events", "/api/meta",
              "/api/projects/{project_id}/specs/{subject_type}/{subject_id}/regenerate"):
        assert p in paths, p
    assert "JobOut" in spec["components"]["schemas"] and "SpecOut" in spec["components"]["schemas"]
    assert client.get("/api/docs").status_code == 200


def test_cors_allows_the_dev_server_origin_only(client: TestClient) -> None:
    ok = client.get("/api/ready", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = client.get("/api/ready", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers


# ---- uploads and the library ----------------------------------------------------------------------
def test_upload_publish_and_use_a_voice_reference_end_to_end(
    client: TestClient, fsettings: Settings, run_worker  # noqa: ANN001
) -> None:
    p = project(client)
    up = client.post(f"/api/projects/{p['id']}/uploads?filename=lin.wav", content=b"RIFFvoice")
    assert up.status_code == 201, up.text
    asset = up.json()
    assert asset["kind"] == "upload.audio" and asset["metadata"]["original_name"] == "lin.wav"
    assert client.get(f"/api/assets/{asset['id']}/file").content == b"RIFFvoice"

    pub = client.post("/api/library", json={"asset_id": asset["id"], "role": "voice", "name": "Lin",
                                             "tags": ["zh"], "meta": {"text": "hello", "consent": True}})
    assert pub.status_code == 201, pub.text
    item = pub.json()
    assert item["ref"] == "library:voice/Lin" and item["asset"]["project_id"] is None
    dup = {"asset_id": asset["id"], "role": "voice", "name": "Lin"}
    assert client.post("/api/library", json=dup).status_code == 409
    assert client.post("/api/library", json={**dup, "role": "bogus", "name": "Z"}).status_code == 422
    assert [i["name"] for i in client.get("/api/library", params={"role": "voice", "tag": "zh"}).json()] == ["Lin"]
    assert client.get(f"/api/library/{item['id']}/file").content == b"RIFFvoice"

    # a job in ANOTHER project can use the item by name
    q = project(client, "Other")
    body = {"kind": "audio.tts", "model_ref": "fake-tts", "project_id": q["id"],
            "inputs": {"text": "hi", "voice_ref": item["ref"]}}
    job = client.post("/api/jobs", json=body).json()["job"]
    run_worker()
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "completed", done
    assert done["inputs"]["voice_ref"] == "library:voice/Lin"
    events = client.get(f"/api/jobs/{job['id']}/events").json()
    assert any(e["event"] == "refs_resolved" for e in events)

    patched = client.patch(f"/api/library/{item['id']}", json={"description": "heroine"}).json()
    assert patched["description"] == "heroine" and patched["meta"]["consent"] is True
    assert client.delete(f"/api/library/{item['id']}").status_code == 200
    assert client.get("/api/library").json() == []


def test_library_upload_and_limits(client: TestClient) -> None:
    r = client.post("/api/library/upload?filename=v.wav&role=voice&name=Direct&consent=true&text=hey", content=b"abc")
    assert r.status_code == 201, r.text
    assert r.json()["meta"] == {"text": "hey", "consent": True}
    assert client.post("/api/library/upload?filename=v.wav&role=voice&name=Empty", content=b"").status_code == 422
    p = project(client)
    assert client.post(f"/api/projects/{p['id']}/uploads?filename=x.wav", content=b"").status_code == 422
    assert client.post("/api/projects/999/uploads?filename=x.wav", content=b"abc").status_code == 404
    assert client.get("/api/library/nope").status_code == 404


def test_jobs_and_assets_can_be_filtered_by_spec(client: TestClient, run_worker) -> None:  # noqa: ANN001
    pid = project(client)["id"]
    a = client.post(f"/api/projects/{pid}/specs", json={
        "subject_type": "shot.video", "subject_id": "1", "kind": "video.generate", "model_ref": "fake-video",
        "prompt": "a", "seed": 1}).json()
    b = client.post(f"/api/projects/{pid}/specs", json={
        "subject_type": "shot.video", "subject_id": "2", "kind": "video.generate", "model_ref": "fake-video",
        "prompt": "b", "seed": 2}).json()
    for spec in (a, b):
        url = f"/api/projects/{pid}/specs/shot.video/{spec['subject_id']}/regenerate"
        r = client.post(url, json={"n_candidates": 2, "tier": "draft"})
        assert r.status_code == 201
    assert len(client.get("/api/jobs", params={"spec_id": a["id"]}).json()) == 2
    run_worker()
    mine = client.get("/api/assets", params={"spec_id": a["id"]}).json()
    assert len(mine) == 2 and {x["spec_id"] for x in mine} == {a["id"]}
    assert len(client.get("/api/assets", params={"project_id": pid}).json()) == 4


def test_queue_counts_reconcile_with_the_job_lists(client: TestClient) -> None:
    """A number on the page must equal the length of the list it opens (global and per project)."""
    first = project(client)["id"]
    other = project(client, "Other")["id"]
    for seed, pid in ((1, first), (2, first), (3, other)):
        r = client.post("/api/jobs", json=job_body(pid, seed=seed))
        assert r.status_code in (200, 201), r.text
    for scope in ({}, {"project_id": first}, {"project_id": other}):
        counts = client.get("/api/queue", params=scope).json()
        for status in ("queued", "running"):
            listed = client.get("/api/jobs", params={**scope, "status": status}).json()
            assert len(listed) == counts[status], (scope, status)
    assert client.get("/api/queue", params={"project_id": first}).json()["queued"] == 2
    assert client.get("/api/queue").json()["queued"] == 3


def test_a_project_can_acknowledge_restricted_licences(client: TestClient) -> None:
    pid = project(client)["id"]
    r = client.patch(f"/api/projects/{pid}/settings", json={"allow_restricted_licenses": True})
    assert r.status_code == 200 and r.json()["settings"]["allow_restricted_licenses"] is True
    r = client.patch(f"/api/projects/{pid}/settings", json={"allow_restricted_licenses": None})
    assert "allow_restricted_licenses" not in r.json()["settings"]
