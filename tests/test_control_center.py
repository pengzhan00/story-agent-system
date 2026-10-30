from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from storyagent.api.app import create_app
from storyagent.control.catalog import ControlError
from storyagent.control.graph import GraphStore
from storyagent.control.publications import Publications
from storyagent.db import Database
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.ledger import Ledger, NewAsset
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker


def image_node(ident="portrait", **extra):
    return {
        "id": ident,
        "kind": "image.generate",
        "model_ref": "fake-image",
        "inputs": {"prompt": "a ceramic teapot"},
        **extra,
    }


def test_graph_fanin_review_restart_and_no_double_dispatch(fsettings, fdb):
    ledger = Ledger(fdb, fsettings)
    graph = GraphStore(fsettings, fdb, ledger)
    definition = {
        "nodes": [
            image_node("one", review=True),
            image_node("two"),
            {
                "id": "clip",
                "kind": "video.generate",
                "model_ref": "fake-video",
                "inputs": {"prompt": "a teapot turning", "image": {"$node": "one", "port": "main"}},
                "depends_on": ["two"],
            },
        ]
    }
    run = graph.submit(definition, "test", "request-1")
    assert graph.submit(definition, "test", "request-1")["id"] == run["id"]
    reg = AdapterRegistry()
    adapter = FakeAdapter()
    reg.register(adapter)
    worker = Worker(fsettings, fdb, reg, ledger=ledger, probes=[])
    try:
        worker.drain(timeout=3)
        waiting = graph.get(run["id"])
        assert waiting["status"] == "waiting"
        assert [n["status"] for n in waiting["nodes"]] == ["waiting_review", "succeeded", "pending"]
        node = waiting["nodes"][0]
        with pytest.raises(ControlError):
            graph.action(run["id"], "approve", "test", "one", node["revision"] - 1)
        graph.action(run["id"], "approve", "test", "one", node["revision"])
        assert worker.drain(timeout=4)
        finished = GraphStore(fsettings, Database(fsettings.db_path)).get(run["id"])
        assert finished["status"] == "succeeded"
        assert len([c for c in adapter.calls if c[0] == "submit"]) == 3
        for _ in range(3):
            graph.tick()
        assert len(ledger.list_jobs()) == 3
        assert fdb.conn.execute("SELECT COUNT(*) FROM asset_relations").fetchone()[0] == 1
    finally:
        worker.shutdown()


def test_graph_rejects_cycle_type_mismatch_and_changed_binding(fsettings, fdb):
    graph = GraphStore(fsettings, fdb)
    with pytest.raises(ControlError, match="cycle"):
        graph.compile({"nodes": [image_node("a", depends_on=["b"]), image_node("b", depends_on=["a"])]})
    with pytest.raises(ControlError, match="Incompatible"):
        graph.compile(
            {
                "nodes": [
                    image_node("a"),
                    {"id": "b", "kind": "audio.separate", "inputs": {"audio": {"$node": "a", "port": "main"}}},
                ]
            }
        )
    run = graph.submit({"nodes": [image_node()]}, "test")
    plan = run["plan"]
    plan["nodes"][0]["binding_digest"] = "changed"
    fdb.conn.execute("UPDATE control_runs SET plan=? WHERE id=?", (json.dumps(plan), run["id"]))
    graph.tick()
    assert graph.get(run["id"])["nodes"][0]["reason"] == "binding_changed"
    assert not Ledger(fdb, fsettings).list_jobs()


def test_graph_cancel_before_dispatch_and_request_collision(fsettings, fdb):
    graph = GraphStore(fsettings, fdb)
    run = graph.submit({"nodes": [image_node()]}, "test", "key")
    with pytest.raises(ControlError, match="Idempotency"):
        graph.submit({"nodes": [image_node("another")]}, "test", "key")
    graph.action(run["id"], "cancel", "test")
    graph.tick()
    assert graph.get(run["id"])["status"] == "cancelled"
    assert not Ledger(fdb, fsettings).list_jobs()


def register_asset(settings, ledger, name):
    path = settings.home / (name + ".txt")
    path.write_text(name)
    return ledger.register_asset(
        NewAsset(kind="upload.text", slot=name, path=path.name, sha256=name, bytes=len(name)), project_id=None
    )


