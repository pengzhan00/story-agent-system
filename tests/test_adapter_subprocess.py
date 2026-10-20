"""CosyVoice and whisper adapters against stub tools (real subprocesses, real worker, no models needed)."""
from __future__ import annotations

import json
import shutil
import stat
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from storyagent.config import Settings
from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.cosyvoice import CosyVoiceAdapter
from storyagent.media.adapters.whisper import WhisperAdapter
from storyagent.media.worker import spawn

from .adapter_kit import Rig, job, make_rig, make_settings, wav_bytes

FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None and not FFMPEG_FULL.exists(),
                                  reason="ffmpeg not installed")

COSY_MODELS = """
[models."cosy-test"]
kind = "audio.tts"
provider = "cosyvoice"
timeout_sec = 30
[models."asr-test"]
kind = "text.asr"
provider = "whisper"
timeout_sec = 30
"""

STUB_RUNNER = textwrap.dedent('''
    import json, sys, time, wave, struct, os
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    with open(os.path.join(os.path.dirname(__file__), "calls.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(cfg, ensure_ascii=False) + "\\n")
    print(json.dumps({"event": "loaded", "sample_rate": 8000}), flush=True)
    os.makedirs(cfg["out_dir"], exist_ok=True)
    failed = 0
    for i, it in enumerate(cfg["items"]):
        if it["text"] == "HANG":
            time.sleep(60)
        if it["text"] == "FAIL":
            failed += 1
            print(json.dumps({"event": "failed", "id": it["id"], "error": "boom"}), flush=True)
            continue
        with wave.open(os.path.join(cfg["out_dir"], it["id"] + ".wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
            w.writeframes(b"".join(struct.pack("<h", 3000) for _ in range(4000 + 800 * len(it["text"]))))
        print(json.dumps({"event": "item", "id": it["id"], "index": i}), flush=True)
    sys.exit(1 if failed else 0)
''')

STUB_WHISPER = textwrap.dedent(f'''\
    #!{sys.executable}
    import sys
    a = sys.argv
    prefix = a[a.index("-of") + 1]
    open(prefix + ".txt", "w", encoding="utf-8").write("hello brave world\\n")
    open(prefix + ".srt", "w", encoding="utf-8").write("1\\n00:00:00,000 --> 00:00:01,000\\nhello brave world\\n")
''')


def calls(tmp_path: Path) -> list[dict[str, Any]]:
    f = tmp_path / "cv" / "calls.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


def build(tmp_path: Path, *, whisper: bool = True, model: bool = True) -> tuple[Rig, Path]:
    cv = tmp_path / "cv"
    cv.mkdir(parents=True, exist_ok=True)
    runner = cv / "runner.py"
    runner.write_text(STUB_RUNNER)
    if model:
        (cv / "model").mkdir(exist_ok=True)
    stub = tmp_path / "whisper-stub"
    stub.write_text(STUB_WHISPER)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    wmodel = tmp_path / "w.bin"
    wmodel.write_bytes(b"x")
    services = f"""
[tools.cosyvoice]
python = "{sys.executable}"
workdir = "{cv}"
model_dir = "{cv / 'model'}"
[tools.whisper]
binary = "{stub if whisper else tmp_path / 'absent'}"
model = "{wmodel}"
"""
    s = make_settings(tmp_path, services=services, models=COSY_MODELS)
    rig = make_rig(s, CosyVoiceAdapter(runner=runner, poll_interval_sec=0.02))
    rig.worker.registry.register(WhisperAdapter(poll_interval_sec=0.02))
    ref = tmp_path / "ref.wav"
    ref.write_bytes(wav_bytes(4.0))
    return rig, ref


def tts(ref: Path, **over: Any) -> Any:
    inputs = {"text": "hello there", "voice_ref": {"audio": str(ref), "text": "reference words", "consent": True}}
    inputs.update(over.pop("inputs", {}))
    return job(MediaKind.AUDIO_TTS, "cosy-test", inputs, **over)


