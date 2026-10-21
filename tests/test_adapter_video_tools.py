"""h3 (video+audio) and LongCat (talking head) adapters against stub executables."""
from __future__ import annotations

import json
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.h3 import H3Adapter, snap_frames
from storyagent.media.adapters.longcat import LongCatAdapter
from storyagent.media.probe import find_tool

from .adapter_kit import Rig, job, make_rig, make_settings

H3_STUB = textwrap.dedent(f'''\
    #!{sys.executable}
    import json, os, sys
    a = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as fh:
        rec = {{"argv": a, "path": os.environ.get("PATH", ""), "cwd": os.getcwd()}}
        fh.write(json.dumps(rec, ensure_ascii=False) + "\\n")
    prompt = a[a.index("-p") + 1]
    for i in range(1, 4):
        print(f"denoising step {{i}}/3", flush=True)
    if prompt == "FAIL":
        print("h3: out of memory", flush=True)
        sys.exit(3)
    if prompt != "NOFILE":
        import shutil
        shutil.copy(os.path.join(here, "tiny.mp4"), a[a.index("-o") + 1])
''')
LONGCAT_STUB = textwrap.dedent('''\
    import json, os, sys
    a = sys.argv[1:]
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "calls.jsonl"), "a", encoding="utf-8") as fh:
        env = {k: v for k, v in os.environ.items() if k.startswith(("LONGCAT_", "HF_", "TRANSFORMERS_"))}
        fh.write(json.dumps({"argv": a, "env": env}) + "\\n")
    for i in range(1, 4):
        print(f"step {i}/3", flush=True)
    if os.environ.get("LONGCAT_PROMPT") != "NOFILE":
        import shutil
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiny.mp4"), a[a.index("--out") + 1])
''')


def tiny_mp4(dest: Path) -> None:
    """A real (1 s, 64x112) clip: the worker checks every output with ffprobe."""
    tool = find_tool(make_settings(dest.parent / "probe-home"), "ffmpeg")
    if tool is None:
        pytest.skip("ffmpeg not installed")
    subprocess.run([str(tool), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x112:rate=8:duration=1",
                    "-pix_fmt", "yuv420p", str(dest)], check=True)


def stub_ffmpeg(tmp_path: Path) -> Path:
    f = tmp_path / "bin" / "ffmpeg"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("#!/bin/sh\nexit 0\n")
    f.chmod(f.stat().st_mode | stat.S_IXUSR)
    return f


def build_h3(tmp_path: Path, *, binary: bool = True, weights: bool = True) -> Rig:
    root = tmp_path / "h3tool"
    root.mkdir(parents=True, exist_ok=True)
    if binary:
        (root / "h3").write_text(H3_STUB)
        (root / "h3").chmod(0o755)
        tiny_mp4(root / "tiny.mp4")
    model = tmp_path / "minimax"
    if weights:
        model.mkdir(exist_ok=True)
        (model / "model_index.json").write_text("{}")
    ff = stub_ffmpeg(tmp_path)
    services = f'[tools.h3]\nbinary = "{root / "h3"}"\nmodel_dir = "{model}"\n[tools.ffmpeg]\nbinary = "{ff}"\n'
    models = """
[models."h3-test"]
kind = "video.av"
provider = "h3"
license_tag = "minimax-h3-community"
commercial_ok = true
attribution = "MiniMax H3"
timeout_sec = 30
"""
    return make_rig(make_settings(tmp_path, services=services, models=models), H3Adapter(poll_interval_sec=0.02))


def h3_calls(tmp_path: Path) -> list[dict]:
    f = tmp_path / "h3tool" / "calls.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_h3_frames_snap_to_5_plus_17n() -> None:
    assert [snap_frames(x) for x in (1, 22, 56, 57, 70, 120)] == [22, 22, 56, 56, 73, 124]