def test_publication_revision_fanout_reverse_lookup_and_confirmation(fsettings, fdb):
    ledger = Ledger(fdb, fsettings)
    assets = [register_asset(fsettings, ledger, n) for n in ["first", "second"]]
    store = Publications(fdb, ledger)
    target = store.target("example-video", "my-channel", "", "My channel")
    data = {"target_id": target["id"], "title": "First release", "items": [{"asset_id": a.id} for a in assets]}
    first = store.save({**data, "request_key": "request"}, "test")
    assert store.save({**data, "request_key": "request"}, "test")["id"] == first["id"]
    assert first["status"] == "draft"
    with pytest.raises(ControlError, match="evidence"):
        store.transition(first["id"], "published", 1, "test", actual_published_at="2026-10-29T08:00:00+08:00")
    published = store.transition(
        first["id"],
        "published",
        1,
        "test",
        external_id="post-1",
        actual_published_at="2026-10-29T08:00:00+08:00",
        evidence="Manually confirmed",
    )
    assert published["confirmation"] == "manual_unverified"
    edited = store.save(
        {**data, "title": "Updated", "items": [{"asset_id": assets[1].id}]}, "test", first["id"], published["revision"]
    )
    assert len(edited["revisions"]) == 2 and len(edited["revisions"][0]["items"]) == 2
    assert store.listing(asset_id=assets[0].id)["total"] == 1
    second = store.save(data, "test")
    with pytest.raises(ControlError, match="already recorded"):
        store.transition(
            second["id"],
            "published",
            1,
            "test",
            external_id="post-1",
            actual_published_at="2026-10-29T08:00:00Z",
            evidence="confirmed",
        )
    gone = store.transition(first["id"], "withdrawn", edited["revision"], "test")
    assert gone["actual_published_at"] == published["actual_published_at"]
    assert store.listing(status="published")["total"] == 0
    with pytest.raises(sqlite3.IntegrityError):
        fdb.conn.execute("DELETE FROM media_assets WHERE id=?", (assets[0].id,))


def test_control_http_preview_submit_and_manual_publication(fsettings):
    with TestClient(create_app(fsettings)) as client:
        ctx = client.app.state.ctx
        asset = register_asset(fsettings, ctx.ledger, "http-asset")
        assert client.post("/api/control/plans", json={"nodes": [image_node()]}).status_code == 200
        assert client.get("/api/control/runs").json()["total"] == 0
        run = client.post("/api/control/runs", json={"nodes": [image_node()]})
        assert run.status_code == 201, run.text
        assert client.get("/api/control/runs").json()["total"] == 1
        t = client.post("/api/publications/targets", json={"platform": "example", "account": "artist"}).json()
        record = client.post(
            "/api/publications",
            json={"target_id": t["id"], "title": "A new release", "items": [{"asset_id": asset.id}]},
        )
        assert record.status_code == 201, record.text
        assert client.get("/api/publications", params={"asset_id": asset.id}).json()["total"] == 1


