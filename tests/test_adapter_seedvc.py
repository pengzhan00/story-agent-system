from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

from storyagent.domain import JobStatus, MediaKind
from storyagent.media.adapters.seedvc import SeedVcAdapter

from .adapter_kit import Rig, job, make_rig, make_settings, wav_bytes

INFERENCE = textwrap.dedent('''\
    import json, os, sys
    a = sys.argv[1:]
    out = a[a.index("--output") + 1]
    os.makedirs(out, exist_ok=True)
    json.dump({"script": os.path.basename(__file__), "argv": a}, open(os.path.join(os.getcwd(), "argv.json"), "w"))
    if os.path.exists("FAILNOW"):
        sys.exit(1)
    if not os.path.exists("NOOUT"):
        open(os.path.join(out, "vc_x.wav"), "wb").write(open(a[a.index("--source") + 1], "rb").read())
''')
DEMUCS = textwrap.dedent('''\
    import os, shutil, sys
    a = sys.argv[1:]
    out, src = a[a.index("-o") + 1], a[-1]
    d = os.path.join(out, a[a.index("-n") + 1], os.path.splitext(os.path.basename(src))[0])
    os.makedirs(d, exist_ok=True)
    shutil.copy(src, os.path.join(d, "vocals.wav"))
    shutil.copy(src, os.path.join(d, "no_vocals.wav"))
    open(os.path.join(os.getcwd(), "demucs_argv.txt"), "w").write(" ".join(a))
''')
FFMPEG = f"#!{sys.executable}\nimport shutil, sys\nshutil.copy(sys.argv[sys.argv.index('-i') + 1], sys.argv[-1])\n"

MODELS = """
[models."vc2-test"]
kind = "audio.convert"
provider = "seedvc"
timeout_sec = 60
engine = "v2"
ar_checkpoint = "checkpoints/ar.pth"
cfm_checkpoint = "checkpoints/cfm.pth"
[models."vc2-test".params]
diffusion_steps = 20
[models."vc1-test"]
kind = "audio.convert"
provider = "seedvc"
timeout_sec = 60
engine = "v1"
[models."vc1-test".params]
f0_condition = true
"""


def build(tmp_path: Path, *, runtime: bool = True, checkpoints: bool = True) -> tuple[Rig, Path, Path, Path]:
    root = tmp_path / "seedvc"
    (root / "checkpoints").mkdir(parents=True, exist_ok=True)
    py = tmp_path / "py-stub"
    ffm = tmp_path / "ffmpeg-stub"
    if runtime:
        (root / "inference_v2.py").write_text(INFERENCE)
        (root / "inference.py").write_text(INFERENCE)
        (root / "demucs").mkdir(exist_ok=True)
        (root / "demucs" / "__init__.py").write_text("")
        (root / "demucs" / "separate.py").write_text(DEMUCS)
        py.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
        py.chmod(py.stat().st_mode | stat.S_IXUSR)
        ffm.write_text(FFMPEG)
        ffm.chmod(ffm.stat().st_mode | stat.S_IXUSR)
    if checkpoints:
        (root / "checkpoints" / "ar.pth").write_bytes(b"x")
        (root / "checkpoints" / "cfm.pth").write_bytes(b"x")
    s = make_settings(tmp_path, services=f'[tools.seedvc]\nroot = "{root}"\npython = "{py}"\n'
                                         f'[tools.ffmpeg]\nbinary = "{ffm}"\n', models=MODELS)
    src, ref = tmp_path / "src.wav", tmp_path / "ref.wav"
    src.write_bytes(wav_bytes(0.5))
    ref.write_bytes(wav_bytes(6))
    return make_rig(s, SeedVcAdapter(poll_interval_sec=0.02)), root, src, ref


def inputs(src: Path, ref: Path, consent: bool = True) -> dict[str, object]:
    return {"audio": str(src), "voice_ref": {"audio": str(ref), "consent": consent}}


def seen(root: Path) -> dict:
    return json.loads((root / "argv.json").read_text())