def test_h3_generates_with_a_first_frame_and_carries_the_attribution(tmp_path: Path) -> None:
    rig = build_h3(tmp_path)
    frame = tmp_path / "first.png"
    frame.write_bytes(b"png")
    rec, [a] = rig.run(job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "a knight rides", "refs": [str(frame)]}, seed=9,
                           params={"width": 480, "height": 864, "frames": 60, "steps": 12, "layers": 40, "reuse": 3}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    [call] = h3_calls(tmp_path)
    argv = call["argv"]
    assert flag(argv, "-d") == str(tmp_path / "minimax") and flag(argv, "-p") == "a knight rides"
    assert (flag(argv, "--width"), flag(argv, "--height"), flag(argv, "--frames")) == ("480", "864", "56")
    assert (flag(argv, "--steps"), flag(argv, "--layers"), flag(argv, "--reuse"), flag(argv, "--seed")) == (
        "12", "40", "3", "9")
    assert flag(argv, "--first-frame") == str(frame) and "--ref-image" not in argv
    assert call["path"].startswith(str(tmp_path / "bin"))                  # h3 shells out to ffmpeg
    assert Path(call["cwd"]).resolve() == (tmp_path / "h3tool").resolve()     # it finds its Metal shader there
    assert a.metadata["attribution"] == "MiniMax H3" and a.metadata["engine"] == "h3.c"


def test_h3_roles_of_reference_files(tmp_path: Path) -> None:
    rig = build_h3(tmp_path)
    img1, img2, wav = tmp_path / "a.png", tmp_path / "b.png", tmp_path / "v.wav"
    for f in (img1, img2, wav):
        f.write_bytes(b"x")
    rig.run(job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "p", "refs": [str(img1), str(img2), str(wav)]}, seed=1))
    argv = h3_calls(tmp_path)[0]["argv"]
    assert flag(argv, "--first-frame") == str(img1) and flag(argv, "--last-frame") == str(img2)
    assert flag(argv, "--ref-audio") == str(wav)
    rig.run(job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "q", "mode": "ref2va", "refs": [str(img1), str(img2)]},
                seed=2))
    argv2 = h3_calls(tmp_path)[1]["argv"]
    assert argv2.count("--ref-image") == 2 and "--first-frame" not in argv2


def test_h3_validation_and_failures(tmp_path: Path) -> None:
    rig = build_h3(tmp_path)
    img = tmp_path / "i.png"
    img.write_bytes(b"x")
    cases = [
        (job(MediaKind.VIDEO_AV, "h3-test", {"prompt": " "}), ErrorCode.INVALID_INPUT),
        (job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "p", "refs": [str(tmp_path / "no.png")]}),
         ErrorCode.INVALID_INPUT),
        (job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "p", "last_frame": str(img)}), ErrorCode.INVALID_INPUT),
        (job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "p"}, params={"frames": 22, "seconds": 2}),
         ErrorCode.INVALID_INPUT),
        (job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "p"}, params={"layers": 41}), ErrorCode.INVALID_PARAMS),
    ]
    for n, (j, code) in enumerate(cases):
        rec, _ = rig.run(job(j.kind, j.model_ref, dict(j.inputs), params=dict(j.params), seed=n))
        assert rec.error_code == code.value, (n, rec.error_message)
    assert not h3_calls(tmp_path)
    rec, assets = rig.run(job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "FAIL"}, seed=50))
    assert rec.status is JobStatus.WORKFLOW_ERROR and "out of memory" in (rec.error_message or "") and not assets
    rec2, _ = rig.run(job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "NOFILE"}, seed=51))
    assert rec2.error_code == ErrorCode.OUTPUT_MISSING.value


def test_h3_missing_tool_and_weights(tmp_path: Path) -> None:
    rec, _ = build_h3(tmp_path / "a", binary=False).run(job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "p"}))
    assert rec.error_code == ErrorCode.MISSING_TOOL.value
    rec2, _ = build_h3(tmp_path / "b", weights=False).run(job(MediaKind.VIDEO_AV, "h3-test", {"prompt": "p"}))
    assert rec2.error_code == ErrorCode.MISSING_MODEL.value


def test_h3_progress_and_capabilities() -> None:
    a = H3Adapter()
    assert a.progress(None, "")[1] == "loading model"                      # type: ignore[arg-type]
    frac, msg = a.progress(None, "denoising step 2/4\n")                  # type: ignore[arg-type]
    assert msg == "step 2/4" and abs(frac - 0.5) < 0.01
    caps = a.capabilities()
    assert caps.heavy and MediaKind.VIDEO_AV in caps.kinds