@pytest.mark.parametrize("duration,kind,starts,stems,nodes", [
    (10, "upload.audio", [0, 4.8, 9.6], 1, 8),     # parent + stem + 3 renders + 2 anchors + assembly
    (5.03, "upload.audio", [0], 1, 4),             # what is left fits the first part's whole frames: no anchor
    (10, "audio.tts", [0, 4.8, 9.6], 0, 7),        # synthesised speech is not separated first
])
def test_lipsync_sequence_persists_dynamic_anchors_and_shares_one_stem(fsettings, duration, kind, starts, stems, nodes):
    import struct
    import wave
    from dataclasses import replace

    from storyagent.config import load_settings
    from storyagent.db import migrate
    from storyagent.domain import MediaKind
    from storyagent.media.contracts import ArtifactSpec

    cfg = fsettings.config_dir / "models.toml"
    with cfg.open("a") as fh:
        fh.write(
            '\n[models."fake-lipsync"]\nkind="video.lipsync"\nprovider="fake"\n'
            '[models."fake-lipsync".params]\nframe_rate=24\n'
        )
    settings = load_settings({"STORY_AGENT_HOME": str(fsettings.home)})
    db = Database(settings.db_path)
    migrate(db)
    ledger = Ledger(db, settings)
    audio = settings.home / "song.wav"
    with wave.open(str(audio), "wb") as w:
        w.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        w.writeframes(struct.pack("<h", 200) * 8000 * 12)
    song = ledger.register_asset(
        NewAsset(
            kind=kind,
            slot="song",
            path="song.wav",
            sha256="test-song",
            duration_sec=12,
            has_audio=True,
            bytes=audio.stat().st_size,
        ),
        project_id=None,
    )
    portrait = register_asset(settings, ledger, "portrait")
    db.conn.execute("UPDATE media_assets SET kind='upload.image' WHERE id=?", (portrait.id,))

    class Render(FakeAdapter):
        def fetch(self, external_id, job, ctx):
            return [replace(a, metadata={"panel_file": "panel-part.mp4"}) for a in super().fetch(external_id, job, ctx)]

    class Separate(FakeAdapter):
        provider = "demucs"

    class Utilities(FakeAdapter):
        provider = "control-tools"

        def fetch(self, external_id, job, ctx):
            items = super().fetch(external_id, job, ctx)
            if job.params["operation"] == "anchor":
                return [ArtifactSpec(items[0].path, metadata={"audio_start_time": job.params["source_end"] - 0.2})]
            import subprocess

            from storyagent.media.probe import find_tool

            target = ctx.work_dir / "final.mp4"
            subprocess.run(
                [
                    str(find_tool(settings, "ffmpeg")),
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=black:s=64x64:r=24:d={job.params['duration']}",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=8000:cl=mono",
                    "-t",
                    str(job.params["duration"]),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(target),
                ],
                check=True,
                capture_output=True,
            )
            return [ArtifactSpec(target)]

    registry = AdapterRegistry()
    for adapter in [Render(), Separate(), Utilities()]:
        registry.register(adapter)
    store = GraphStore(settings, db, ledger)
    run = store.submit(
        {
            "nodes": [
                {
                    "id": "singer",
                    "kind": "video.lipsync",
                    "model_ref": "fake-lipsync",
                    "inputs": {"image": f"asset:{portrait.id}", "audio": f"asset:{song.id}"},
                    "params": {"duration": duration},
                }
            ]
        },
        "test",
    )
    worker = Worker(settings, db, registry, ledger=ledger, probes=[])
    try:
        assert worker.drain(10)
        done = store.get(run["id"])
        assert done["status"] == "succeeded", [
            {k: v for k, v in n.items() if k in ("node_id", "status", "error", "reason")} for n in done["nodes"]]
        jobs = ledger.list_jobs(limit=100)
        assert sum(j.kind is MediaKind.AUDIO_SEPARATE for j in jobs) == stems
        rendered = sorted(
            (j for j in jobs if j.kind is MediaKind.VIDEO_LIPSYNC), key=lambda j: j.params["audio_start_time"]
        )
        assert [round(j.params["audio_start_time"], 1) for j in rendered] == starts
        assert all(j.params["audio_stem_auto"] is False for j in rendered)
        if len(rendered) > 1:
            assert rendered[1].inputs["refs"][0]["path"] != rendered[0].inputs["refs"][0]["path"]
        graph_plan = done["plan"]["nodes"][0]["sequence"]
        assert sum(graph_plan["lengths"]) == pytest.approx(duration)
        assert len(done["nodes"]) == nodes
    finally:
        worker.shutdown()