def test_v2_converts_with_explicit_local_checkpoints(tmp_path: Path) -> None:
    rig, root, src, ref = build(tmp_path)
    rec, [a] = rig.run(job(MediaKind.AUDIO_CONVERT, "vc2-test", inputs(src, ref),
                           params={"similarity_cfg_rate": 0.9, "convert_style": True}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert a.kind == "audio.convert" and a.path.endswith(".wav") and a.duration_sec and a.duration_sec > 0.4
    s = seen(root)
    argv = s["argv"]
    assert s["script"] == "inference_v2.py"
    assert argv[argv.index("--source") + 1] == str(src) and argv[argv.index("--target") + 1] == str(ref)
    assert argv[argv.index("--diffusion-steps") + 1] == "20" and argv[argv.index("--similarity-cfg-rate") + 1] == "0.9"
    assert argv[argv.index("--convert-style") + 1] == "true"
    assert argv[argv.index("--ar-checkpoint-path") + 1] == str(root / "checkpoints" / "ar.pth")
    assert argv[argv.index("--cfm-checkpoint-path") + 1] == str(root / "checkpoints" / "cfm.pth")
    assert a.metadata["engine"] == "v2" and a.metadata["reference"] == "ref.wav" and not a.metadata["separate_vocals"]


def test_v1_engine_uses_its_own_script_and_flags(tmp_path: Path) -> None:
    rig, root, src, ref = build(tmp_path)
    rec, _ = rig.run(job(MediaKind.AUDIO_CONVERT, "vc1-test", inputs(src, ref), params={"semi_tone_shift": -12}))
    assert rec.status is JobStatus.COMPLETED, rec.error_message
    s = seen(root)
    assert s["script"] == "inference.py"
    assert s["argv"][s["argv"].index("--f0-condition") + 1] == "true"
    assert s["argv"][s["argv"].index("--semi-tone-shift") + 1] == "-12"
    assert "--ar-checkpoint-path" not in s["argv"]


def test_song_mode_converts_only_the_vocals_and_mixes_back(tmp_path: Path) -> None:
    rig, root, src, ref = build(tmp_path)
    rec, [a] = rig.run(job(MediaKind.AUDIO_CONVERT, "vc2-test", inputs(src, ref),
                           params={"separate_vocals": True, "accompaniment_gain": 0.8, "demucs_device": "cpu"}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert a.metadata["separate_vocals"] is True
    d = (root / "demucs_argv.txt").read_text()
    assert "--two-stems=vocals" in d and "-n htdemucs" in d and "-d cpu" in d
    assert "vocals.wav" in seen(root)["argv"][seen(root)["argv"].index("--source") + 1]  # the converter got the stem


def test_consent_is_required_for_the_reference_voice(tmp_path: Path) -> None:
    rig, _, src, ref = build(tmp_path)
    rec, _ = rig.run(job(MediaKind.AUDIO_CONVERT, "vc2-test", inputs(src, ref, consent=False)))
    assert rec.status is JobStatus.FAILED_VALIDATION and rec.error_code == "consent_required"


def test_validation_and_missing_pieces(tmp_path: Path) -> None:
    rig, _, src, ref = build(tmp_path)
    rec, _ = rig.run(job(MediaKind.AUDIO_CONVERT, "vc2-test", inputs(tmp_path / "nope.wav", ref)))
    assert rec.error_code == "invalid_input"
    short = tmp_path / "short.wav"
    short.write_bytes(wav_bytes(1))
    rec, _ = rig.run(job(MediaKind.AUDIO_CONVERT, "vc2-test", inputs(src, short)))
    assert rec.error_code == "invalid_input"
    rig2, _, src2, ref2 = build(tmp_path / "x", runtime=False)
    rec, _ = rig2.run(job(MediaKind.AUDIO_CONVERT, "vc2-test", inputs(src2, ref2)))
    assert rec.error_code == "missing_tool"
    rig3, _, src3, ref3 = build(tmp_path / "y", checkpoints=False)
    rec, _ = rig3.run(job(MediaKind.AUDIO_CONVERT, "vc2-test", inputs(src3, ref3)))
    assert rec.error_code == "missing_model"
    rec, _ = rig3.run(job(MediaKind.AUDIO_CONVERT, "vc1-test", inputs(src3, ref3)))   # v1 needs no named checkpoint
    assert rec.status is JobStatus.COMPLETED, rec.error_message


def test_a_failing_converter_is_a_tool_failure(tmp_path: Path) -> None:
    rig, root, src, ref = build(tmp_path)
    (root / "FAILNOW").write_text("")
    rec, _ = rig.run(job(MediaKind.AUDIO_CONVERT, "vc2-test", inputs(src, ref)))
    assert rec.status is JobStatus.WORKFLOW_ERROR and rec.error_code == "tool_failed"
