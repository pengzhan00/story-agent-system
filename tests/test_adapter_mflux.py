from __future__ import annotations

import json
import stat
import struct
import sys
import textwrap
import zlib
from pathlib import Path
from typing import Any

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.mflux import MfluxAdapter

from .adapter_kit import Rig, job, make_rig, make_settings


def png_bytes(w: int = 32, h: int = 48) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x80\x40\x20" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


STUB = textwrap.dedent(f'''\
    #!{sys.executable}
    import json, os, sys, struct, zlib
    a = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(a, ensure_ascii=False) + "\\n")
    prompt = a[a.index("--prompt") + 1]
    for i in range(1, 4):
        print(f"  {{i}}/3 [00:0{{i}}<00:00, 1.00it/s]", flush=True)
    if prompt == "FAIL":
        print("Traceback: kaboom", flush=True)
        sys.exit(2)
    if prompt != "NOFILE":
        w = int(a[a.index("--width") + 1]) if "--width" in a else 32
        h = int(a[a.index("--height") + 1]) if "--height" in a else 48
        def chunk(tag, data):
            body = tag + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        raw = b"".join(b"\\x00" + b"\\x80\\x40\\x20" * w for _ in range(h))
        png = (b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        open(a[a.index("--output") + 1], "wb").write(png)
''')

MODELS = """
[models."img-test"]
kind = "image.generate"
provider = "mflux"
command = "stub-generate"
weights = "{weights}"
base_model = "flux2-klein-base-4b"
license_tag = "apache"
timeout_sec = 30
[models."edit-test"]
kind = "image.edit"
provider = "mflux"
command = "stub-generate"
weights = "{weights}"
timeout_sec = 30
"""


def build(tmp_path: Path, *, weights: bool = True, binary: bool = True) -> Rig:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    if binary:
        stub = bindir / "stub-generate"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    wdir = tmp_path / "weights"
    if weights:
        wdir.mkdir(exist_ok=True)
    s = make_settings(tmp_path, services=f'[tools.mflux]\nbin_dir = "{bindir}"\n',
                      models=MODELS.format(weights=wdir))
    return make_rig(s, MfluxAdapter(poll_interval_sec=0.02))


def calls(tmp_path: Path) -> list[list[str]]:
    f = tmp_path / "bin" / "calls.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_generates_an_image(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rec, [a] = rig.run(job(MediaKind.IMAGE_GENERATE, "img-test",
                           {"prompt": "a knight at dusk", "negative_prompt": "blurry"}, seed=7,
                           params={"width": 64, "height": 96, "steps": 12, "guidance": 3.5, "quantize": 8}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert a.kind == "image.generate" and a.path.endswith(".png") and (a.width, a.height) == (64, 96)
    [argv] = calls(tmp_path)
    assert flag(argv, "--prompt") == "a knight at dusk" and flag(argv, "--seed") == "7"
    assert flag(argv, "--model") == str(tmp_path / "weights") and flag(argv, "--base-model") == "flux2-klein-base-4b"
    assert (flag(argv, "--width"), flag(argv, "--height"), flag(argv, "--steps")) == ("64", "96", "12")
    assert flag(argv, "--guidance") == "3.5" and flag(argv, "--quantize") == "8"
    assert flag(argv, "--negative-prompt") == "blurry"
    assert a.metadata["seed"] == 7


def test_unset_parameters_are_left_to_the_tool(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rig.run(job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "x"}, seed=1))
    [argv] = calls(tmp_path)
    for f in ("--width", "--height", "--steps", "--guidance", "--quantize", "--low-ram", "--negative-prompt", "--lora"):
        assert f not in argv


