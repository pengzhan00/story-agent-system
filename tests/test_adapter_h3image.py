from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path
from typing import Any

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.h3image import REQUIRED_WEIGHTS, H3ImageAdapter

from .adapter_kit import Rig, job, make_rig, make_settings
from .test_adapter_mflux import png_bytes

STUB = textwrap.dedent(f'''\
    #!{sys.executable}
    import json, os, sys, struct, zlib
    a = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(a, ensure_ascii=False) + "\\n")
    prompt = a[a.index("--prompt") + 1]
    if prompt == "FAIL":
        print("error: model blew up", flush=True)
        sys.exit(2)
    if prompt != "NOFILE":
        w = int(a[a.index("--width") + 1]) if "--width" in a else 32
        h = int(a[a.index("--height") + 1]) if "--height" in a else 32
        def chunk(tag, data):
            body = tag + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        raw = b"".join(b"\\x00" + b"\\x80\\x40\\x20" * w for _ in range(h))
        png = (b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        open(a[a.index("--output") + 1], "wb").write(png)
        print(a[a.index("--output") + 1])
''')

MODELS = """
[models."h3i-test"]
kind = "image.generate"
provider = "h3image"
license_tag = "community"
commercial_ok = false
timeout_sec = 30
[models."h3i-edit-test"]
kind = "image.edit"
provider = "h3image"
license_tag = "community"
commercial_ok = false
timeout_sec = 30
"""


def build(tmp_path: Path, *, weights: bool = True, binary: bool = True, missing: str = "") -> Rig:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "h3-image-mlx"
    if binary:
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    wdir = tmp_path / "models"
    if weights:
        wdir.mkdir(exist_ok=True)
        for name in REQUIRED_WEIGHTS:
            if name == missing:
                continue
            (wdir / name).mkdir(exist_ok=True) if "." not in name else (wdir / name).write_text("{}")
    s = make_settings(tmp_path, services=f'[tools.h3image]\nbinary = "{stub}"\nmodel_dir = "{wdir}"\n', models=MODELS)
    return make_rig(s, H3ImageAdapter(poll_interval_sec=0.02))


def calls(tmp_path: Path) -> list[list[str]]:
    f = tmp_path / "bin" / "calls.jsonl"
    return [json.loads(x) for x in f.read_text("utf-8").splitlines()] if f.exists() else []


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_generates_an_image_and_leaves_unset_parameters_to_the_tool(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rec, [a] = rig.run(job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "a quiet street at dawn"}, seed=7,
                           params={"width": 256, "height": 288, "dit_mode": "stream"}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    assert a.path.endswith(".png") and (a.width, a.height) == (256, 288) and a.metadata["seed"] == 7
    [argv] = calls(tmp_path)
    assert argv[0] == "generate" and flag(argv, "--models") == str(tmp_path / "models")
    assert flag(argv, "--seed") == "7" and flag(argv, "--dit-mode") == "stream"
    for absent in ("--precision", "--backend", "--reference", "--steps", "--guidance"):
        assert absent not in argv


def test_references_keep_their_order_for_picture_n(tmp_path: Path) -> None:
    rig = build(tmp_path)
    one, two = tmp_path / "one.png", tmp_path / "two.png"
    one.write_bytes(png_bytes())
    two.write_bytes(png_bytes())
    rec, [a] = rig.run(job(MediaKind.IMAGE_EDIT, "h3i-edit-test",
                           {"prompt": "put <Picture 2>'s coat on <Picture 1>", "refs": [str(one), str(two)]}, seed=3,
                           params={"width": 256, "height": 256}))
    assert rec.status is JobStatus.COMPLETED and a.metadata["references"] == 2
    [argv] = calls(tmp_path)
    refs = [argv[i + 1] for i, v in enumerate(argv) if v == "--reference"]
    assert refs == [str(one), str(two)]


def test_validation_names_the_reason(tmp_path: Path) -> None:
    rig = build(tmp_path)
    ref = tmp_path / "ref.png"
    ref.write_bytes(png_bytes())
    many = [str(ref)] * 17
    cases: list[tuple[Any, ErrorCode]] = [
        (job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": " "}), ErrorCode.INVALID_INPUT),
        (job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "x"}, params={"width": 1000, "height": 1024}),
         ErrorCode.INVALID_PARAMS),                                    # 1000 is not a multiple of 32
        (job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "x"}, params={"width": 8192, "height": 1024}),
         ErrorCode.INVALID_PARAMS),
        (job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "x"}, params={"dit_mode": "fast"}),
         ErrorCode.INVALID_PARAMS),
        (job(MediaKind.IMAGE_EDIT, "h3i-edit-test", {"prompt": "x"}), ErrorCode.INVALID_INPUT),
        (job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "x", "refs": [str(tmp_path / "no.png")]}),
         ErrorCode.INVALID_INPUT),
        (job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "x", "refs": many}), ErrorCode.INVALID_INPUT),
        (job(MediaKind.IMAGE_EDIT, "h3i-edit-test", {"prompt": "change <Picture 2>", "refs": [str(ref)]}),
         ErrorCode.INVALID_INPUT),                                     # there is no second picture
    ]
    for n, (j, code) in enumerate(cases):
        rec, _ = rig.run(job(j.kind, j.model_ref, dict(j.inputs), params=dict(j.params), seed=n))
        assert rec.error_code == code.value, (n, rec.error_message)
    assert not calls(tmp_path)


def test_missing_tool_and_incomplete_weights_are_told_apart_from_a_bad_model(tmp_path: Path) -> None:
    rec, _ = build(tmp_path / "a", binary=False).run(job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "x"}))
    assert rec.error_code == ErrorCode.MISSING_TOOL.value
    rec2, _ = build(tmp_path / "b", weights=False).run(job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "x"}))
    assert rec2.error_code == ErrorCode.MISSING_MODEL.value
    rec3, _ = build(tmp_path / "c", missing="vae").run(job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "x"}))
    assert rec3.error_code == ErrorCode.MISSING_MODEL.value and "vae" in (rec3.error_message or "")


def test_a_crash_and_a_silent_exit_fail_with_the_reason(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rec, assets = rig.run(job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "FAIL"}))
    assert rec.status is JobStatus.WORKFLOW_ERROR and "blew up" in (rec.error_message or "") and not assets
    rec2, assets2 = rig.run(job(MediaKind.IMAGE_GENERATE, "h3i-test", {"prompt": "NOFILE"}, seed=2))
    assert rec2.error_code == ErrorCode.OUTPUT_MISSING.value and not assets2


def test_the_shipped_entries_are_heavy_licensed_and_not_defaults(tmp_path: Path) -> None:
    from storyagent.media.registry import default_registry

    s = make_settings(tmp_path)
    assert "h3image" in default_registry(s)
    gen, edit = s.models["h3-image"], s.models["h3-image-edit"]
    assert gen.heavy and edit.heavy and not gen.commercial_ok and gen.license_tag == "minimax-h3-image-community"
    assert {s.default_model(k).provider for k in ("image.generate", "image.edit")}.isdisjoint({"h3image"})


def test_a_project_shape_is_fitted_to_the_32_pixel_grid(tmp_path: Path) -> None:
    from storyagent.media.params import resolve_params

    s = make_settings(tmp_path)
    for ref in ("h3-image", "h3-image-edit"):
        for frame in ((720, 1280), (1280, 720)):
            p = resolve_params(s, ref, "draft", frame=frame)
            assert p["width"] % 32 == 0 and p["height"] % 32 == 0, (ref, frame, p)
            assert (p["width"] < p["height"]) == (frame[0] < frame[1])