def test_http_auth_roles_and_restricted_reference_isolation(fsettings):
    from storyagent.config import load_settings

    settings = load_settings({"STORY_AGENT_HOME": str(fsettings.home), "STORY_AGENT_AUTH": "token"})
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        ctx = client.app.state.ctx
        for name, role in [("writer", "editor"), ("reader", "viewer"), ("root", "admin")]:
            ctx.auth.create_user(name, "correct horse", role, birth_date="1990-01-01")
        assert client.get("/api/control/runs").status_code == 401
        tokens = {}
        for name in ["writer", "reader", "root"]:
            tokens[name] = client.post("/api/auth/login", json={"username": name, "password": "correct horse"}).json()[
                "token"
            ]

        def h(name):
            return {"Authorization": "Bearer " + tokens[name]}

        assert client.post("/api/control/runs", headers=h("reader"), json={"nodes": [image_node()]}).status_code == 403
        assert (
            client.post(
                "/api/publications/targets", headers=h("writer"), json={"platform": "x", "account": "y"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/publications/targets", headers=h("root"), json={"platform": "x", "account": "y"}
            ).status_code
            == 201
        )
        asset = register_asset(settings, ctx.ledger, "restricted-input")
        ctx.db.conn.execute("UPDATE media_assets SET rating='restricted' WHERE id=?", (asset.id,))
        request = {"nodes": [image_node(inputs={"prompt": "transform", "image": f"asset:{asset.id}"})]}
        assert client.post("/api/control/runs", headers=h("writer"), json=request).status_code == 403
        assert ctx.db.conn.execute("SELECT COUNT(*) FROM control_runs").fetchone()[0] == 0
        shared = register_asset(settings, ctx.ledger, "shared-public")
        ctx.db.conn.execute("UPDATE projects SET rating='restricted' WHERE id=1")
        ctx.db.conn.execute(
            "INSERT INTO asset_selections VALUES ('shot','1','shot.keyframe',?,1,'root','2026-10-29')",
            (shared.id,),
        )
        response = client.get(f"/api/assets/{shared.id}/relations", headers=h("writer"))
        assert response.status_code == 200
        assert response.json()["selections"] == []


def test_api_selection_drives_project_consumer_and_revision_lock(fsettings, fdb):
    from storyagent.domain import MediaKind
    from storyagent.media.specs import SpecStore
    from storyagent.workflow.media import current_asset, up_to_date

    with TestClient(create_app(fsettings)) as client:
        ctx = client.app.state.ctx
        asset = register_asset(fsettings, ctx.ledger, "selected")
        ctx.db.conn.execute("UPDATE media_assets SET kind='image.generate' WHERE id=?", (asset.id,))
        spec = SpecStore(ctx.db, fsettings, ctx.ledger).create(
            1, "shot.keyframe", "1", MediaKind.IMAGE_GENERATE, prompt="a portrait", model_ref="fake-image"
        )
        payload = {"owner_type": "shot", "owner_id": "1", "slot": "shot.keyframe", "asset_id": asset.id, "revision": 0}
        response = client.put("/api/control/selections", json=payload)
        assert response.status_code == 200, response.text
        assert current_asset(ctx.ledger, spec).id == asset.id
        assert up_to_date(ctx.ledger, spec)
        assert client.put("/api/control/selections", json=payload).status_code == 409


def test_cpu_assembly_uses_original_audio_and_exact_interval(fsettings, tmp_path):
    import subprocess
    import wave

    from storyagent.media.probe import find_tool, probe
    from storyagent.media.runners.control_tools import run

    ffmpeg = find_tool(fsettings, "ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg unavailable")
    audio = tmp_path / "source.wav"
    with wave.open(str(audio), "wb") as w:
        w.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        w.writeframes(b"\0\0" * 8000 * 3)
    clips = []
    for i, color in enumerate(["red", "blue"]):
        path = tmp_path / f"clip-{i}.mp4"
        subprocess.run(
            [
                str(ffmpeg),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=64x64:r=24:d=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
        clips.append(str(path))
    result = run(
        {
            "out": str(tmp_path),
            "operation": "stitch",
            "ffmpeg": str(ffmpeg),
            "inputs": {"clips": clips, "source_audio": str(audio)},
            "params": {"lengths": [0.5, 0.5], "duration": 1.0, "audio_start": 0.75, "fps": 24},
        }
    )
    info = probe(fsettings, tmp_path / result["file"])
    assert info.has_audio and info.has_video
    assert abs(info.duration_sec - 1.0) < 0.1
    assert result["original_audio"] is True
    assert abs(result["stream_durations"]["video"] - 1.0) < 0.05
    # A longer audio track must not hide a short picture stream in the container duration.
    short_out = tmp_path / "short-picture"
    short_out.mkdir()
    with pytest.raises(ValueError, match="Video stream"):
        run(
            {
                "out": str(short_out),
                "operation": "stitch",
                "ffmpeg": str(ffmpeg),
                "inputs": {"clips": clips, "source_audio": str(audio)},
                "params": {"lengths": [0.5, 1.5], "duration": 2.0, "audio_start": 0.5, "fps": 24},
            }
        )


def test_project_compiler_uses_explicit_script_and_mixed_modes(fsettings, fdb):
    from storyagent.domain import MediaKind
    from storyagent.media.specs import SpecStore

    with TestClient(create_app(fsettings)) as client:
        ctx = client.app.state.ctx
        script = ctx.db.conn.execute("INSERT INTO scripts(project_id,episode_no,title) VALUES (1,1,'Pilot')").lastrowid
        ctx.db.conn.execute(
            "UPDATE shots SET script_id=?,render_payload=? WHERE id=1",
            (script, json.dumps({"mode": "t2v", "seconds": 1, "prompt": "clouds"})),
        )
        for number, mode in [(2, "i2v"), (3, "a2v")]:
            sid = ctx.db.conn.execute(
                "INSERT INTO shots(project_id,episode_id,script_id,shot_number,render_payload) VALUES (1,1,?,?,?)",
                (script, number, json.dumps({"mode": mode, "seconds": 1, "prompt": "a singer", "audio_start": 2})),
            ).lastrowid
            SpecStore(ctx.db, fsettings, ctx.ledger).create(
                1, "shot.keyframe", str(sid), MediaKind.IMAGE_GENERATE, prompt="a portrait", model_ref="fake-image"
            )
            asset = register_asset(fsettings, ctx.ledger, f"portrait-{sid}")
            ctx.db.conn.execute("UPDATE media_assets SET kind='upload.image' WHERE id=?", (asset.id,))
            assert (
                client.put(
                    "/api/control/selections",
                    json={
                        "owner_type": "shot",
                        "owner_id": str(sid),
                        "slot": "shot.keyframe",
                        "asset_id": asset.id,
                        "revision": 0,
                    },
                ).status_code
                == 200
            )
        song = register_asset(fsettings, ctx.ledger, "song-plan")
        ctx.db.conn.execute("UPDATE media_assets SET kind='upload.audio',duration_sec=3 WHERE id=?", (song.id,))
        request = {"script_id": script, "source_audio": f"asset:{song.id}", "assemble": True}
        response = client.post("/api/projects/1/control/plan", json=request)
        assert response.status_code == 200, response.text
        plan = response.json()
        assert len(plan["nodes"]) == 4
        assert [n["params"]["mode"] for n in plan["nodes"][:3]] == ["t2v", "i2v", "a2v"]
        assert plan["nodes"][2]["kind"] == "video.lipsync"
        assert plan["nodes"][3]["params"]["duration"] == 3
        assert ctx.db.conn.execute("SELECT COUNT(*) FROM control_runs").fetchone()[0] == 0
        ctx.db.conn.execute("UPDATE media_assets SET duration_sec=3.6 WHERE id=?", (song.id,))
        assert client.post("/api/projects/1/control/plan", json=request).status_code == 422
        assert (
            client.post("/api/projects/1/control/plan", json={**request, "tail_policy": "freeze_last"}).status_code
            == 422
        )


def test_scheduler_isolates_invalid_checkpoint(fsettings, fdb, monkeypatch):
    graph = GraphStore(fsettings, fdb)
    broken = graph.submit({"nodes": [image_node()]}, "test")
    healthy = graph.submit({"nodes": [image_node()]}, "test")
    original = graph._advance

    def advance(rid):
        if rid == broken["id"]:
            raise ControlError("Invalid persisted sequence checkpoint")
        original(rid)

    monkeypatch.setattr(graph, "_advance", advance)
    graph.tick()
    assert graph.get(broken["id"])["status"] == "failed"
    assert graph.get(healthy["id"])["nodes"][0]["job_id"]


@pytest.mark.parametrize("bad", ["nan", "-1", "bad"])
def test_assembly_rejects_invalid_timing(fsettings, fdb, bad):
    graph = GraphStore(fsettings, fdb)
    with pytest.raises(ControlError):
        graph.compile(
            {
                "nodes": [
                    {
                        "id": "film",
                        "kind": "compose.assemble",
                        "model_ref": "control-stitch",
                        "inputs": {"shots": [{"video": "asset:missing", "seconds": bad}]},
                        "params": {"duration": 1},
                    }
                ]
            }
        )


def test_control_migration_upgrades_old_database_and_preserves_restore_copy(tmp_path, monkeypatch):
    from storyagent.db import current_version, migrate, migrator

    migrations = migrator.discover()
    db = Database(tmp_path / "upgrade.db")
    with monkeypatch.context() as ctx:
        ctx.setattr(migrator, "discover", lambda: migrations[:24])
        assert migrate(db) == (0, 24)
    db.conn.execute("INSERT INTO projects(slug,name) VALUES ('kept','Existing project')")
    latest = len(migrations)
    assert migrate(db) == (24, latest)
    assert migrate(db) == (latest, latest)
    assert db.conn.execute("SELECT name FROM projects").fetchone()[0] == "Existing project"
    assert list(db.conn.execute("PRAGMA foreign_key_check")) == []
    backup = Database(tmp_path / "upgrade.db.bak-v24")
    assert current_version(backup) == 24
    assert backup.conn.execute("SELECT name FROM projects").fetchone()[0] == "Existing project"
    backup.close()
    db.close()


def test_separation_cache_reuses_valid_artifacts_and_ignores_purged_ids(fsettings, fdb):
    ledger = Ledger(fdb, fsettings)
    source = register_asset(fsettings, ledger, "cache-source")
    output = register_asset(fsettings, ledger, "cache-output")
    fdb.conn.execute("UPDATE media_assets SET kind='upload.audio' WHERE id IN (?,?)", (source.id, output.id))
    graph = GraphStore(fsettings, fdb, ledger)
    definition = {"nodes": [{"id": "stem", "kind": "audio.separate", "inputs": {"audio": f"asset:{source.id}"}}]}
    first = graph.submit(definition, "test")
    graph.tick()
    run = graph.get(first["id"])
    job = ledger.get_job(run["nodes"][0]["job_id"])
    key = graph.cache_key(None, run["plan"]["nodes"][0], job.inputs)
    fdb.conn.execute("INSERT INTO control_cache VALUES (?,?,?)", (key, json.dumps({"main": "purged"}), "now"))
    second = graph.submit(definition, "test")
    graph.tick()
    assert graph.get(second["id"])["nodes"][0]["job_id"]
    fdb.conn.execute("UPDATE control_cache SET outputs=? WHERE cache_key=?", (json.dumps({"main": output.id}), key))
    third = graph.submit(definition, "test")
    graph.tick()
    node = graph.get(third["id"])["nodes"][0]
    assert node["status"] == "succeeded" and node["job_id"] is None
    assert node["outputs"] == {"main": output.id}


def test_configured_sound_effect_can_be_previewed_and_dispatched(fsettings, fdb):
    """Every exposed sound model needs a shared workshop/control input descriptor."""
    with TestClient(create_app(fsettings)) as client:
        descriptor = next(c for c in client.get("/api/capabilities").json() if c["id"] == "audio.sfx")
        assert descriptor["module"] == "sound"
        definition = {
            "nodes": [
                {
                    "id": "knock",
                    "kind": "audio.sfx",
                    "model_ref": "stable-audio-3-medium",
                    "inputs": {"prompt": "two knocks on a wooden door"},
                    "params": {"duration": 2},
                }
            ]
        }
        preview = client.post("/api/control/plans", json=definition)
        assert preview.status_code == 200, preview.text
        created = client.post("/api/control/runs", json=definition)
        assert created.status_code == 201, created.text
        ctx = client.app.state.ctx
        graph = GraphStore(fsettings, ctx.db, ctx.ledger)
        graph.tick()
        job_id = graph.get(created.json()["id"])["nodes"][0]["job_id"]
        assert ctx.ledger.get_job(job_id).inputs["prompt"] == "two knocks on a wooden door"
