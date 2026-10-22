from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters._http import ServiceClient
from storyagent.media.adapters.phosphene import PhospheneAdapter
from storyagent.media.contracts import HealthState
from storyagent.media.prompts import render_prompt

from .adapter_kit import job, make_rig, make_settings
from .fakes.http_server import FakeServer, RecordedRequest

MODELS = """
[models."ltx-test"]
kind = "video.av"
provider = "phosphene"
prompt_profile = "ltx_av"
heavy = true
timeout_sec = 30
[models."ltx-test".params]
width = 1024
height = 576
frames = 121
quality = "high"
[models."ltx-test-silent"]
kind = "video.generate"
provider = "phosphene"
timeout_sec = 30
[models."ltx-test-lipsync"]
kind = "video.lipsync"
provider = "phosphene"
timeout_sec = 30
"""
FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None and not FFMPEG_FULL.exists(),
                                  reason="ffmpeg not installed")


def form(req: RecordedRequest) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(req.body.decode()).items()}


def status(
    current: dict[str, Any] | None = None, queue: list | None = None, history: list | None = None
) -> dict[str, Any]:
    return {"running": bool(current), "paused": False, "current": current,
            "queue": queue or [], "history": history or []}


def make_video(path: Path, w: int = 64, h: int = 64, audio: bool = True) -> Path:
    ff = find_tool_path()
    args = [ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=red:s={w}x{h}:d=1:r=8"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", "-c:a", "aac"]
    args += ["-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True)
    return path


def find_tool_path() -> str:
    return shutil.which("ffmpeg") or str(FFMPEG_FULL)


@pytest.fixture
def panel(tmp_path: Path) -> Any:
    out = tmp_path / "panel_out"
    out.mkdir()
    state: dict[str, Any] = {"polls": 0, "video": out / "clip.mp4"}
    with FakeServer() as srv:
        srv.state = state  # type: ignore[attr-defined]
        srv.route("POST", "/queue/add", {"ok": True, "id": "pj-1"})

        def st(_: RecordedRequest) -> dict[str, Any]:
            state["polls"] += 1
            if state["polls"] < 3:
                return status(current={"id": "pj-1", "progress": {"pct": 40, "phase_label": "Stage 1"}})
            return status(history=[{"id": "pj-1", "status": "done", "output_path": str(state["video"])}])

        srv.route("GET", "/status", st)
        srv.route("POST", "/stop", {"ok": True})
        srv.route("POST", "/queue/remove", {"ok": True})
        yield srv


def rig(tmp_path: Path, panel: FakeServer):  # noqa: ANN201
    s = make_settings(tmp_path, services=f'[services.phosphene]\nurl = "{panel.url}"\n', models=MODELS)
    return make_rig(s, PhospheneAdapter(ServiceClient(s.service("phosphene")), poll_interval_sec=0.01))


@needs_ffmpeg
def test_text_to_video_with_sound(tmp_path: Path, panel: FakeServer) -> None:
    make_video(panel.state["video"])  # type: ignore[attr-defined]
    r = rig(tmp_path, panel)
    rec, [a] = r.run(job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "a knight rides", "negative_prompt": "blurry"},
                         seed=42, params={"upscale": "off"}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert a.kind == "video.av" and a.has_audio and a.width == 64 and a.duration_sec
    f = form(next(q for q in panel.requests if q.path == "/queue/add"))
    assert f["mode"] == "t2v" and f["prompt"] == "a knight rides" and f["negative_prompt"] == "blurry"
    assert f["seed"] == "42" and f["width"] == "576" and f["frames"] == "121" and f["quality"] == "high"
    assert a.metadata["panel_job_id"] == "pj-1"
    # the model lists 1024x576; the project frame is vertical, so 576x1024 is asked for - and the panel clamped it
    # down to 64x64: surfaced, not silent
    assert a.metadata["requested_size"] == [576, 1024] and a.metadata["actual_size"] == [64, 64]
    assert "resolution_clamped" in [e["event"] for e in r.ledger.events(rec.id)]


@needs_ffmpeg
def test_picture_only_strips_the_sound(tmp_path: Path, panel: FakeServer) -> None:
    make_video(panel.state["video"])  # type: ignore[attr-defined]
    r = rig(tmp_path, panel)
    rec, [a] = r.run(job(MediaKind.VIDEO_GENERATE, "ltx-test-silent", {"prompt": "a lake"}, params={"frames": 49}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert a.has_audio is False and a.metadata["audio_stripped"] is True
    r2 = rig(tmp_path / "b", panel)
    rec2, assets2 = r2.run(job(MediaKind.VIDEO_GENERATE, "ltx-test-silent", {"prompt": "a lake"},
                               params={"frames": 49, "keep_audio": True}))
    assert assets2[0].has_audio is True


def test_upscaled_sibling_is_preferred(tmp_path: Path, panel: FakeServer) -> None:
    clip = panel.state["video"]  # type: ignore[attr-defined]
    clip.write_bytes(b"raw")
    clip.with_name("clip_1080p.mp4").write_bytes(b"big")
    assert PhospheneAdapter._output({"output_path": str(clip)}) == clip.with_name("clip_1080p.mp4")
    clip.with_name("clip_1080p.mp4").unlink()
    assert PhospheneAdapter._output({"output_path": str(clip)}) == clip
    assert PhospheneAdapter._output({}) is None


@needs_ffmpeg
def test_image_to_video_and_lipsync_modes(tmp_path: Path, panel: FakeServer) -> None:
    make_video(panel.state["video"])  # type: ignore[attr-defined]
    img = tmp_path / "face.png"
    img.write_bytes(b"png")
    wav = tmp_path / "line.wav"
    wav.write_bytes(b"wav")
    r = rig(tmp_path, panel)
    rec, _ = r.run(job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "she turns", "refs": [str(img)]}, seed=1))
    assert rec.status is JobStatus.COMPLETED
    f = form(next(q for q in panel.requests if q.path == "/queue/add"))
    assert f["mode"] == "i2v" and f["image"] == str(img)

    panel.state["polls"] = 0  # type: ignore[attr-defined]
    panel.requests.clear()
    rec2, _ = r.run(job(MediaKind.VIDEO_LIPSYNC, "ltx-test-lipsync",
                        {"prompt": "", "refs": [str(img), {"role": "audio", "path": str(wav)}]}, seed=2))
    assert rec2.status is JobStatus.COMPLETED, (rec2.error_code, rec2.error_message)
    f2 = form(next(q for q in panel.requests if q.path == "/queue/add"))
    assert f2["mode"] == "a2v" and f2["image"] == str(img) and f2["audio"] == str(wav) and f2["enhance"] == "false"


def test_input_validation_blocks_submission(tmp_path: Path, panel: FakeServer) -> None:
    r = rig(tmp_path, panel)
    cases = [
        (job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "x"}, params={"frames": 120}), ErrorCode.INVALID_PARAMS),
        (job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "x"}, params={"width": 1000}), ErrorCode.INVALID_PARAMS),
        (job(MediaKind.VIDEO_AV, "ltx-test", {}), ErrorCode.INVALID_INPUT),
        (job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "x", "refs": [str(tmp_path / "nope.png")]}),
         ErrorCode.INVALID_INPUT),
        (job(MediaKind.VIDEO_LIPSYNC, "ltx-test-lipsync", {"refs": []}), ErrorCode.INVALID_INPUT),
    ]
    for n, (j, code) in enumerate(cases):
        rec, _ = r.run(job(j.kind, j.model_ref, dict(j.inputs), params=dict(j.params), seed=n))
        assert rec.status is JobStatus.FAILED_VALIDATION and rec.error_code == code.value, (n, rec.error_message)
    assert not any(q.path == "/queue/add" for q in panel.requests)


