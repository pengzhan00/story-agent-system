from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

from storyagent.domain import JobStatus, MediaKind
from storyagent.media.adapters.seedvc import SeedVcAdapter

from .adapter_kit import Rig, job, make_rig, make_settings, wav_bytes

STUB = textwrap.dedent(f'''\
    #!{sys.executable}
    import json, os, sys
    a = sys.argv[1:]
    out = a[a.index("--output") + 1]
    os.makedirs(out, exist_ok=True)
    json.dump(a, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "argv.json"), "w"))
    if os.path.exists("FAILNOW"):
        sys.exit(1)
    sys.stdout.write("Loading models\\n")
    if not os.path.exists("NOOUT"):
        open(os.path.join(out, "vc_x_y.wav"), "wb").write(open(a[a.index("--source") + 1], "rb").read())
''')

MODELS = """
[models."vc-test"]
kind = "audio.convert"
provider = "seedvc"
timeout_sec = 30
[models."vc-test".params]
diffusion_steps = 20
f0_condition = false
"""


def build(tmp_path: Path, *, runtime: bool = True) -> tuple[Rig, Path, Path]:
    root = tmp_path / "seedvc"
    (root / "checkpoints").mkdir(parents=True, exist_ok=True)
    py = tmp_path / "py-stub"
    if runtime:
        (root / "inference.py").write_text(STUB)
        py.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
        py.chmod(py.stat().st_mode | stat.S_IXUSR)
    s = make_settings(tmp_path, services=f'[tools.seedvc]\nroot = "{root}"\npython = "{py}"\n', models=MODELS)
    src, ref = tmp_path / "src.wav", tmp_path / "ref.wav"
    src.write_bytes(wav_bytes(0.5))
    ref.write_bytes(wav_bytes(6))
    return make_rig(s, SeedVcAdapter(poll_interval_sec=0.02)), src, ref


def test_converts_and_passes_parameters(tmp_path: Path) -> None:
    rig, src, ref = build(tmp_path)
    rec, [a] = rig.run(job(MediaKind.AUDIO_CONVERT, "vc-test",
                           {"audio": str(src), "voice_ref": {"audio": str(ref), "consent": True}},
                           params={"inference_cfg_rate": 0.9, "f0_condition": True}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert a.kind == "audio.convert" and a.path.endswith(".wav") and a.duration_sec and a.duration_sec > 0.4
    argv = json.loads((tmp_path / "seedvc" / "argv.json").read_text())
    assert argv[argv.index("--source") + 1] == str(src) and argv[argv.index("--target") + 1] == str(ref)
    assert argv[argv.index("--diffusion-steps") + 1] == "20" and argv[argv.index("--inference-cfg-rate") + 1] == "0.9"
    assert argv[argv.index("--f0-condition") + 1] == "true"
    assert a.metadata["reference"] == "ref.wav"


def test_consent_is_required_for_the_reference_voice(tmp_path: Path) -> None:
    rig, src, ref = build(tmp_path)
    rec, _ = rig.run(job(MediaKind.AUDIO_CONVERT, "vc-test", {"audio": str(src), "voice_ref": {"audio": str(ref)}}))
    assert rec.status is JobStatus.FAILED_VALIDATION and rec.error_code == "consent_required"


def test_validation_and_missing_runtime(tmp_path: Path) -> None:
    rig, src, ref = build(tmp_path)
    ok = {"voice_ref": {"audio": str(ref), "consent": True}}
    rec, _ = rig.run(job(MediaKind.AUDIO_CONVERT, "vc-test", {"audio": str(tmp_path / "nope.wav"), **ok}))
    assert rec.error_code == "invalid_input"
    short = tmp_path / "short.wav"
    short.write_bytes(wav_bytes(1))
    rec, _ = rig.run(job(MediaKind.AUDIO_CONVERT, "vc-test",
                         {"audio": str(src), "voice_ref": {"audio": str(short), "consent": True}}))
    assert rec.error_code == "invalid_input"
    rig2, src2, ref2 = build(tmp_path / "x", runtime=False)
    rec, _ = rig2.run(job(MediaKind.AUDIO_CONVERT, "vc-test",
                          {"audio": str(src2), "voice_ref": {"audio": str(ref2), "consent": True}}))
    assert rec.error_code == "missing_tool"
