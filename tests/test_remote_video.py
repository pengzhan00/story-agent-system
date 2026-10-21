"""Cloud video through the configurable REST job adapter, against a local fake vendor."""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from storyagent.config import Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.domain import MediaKind
from storyagent.media import compliance
from storyagent.media.adapters.remote_video import RemoteVideoAdapter, dig, fill
from storyagent.media.broker import can_start
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker

from .fakes.http_server import FakeServer

KEY_ENV = "TEST_CLOUDVIDEO_KEY"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
needs_ffmpeg = pytest.mark.skipif(not Path(FFMPEG).exists(), reason="ffmpeg not installed")


def real_mp4(tmp_path: Path) -> bytes:
    """The worker checks every output with ffprobe, so the fake vendor serves a real 1 s clip."""
    out = tmp_path / "vendor.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x112:rate=8:duration=1",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    return out.read_bytes()

MODEL = """
[models."cloud-a"]
kind = "video.generate"
provider = "remote_video"
remote = true
service = "cloudvideo"
api_key_env = "TEST_CLOUDVIDEO_KEY"
price_per_second = 0.5
currency = "USD"
timeout_sec = 30
license_tag = "vendor"
[models."cloud-a".params]
duration = 4
[models."cloud-a".api]
submit_path = "/v1/videos"
submit_body = { model = "m1", prompt = "${prompt}", image = "${image}", duration = "${duration}", note = "${missing}" }
task_id_path = "data.task_id"
status_path = "/v1/videos/${task_id}"
status_field = "data.status"
progress_field = "data.progress"
result_url_field = "data.video_url"
error_field = "data.error"
cancel_path = "/v1/videos/${task_id}/cancel"
[models."fake-heavy"]
kind = "video.generate"
provider = "fake"
heavy = true
est_memory_gib = 40
timeout_sec = 30
"""


def make_settings(tmp_path: Path, url: str, *, allow: bool = True) -> Settings:
    home = tmp_path / "home"
    (home / "config").mkdir(parents=True)
    (home / "config" / "models.toml").write_text(MODEL)
    (home / "config" / "services.toml").write_text(
        f'[services.cloudvideo]\nurl = "{url}"\nhealth_path = "/"\n'
        f"[runtime]\npoll_interval_seconds = 0.01\nheartbeat_seconds = 0.05\nlease_seconds = 5\ncaffeinate = false\n"
        f"allow_remote = {str(allow).lower()}\n")
    return load_settings({"STORY_AGENT_HOME": str(home)})


@pytest.fixture
def vendor(tmp_path: Path) -> Iterator[FakeServer]:
    with FakeServer() as srv:
        srv.route("POST", "/v1/videos", {"data": {"task_id": "T1"}})
        srv.route("GET", "/v1/videos/T1", [
            {"data": {"status": "running", "progress": 40}},
            {"data": {"status": "succeeded", "progress": 100, "video_url": "/files/out.mp4"}},
        ])
        srv.route("GET", "/files/out.mp4", real_mp4(tmp_path) if Path(FFMPEG).exists() else b"x")
        yield srv


def drain(settings: Settings) -> Ledger:
    db = Database(settings.db_path)
    migrate(db)
    reg = AdapterRegistry()
    reg.register(RemoteVideoAdapter(settings, poll_interval_sec=0.01))
    assert Worker(settings, db, reg, probes=[]).drain(20)
    return Ledger(db, settings)


def submit(settings: Settings, **kw: Any) -> str:
    db = Database(settings.db_path)
    migrate(db)
    rec, _ = Ledger(db, settings).enqueue(MediaJob(kind=MediaKind.VIDEO_GENERATE, model_ref="cloud-a",
                                                   inputs={"prompt": "a cat"}, **kw), {})
    return rec.id