def test_long_clip_without_high_quality_warns_about_identity(tmp_path: Path, panel: FakeServer) -> None:
    panel.state["video"].write_bytes(b"x")  # type: ignore[attr-defined]
    r = rig(tmp_path, panel)
    rec, _ = r.run(job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "x"}, seed=1,
                       params={"frames": 169, "quality": "balanced"}))
    assert "identity_risk" in [e["event"] for e in r.ledger.events(rec.id)]


def test_panel_failure_is_a_workflow_error(tmp_path: Path, panel: FakeServer) -> None:
    panel.route("GET", "/status", status(history=[{"id": "pj-1", "status": "failed", "error": "OOM in stage 2"}]))
    r = rig(tmp_path, panel)
    rec, assets = r.run(job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "x"}, seed=1))
    assert rec.status is JobStatus.WORKFLOW_ERROR and "OOM in stage 2" in (rec.error_message or "") and not assets


def test_done_but_file_missing_is_not_completed(tmp_path: Path, panel: FakeServer) -> None:
    # the panel says "done" for a file that is not there
    r = rig(tmp_path, panel)
    rec, assets = r.run(job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "x"}, seed=1))
    assert rec.error_code == ErrorCode.OUTPUT_MISSING.value and not assets


def test_refused_submission_is_a_transport_error(tmp_path: Path, panel: FakeServer) -> None:
    panel.route("POST", "/queue/add", (400, {"error": "Image mode needs a picture", "code": "input_missing"}))
    r = rig(tmp_path, panel)
    rec, _ = r.run(job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "x"}, seed=1))
    assert rec.status is JobStatus.TRANSPORT_ERROR and rec.error_code == ErrorCode.SERVICE_REJECTED.value
    assert "Image mode needs a picture" in (rec.error_message or "")