def test_loras_init_image_and_edit_inputs(tmp_path: Path) -> None:
    rig = build(tmp_path)
    lora, ref = tmp_path / "hero.safetensors", tmp_path / "ref.png"
    lora.write_bytes(b"x")
    ref.write_bytes(png_bytes())
    rec, _ = rig.run(job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "p", "refs": [str(ref)]}, seed=1,
                         params={"loras": [{"path": str(lora), "scale": 0.8}], "image_strength": 0.6, "low_ram": True}))
    assert rec.status is JobStatus.COMPLETED
    argv = calls(tmp_path)[0]
    i = argv.index("--lora")
    assert argv[i + 1:i + 3] == [str(lora), "0.8"] and "--low-ram" in argv
    i = argv.index("--image")
    assert argv[i + 1:i + 3] == [str(ref), "0.6"]

    rec2, _ = rig.run(job(MediaKind.IMAGE_EDIT, "edit-test", {"prompt": "make it night", "refs": [str(ref)]}, seed=2))
    assert rec2.status is JobStatus.COMPLETED
    argv2 = calls(tmp_path)[1]
    assert flag(argv2, "--image-paths") == str(ref) and "--image" not in argv2


def test_validation(tmp_path: Path) -> None:
    rig = build(tmp_path)
    lora = tmp_path / "missing.safetensors"
    cases: list[tuple[Any, ErrorCode]] = [
        (job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": " "}), ErrorCode.INVALID_INPUT),
        (job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "x"}, params={"width": 100}), ErrorCode.INVALID_PARAMS),
        (job(MediaKind.IMAGE_EDIT, "edit-test", {"prompt": "x"}), ErrorCode.INVALID_INPUT),
        (job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "x", "refs": [str(tmp_path / "no.png")]}),
         ErrorCode.INVALID_INPUT),
        (job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "x"}, params={"loras": [{"path": str(lora)}]}),
         ErrorCode.INVALID_INPUT),
        (job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "x"}, params={"quantize": 7}), ErrorCode.INVALID_PARAMS),
    ]
    for n, (j, code) in enumerate(cases):
        rec, _ = rig.run(job(j.kind, j.model_ref, dict(j.inputs), params=dict(j.params), seed=n))
        assert rec.error_code == code.value, (n, rec.error_message)
    assert not calls(tmp_path)


def test_missing_tool_and_weights(tmp_path: Path) -> None:
    rec, _ = build(tmp_path / "a", binary=False).run(job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "x"}))
    assert rec.error_code == ErrorCode.MISSING_TOOL.value
    rec2, _ = build(tmp_path / "b", weights=False).run(job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "x"}))
    assert rec2.error_code == ErrorCode.MISSING_MODEL.value  # never downloaded implicitly


def test_failures(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rec, assets = rig.run(job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "FAIL"}))
    assert rec.status is JobStatus.WORKFLOW_ERROR and "kaboom" in (rec.error_message or "") and not assets
    rec2, assets2 = rig.run(job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "NOFILE"}, seed=2))
    assert rec2.error_code == ErrorCode.OUTPUT_MISSING.value and not assets2


def test_progress_from_tqdm_output() -> None:
    a = MfluxAdapter()
    assert a.progress(None, "")[1] == "loading model"  # type: ignore[arg-type]
    frac, msg = a.progress(None, " 2/8 [00:01<00:03]\n 5/8 [00:03<00:01]")  # type: ignore[arg-type]
    assert msg == "step 5/8" and frac == 5 / 8


def test_defaults_point_at_mflux(tmp_path: Path) -> None:
    from storyagent.media.registry import default_registry

    s = make_settings(tmp_path)
    assert "mflux" in default_registry(s)
    gen, edit = s.default_model("image.generate"), s.default_model("image.edit")
    assert (gen.provider, edit.provider) == ("mflux", "mflux") and gen.commercial_ok
    assert gen.extra["command"] == "mflux-generate-flux2" and edit.extra["command"] == "mflux-generate-flux2-edit"


def test_negative_prompt_dropped_when_model_has_none(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rig.settings.models["img-test"].extra["negative_prompt"] = False
    rec, _ = rig.run(job(MediaKind.IMAGE_GENERATE, "img-test", {"prompt": "x", "negative_prompt": "blurry"}, seed=1))
    assert rec.status is JobStatus.COMPLETED
    [argv] = calls(tmp_path)
    assert "--negative-prompt" not in argv