@needs_ffmpeg
def test_submit_poll_download_and_record(tmp_path: Path, vendor: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "sekret")
    settings = make_settings(tmp_path, vendor.url)
    jid = submit(settings)
    ledger = drain(settings)
    job = ledger.get_job(jid)
    assert job.status.value == "completed" and job.external_id == "T1", (job.error_code, job.error_message)
    body = vendor.requests[0].json
    assert body == {"model": "m1", "prompt": "a cat", "duration": 4}   # ${image}/${missing} dropped, native type
    asset = ledger.list_assets(job_id=jid)[0]
    assert asset.metadata["remote"] is True and asset.metadata["estimated_cost"] == 2.0
    assert asset.provenance["remote"] is True and (settings.home / asset.path).stat().st_size > 100
    assert all(r.body == b"" or b"sekret" not in r.body for r in vendor.requests)   # the key is never in a payload


def test_nothing_is_sent_to_the_cloud_until_it_is_allowed(
    tmp_path: Path, vendor: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY_ENV, "sekret")
    settings = make_settings(tmp_path, vendor.url, allow=False)
    jid = submit(settings)
    ledger = drain(settings)
    assert ledger.get_job(jid).error_code == "remote_not_allowed" and vendor.requests == []
    again = submit(settings, seed=2)                                              # a project may allow it for itself
    db = Database(settings.db_path)
    db.conn.execute("UPDATE media_jobs SET status='queued', error_code=NULL WHERE id=?", (again,))
    assert compliance.remote_allowed(settings, {"allow_remote": True}) and not compliance.remote_allowed(settings, {})


def test_a_missing_key_is_reported_by_name(tmp_path: Path, vendor: FakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    settings = make_settings(tmp_path, vendor.url)
    jid = submit(settings)
    job = drain(settings).get_job(jid)
    assert job.error_code == "missing_credentials" and KEY_ENV in (job.error_message or "") and vendor.requests == []


@needs_ffmpeg
def test_the_key_goes_only_to_the_vendors_own_host(
    tmp_path: Path, vendor: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY_ENV, "sekret")
    with FakeServer() as cdn:
        clip = real_mp4(tmp_path)
        cdn.route("GET", "/clip.mp4", clip)
        vendor.route("GET", "/v1/videos/T1", {"data": {"status": "succeeded", "video_url": cdn.url + "/clip.mp4"}})
        settings = make_settings(tmp_path, vendor.url)
        jid = submit(settings)
        ledger = drain(settings)
        assert (settings.home / ledger.list_assets(job_id=jid)[0].path).read_bytes() == clip
        assert len(cdn.requests) == 1
    api_calls = [r for r in vendor.requests if r.path.startswith("/v1")]
    assert api_calls                                                              # the vendor's API got the calls


def test_a_failed_cloud_task_fails_the_job_with_the_vendors_reason(
    tmp_path: Path, vendor: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(KEY_ENV, "sekret")
    vendor.route("GET", "/v1/videos/T1", {"data": {"status": "failed", "error": "content policy"}})
    settings = make_settings(tmp_path, vendor.url)
    jid = submit(settings)
    job = drain(settings).get_job(jid)
    assert job.status.value == "workflow_error" and "content policy" in (job.error_message or "")


def test_a_cloud_job_runs_next_to_a_local_heavy_job(tmp_path: Path, vendor: FakeServer) -> None:
    settings = make_settings(tmp_path, vendor.url)
    db = Database(settings.db_path)
    migrate(db)
    ledger = Ledger(db, settings)
    heavy, _ = ledger.enqueue(MediaJob(kind=MediaKind.VIDEO_GENERATE, model_ref="fake-heavy", inputs={}), {})
    cloud, _ = ledger.enqueue(MediaJob(kind=MediaKind.VIDEO_GENERATE, model_ref="cloud-a", inputs={"prompt": "x"}), {})
    assert can_start(cloud, [heavy], settings).allowed                           # no local memory/GPU involved
    assert not can_start(ledger.enqueue(MediaJob(kind=MediaKind.VIDEO_GENERATE, model_ref="fake-heavy",
                                                 inputs={}, seed=9), {})[0], [heavy], settings).allowed


def test_templates_and_paths() -> None:
    assert fill({"a": "${x}", "b": "n=${x}", "c": "${nope}", "d": ["${x}", "${nope}"]}, {"x": 3}) == {
        "a": 3, "b": "n=3", "d": [3]}
    assert dig({"a": {"b": [{"c": 1}]}}, "a.b.0.c") == 1 and dig({"a": 1}, "a.b") is None