@needs_ffmpeg
def test_reattach_to_a_running_render_does_not_resubmit(tmp_path: Path, panel: FakeServer) -> None:
    make_video(panel.state["video"])  # type: ignore[attr-defined]
    r = rig(tmp_path, panel)
    rec = r.submit(job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "x"}, seed=1, params={"upscale": "off"}))
    r.ledger.claim(rec.id, "dead", 0.0)
    r.ledger.set_external_id(rec.id, "dead", "pj-1")
    r.ledger.requeue_expired()
    assert r.worker.drain(20)
    assert r.ledger.get_job(rec.id).status is JobStatus.COMPLETED
    assert not any(q.path == "/queue/add" for q in panel.requests)
    assert "reattached" in [e["event"] for e in r.ledger.events(rec.id)]


def test_forgotten_job_is_resubmitted(tmp_path: Path, panel: FakeServer) -> None:
    panel.route("GET", "/status", status())  # the restarted panel lost everything
    r = rig(tmp_path, panel)
    rec = r.submit(job(MediaKind.VIDEO_AV, "ltx-test", {"prompt": "x"}, seed=1))
    r.ledger.claim(rec.id, "dead", 0.0)
    r.ledger.set_external_id(rec.id, "dead", "gone")
    r.ledger.requeue_expired()
    r.worker.drain(5)
    events = [e["event"] for e in r.ledger.events(rec.id)]
    assert "resubmitting" in events and any(q.path == "/queue/add" for q in panel.requests)


def test_cancel_stops_the_running_job_and_removes_queued_ones(tmp_path: Path, panel: FakeServer) -> None:
    adapter = rig(tmp_path, panel).worker.registry.get("phosphene")
    assert adapter is not None
    panel.route("GET", "/status", status(current={"id": "pj-9"}))
    ctx = type("C", (), {"log": staticmethod(lambda *a, **k: None)})()
    adapter.cancel("pj-9", None, ctx)  # type: ignore[arg-type]
    assert [q.path for q in panel.requests if q.method == "POST"] == ["/stop"]
    assert panel.requests[-1].query["id"] == ["pj-9"]
    panel.route("GET", "/status", status(queue=[{"id": "pj-10"}]))
    adapter.cancel("pj-10", None, ctx)  # type: ignore[arg-type]
    assert panel.requests[-1].path == "/queue/remove" and b"pj-10" in panel.requests[-1].body


def test_health(tmp_path: Path, panel: FakeServer) -> None:
    adapter = rig(tmp_path, panel).worker.registry.get("phosphene")
    assert adapter is not None and adapter.health().state is HealthState.OK
    panel.route("GET", "/status", {**status(), "paused": True, "paused_reason": "low memory"})
    h = adapter.health()
    assert h.state is HealthState.DEGRADED and "low memory" in h.detail
    s = make_settings(tmp_path / "d", services='[services.phosphene]\nurl = "http://127.0.0.1:9"\n')
    assert PhospheneAdapter(ServiceClient(s.service("phosphene"), timeout=1)).health().state is HealthState.NOT_RUNNING


def test_prompt_dialect_puts_sound_and_speech_in_their_own_sections() -> None:
    out = render_prompt("ltx_av", "Night street, rain.", {
        "action": "She draws a sword.", "look": "well lit, crisp", "audio": "wind and steel",
        "dialogue": "Is that all you have?", "dialogue_language": "English"})
    assert out == ("Night street, rain. She draws a sword. well lit, crisp AUDIO: wind and steel "
                   "DIALOGUE in English: Is that all you have?")
    assert "DIALOGUE in Mandarin: x" in render_prompt("ltx_av", "", {"dialogue": "x"})
    assert render_prompt("ltx_av", "just text", None) == "just text"


def test_registered_and_default_models_resolve(tmp_path: Path) -> None:
    from storyagent.media.registry import default_registry

    s = make_settings(tmp_path)
    assert "phosphene" in default_registry(s)
    for kind in ("video.av", "video.generate", "video.lipsync"):
        assert s.default_model(kind).provider == "phosphene"
    final = s.model("ltx-2.5-av")
    assert (final.params["width"], final.params["height"], final.params["stage1_steps"]) == (832, 1536, 16)
    assert final.tiers["draft"]["quality"] == "balanced" and final.license_tag == "ltx-community"


def test_the_style_avoid_list_is_added_to_the_models_not_a_replacement_for_it() -> None:
    from storyagent.media.adapters.phosphene import _negatives

    assert _negatives("warm colors, blurry", "blurry, banding, ghosting") == "warm colors, blurry, banding, ghosting"
    assert _negatives("", "banding") == "banding" and _negatives(None, None) == "" and _negatives("a", "") == "a"