# ---- CosyVoice --------------------------------------------------------------------------------
def test_single_line(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    rec, assets = rig.run(tts(ref, seed=5, params={"speed": 1.2}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    [a] = [x for x in assets if x.kind == "audio.tts"]
    assert a.slot == "main" and a.path.endswith(".wav") and a.has_audio and a.duration_sec and a.duration_sec > 0.5
    assert a.metadata["ref_text"] == "reference words" and a.metadata["speed"] == 1.2
    [cfg] = calls(tmp_path)
    assert cfg["items"] == [{"id": "line", "text": "hello there"}] and cfg["seed"] == 5 and cfg["mode"] == "zero_shot"
    assert Path(cfg["ref_wav"]) == ref and cfg["root"].endswith("cv")


def test_batch_lines_become_one_asset_each(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    lines = [{"id": "l01", "text": "first"}, {"id": "l02", "text": "a considerably longer second line"}]
    rec, assets = rig.run(tts(ref, inputs={"text": "", "lines": lines}))
    assert rec.status is JobStatus.COMPLETED
    by_slot = {a.slot: a for a in assets}
    assert set(by_slot) == {"l01", "l02"}
    assert by_slot["l02"].duration_sec > by_slot["l01"].duration_sec  # measured, not estimated
    assert len(calls(tmp_path)) == 1  # one model load for the whole batch


def test_consent_is_enforced_but_can_be_switched_off_per_project(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    no_consent = tts(ref, inputs={"voice_ref": {"audio": str(ref), "text": "t", "consent": False}})
    rec, _ = rig.run(no_consent)
    assert rec.error_code == ErrorCode.CONSENT_REQUIRED.value and not calls(tmp_path)
    rig.db.conn.execute("UPDATE projects SET settings=? WHERE id=1", (json.dumps({"compliance": {"enabled": False}}),))
    rec2, _ = rig.run(tts(ref, seed=9, inputs={"voice_ref": {"audio": str(ref), "text": "t", "consent": False}}))
    assert rec2.status is JobStatus.COMPLETED


@needs_ffmpeg
def test_reference_length_window(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    short = tmp_path / "short.wav"
    short.write_bytes(wav_bytes(1.0))
    rec, _ = rig.run(tts(short))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value and "3-20 s" in (rec.error_message or "")
    rec2, _ = rig.run(tts(short, seed=2, params={"ref_min_sec": 0.5}))  # the window is configurable
    assert rec2.status is JobStatus.COMPLETED


def test_input_validation(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    for n, inputs in enumerate([{"text": " "}, {"voice_ref": {"audio": str(tmp_path / "none.wav"), "consent": True}},
                                {"text": "", "lines": [{"id": "a b", "text": "x"}]},
                                {"text": "", "lines": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]}]):
        rec, _ = rig.run(tts(ref, seed=n, inputs=inputs))
        assert rec.error_code == ErrorCode.INVALID_INPUT.value, (n, rec.error_message)
    rec, _ = rig.run(tts(ref, seed=7, params={"mode": "instruct"}))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value and "instruct" in (rec.error_message or "")
    assert not calls(tmp_path)


@needs_ffmpeg
def test_missing_reference_transcript_is_transcribed_with_whisper(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    rec, _ = rig.run(tts(ref, inputs={"voice_ref": {"audio": str(ref), "consent": True}}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert calls(tmp_path)[0]["ref_text"] == "hello brave world"
    assert "ref_transcribed" in [e["event"] for e in rig.ledger.events(rec.id)]


def test_missing_transcript_without_whisper_is_invalid(tmp_path: Path) -> None:
    rig, ref = build(tmp_path, whisper=False)
    rec, _ = rig.run(tts(ref, inputs={"voice_ref": {"audio": str(ref), "consent": True}}))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value and "transcript" in (rec.error_message or "")
    rec2, _ = rig.run(tts(ref, seed=3, params={"mode": "cross_lingual"},
                          inputs={"voice_ref": {"audio": str(ref), "consent": True}}))
    assert rec2.status is JobStatus.COMPLETED  # cross-lingual needs no transcript


def test_runtime_and_model_checks(tmp_path: Path) -> None:
    rig, ref = build(tmp_path, model=False)
    rec, _ = rig.run(tts(ref))
    assert rec.error_code == ErrorCode.MISSING_MODEL.value
    broken = make_settings(tmp_path / "b", services=f'[tools.cosyvoice]\npython = "{tmp_path / "nope"}"\n',
                           models=COSY_MODELS)
    r2 = make_rig(broken, CosyVoiceAdapter(runner=tmp_path / "cv" / "runner.py"))
    rec2, _ = r2.run(tts(ref))
    assert rec2.error_code == ErrorCode.MISSING_TOOL.value


def test_tool_failure_reports_the_log_tail(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    rec, assets = rig.run(tts(ref, inputs={"text": "FAIL"}))
    assert rec.status is JobStatus.WORKFLOW_ERROR and rec.error_code == ErrorCode.TOOL_FAILED.value
    assert "boom" in (rec.error_message or "") and not assets


@needs_ffmpeg
def test_mp3_output(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    rec, assets = rig.run(tts(ref, params={"output_format": "mp3"}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert [a.path for a in assets if a.kind == "audio.tts"][0].endswith(".mp3")


def test_cancel_terminates_the_process(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    rec = rig.submit(tts(ref, inputs={"text": "HANG"}))
    thread, stop = spawn(rig.worker)
    try:
        deadline = time.time() + 15
        while rig.ledger.get_job(rec.id).status is not JobStatus.RUNNING and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.5)  # let the subprocess actually start
        rig.ledger.request_cancel(rec.id)
        while not rig.ledger.get_job(rec.id).status.is_terminal and time.time() < deadline:
            time.sleep(0.05)
    finally:
        stop()
        thread.join(timeout=10)
    assert rig.ledger.get_job(rec.id).status is JobStatus.CANCELLED
    adapter = rig.worker.registry.get("cosyvoice")
    assert isinstance(adapter, CosyVoiceAdapter) and not adapter._procs  # nothing left running


def test_progress_from_the_log() -> None:
    a = CosyVoiceAdapter()
    j = type("J", (), {"inputs": {"lines": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]}})()
    assert a.progress(j, "")[1] == "loading model"  # type: ignore[arg-type]
    log = '{"event": "loaded"}\n{"event": "item", "id": "a"}\n'
    frac, msg = a.progress(j, log)  # type: ignore[arg-type]
    assert msg == "1/2 lines" and 0.4 < frac < 0.6


# ---- whisper ----------------------------------------------------------------------------------
@needs_ffmpeg
def test_transcript_and_srt(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    rec, assets = rig.run(job(MediaKind.TEXT_ASR, "asr-test", {"audio": str(ref)}, params={"language": "en"}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    by_slot = {a.slot: a for a in assets}
    assert set(by_slot) == {"main", "srt"} and by_slot["main"].metadata["text"] == "hello brave world"
    home = rig.settings.home
    assert (home / by_slot["main"].path).read_text("utf-8").strip() == "hello brave world"
    assert "-->" in (home / by_slot["srt"].path).read_text("utf-8")


def test_asr_input_and_tool_checks(tmp_path: Path) -> None:
    rig, ref = build(tmp_path)
    rec, _ = rig.run(job(MediaKind.TEXT_ASR, "asr-test", {"audio": str(tmp_path / "none.wav")}))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value
    rec2, _ = rig.run(job(MediaKind.TEXT_ASR, "asr-test", {}, seed=1))
    assert rec2.error_code == ErrorCode.INVALID_INPUT.value
    rig_nw, ref2 = build(tmp_path / "x", whisper=False)
    rec3, _ = rig_nw.run(job(MediaKind.TEXT_ASR, "asr-test", {"audio": str(ref2)}))
    assert rec3.error_code == ErrorCode.MISSING_TOOL.value


def test_registered_and_configured_by_default(tmp_path: Path) -> None:
    from storyagent.media.registry import default_registry

    s: Settings = make_settings(tmp_path)
    reg = default_registry(s)
    assert {"acestep", "phosphene", "cosyvoice", "whisper"} <= set(reg.providers())
    assert s.default_model("text.asr").provider == "whisper" and s.default_model("audio.tts").provider == "cosyvoice"
    assert s.model("cosyvoice2-0.5b").params["ref_min_sec"] == 3