# ---- LongCat -------------------------------------------------------------------------------------------------
def build_longcat(tmp_path: Path, *, variant: bool = True, script: bool = True) -> Rig:
    root = tmp_path / "longcat"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    if script:
        (root / "scripts" / "run_inference.py").write_text(LONGCAT_STUB)
        tiny_mp4(root / "scripts" / "tiny.mp4")
    weights = root / "weights"
    if variant:
        (weights / "LongCat-Video-Avatar-1.5-q4-dmd-merged").mkdir(parents=True, exist_ok=True)
    services = (f'[tools.longcat]\nroot = "{root}"\npython = "{sys.executable}"\nweights_dir = "{weights}"\n'
                'variant_dir = "LongCat-Video-Avatar-1.5-q4-dmd-merged"\n')
    models = """
[models."lc-test"]
kind = "video.avatar"
provider = "longcat"
timeout_sec = 30
"""
    return make_rig(make_settings(tmp_path, services=services, models=models), LongCatAdapter(poll_interval_sec=0.02))


def lc_calls(tmp_path: Path) -> list[dict]:
    f = tmp_path / "longcat" / "scripts" / "calls.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


def test_longcat_makes_a_talking_head_from_portrait_and_speech(tmp_path: Path) -> None:
    rig = build_longcat(tmp_path)
    face, speech = tmp_path / "face.png", tmp_path / "speech.wav"
    face.write_bytes(b"x")
    speech.write_bytes(b"y")
    rec, [a] = rig.run(job(MediaKind.VIDEO_AVATAR, "lc-test", {"prompt": "she greets the camera",
                                                                "refs": [str(face), str(speech)]}, seed=4,
                           params={"width": 832, "height": 480, "num_frames": 93}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    [call] = lc_calls(tmp_path)
    assert flag(call["argv"], "--weights") == str(tmp_path / "longcat" / "weights")      # the root, not the variant
    assert flag(call["argv"], "--variant") == "q4-merged"
    assert (flag(call["argv"], "--width"), flag(call["argv"], "--height"), flag(call["argv"], "--num-frames")) == (
        "832", "480", "93")
    assert flag(call["argv"], "--seed") == "4"
    assert call["env"] == {"LONGCAT_IMAGE": str(face), "LONGCAT_AUDIO": str(speech),
                           "LONGCAT_PROMPT": "she greets the camera",
                           "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_XET": "1"}   # no hub
    assert a.kind == "video.avatar" and a.metadata["engine"] == "longcat-avatar-1.5"


def test_longcat_validation(tmp_path: Path) -> None:
    rig = build_longcat(tmp_path)
    rec, _ = rig.run(job(MediaKind.VIDEO_AVATAR, "lc-test", {"prompt": "p"}))
    assert rec.error_code == ErrorCode.INVALID_INPUT.value
    rec, _ = build_longcat(tmp_path / "a", variant=False).run(job(MediaKind.VIDEO_AVATAR, "lc-test", {"prompt": "p"}))
    assert rec.error_code == ErrorCode.MISSING_MODEL.value                  # never downloaded
    rec, _ = build_longcat(tmp_path / "b", script=False).run(job(MediaKind.VIDEO_AVATAR, "lc-test", {"prompt": "p"}))
    assert rec.error_code == ErrorCode.MISSING_TOOL.value


def test_longcat_no_output_and_capabilities(tmp_path: Path) -> None:
    rig = build_longcat(tmp_path)
    face, speech = tmp_path / "f.png", tmp_path / "s.wav"
    face.write_bytes(b"x")
    speech.write_bytes(b"y")
    rec, assets = rig.run(job(MediaKind.VIDEO_AVATAR, "lc-test", {"prompt": "NOFILE", "image": str(face),
                                                                   "audio": str(speech)}, seed=1))
    assert rec.error_code == ErrorCode.OUTPUT_MISSING.value and not assets
    caps = LongCatAdapter().capabilities()
    assert caps.heavy and caps.kinds == (MediaKind.VIDEO_AVATAR,)
    assert "longcat" in __import__("storyagent.media.registry", fromlist=["default_registry"]).default_registry(
        rig.settings)

