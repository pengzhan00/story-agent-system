from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.lora_train import LoraTrainAdapter, deep_merge
from storyagent.media.library import Library, import_file

from .adapter_kit import Rig, job, make_rig, make_settings
from .test_adapter_mflux import png_bytes

STUB = textwrap.dedent(f'''\
    #!{sys.executable}
    import json, os, shutil, sys
    a = sys.argv[1:]
    cfg = json.load(open(a[a.index("--config") + 1], encoding="utf-8"))
    here = os.path.dirname(os.path.abspath(__file__))
    json.dump({{"cfg": cfg, "data": sorted(os.listdir(cfg["data"])),
               "caption": open(os.path.join(cfg["data"], "01.txt"), encoding="utf-8").read()}},
              open(os.path.join(here, "seen.json"), "w", encoding="utf-8"))
    for i in range(1, 4):
        print(f"Training  {{i}}/3 [00:0{{i}}<00:00, 1.00it/s]", flush=True)
    if os.path.exists(os.path.join(here, "FAIL")):
        sys.exit(3)
    if not os.path.exists(os.path.join(here, "NOADAPTER")):
        out = os.path.join(cfg["checkpoint"]["output_path"] + "_20260101_000000", "checkpoints")
        os.makedirs(out, exist_ok=True)
        import zipfile, time
        for n in (5, 10):
            with zipfile.ZipFile(os.path.join(out, f"{{n:07d}}_checkpoint.zip"), "w") as zf:
                zf.writestr(f"{{n:07d}}_adapter.safetensors", b"lora-%d" % n)
                zf.writestr(f"{{n:07d}}_loss.json", json.dumps({{"loss": [1.0, 0.5, 0.25]}}))
            time.sleep(0.02)
        if os.path.exists(os.path.join(here, "LOOSE")):
            open(os.path.join(out, "0000099_adapter.safetensors"), "wb").write(b"loose")
''')

MODELS = """
[models."trainer-test"]
kind = "train.lora"
provider = "lora_train"
heavy = true
license_tag = "apache-test"
commercial_ok = true
timeout_sec = 60
[models."trainer-test".train_template]
model = "flux2-klein-base-4b"
seed = 1
max_resolution = 512
[models."trainer-test".train_template.training_loop]
num_epochs = 60
[models."trainer-test".train_template.optimizer]
learning_rate = 0.0001
[models."trainer-test".train_template.checkpoint]
save_frequency = 5
[[models."trainer-test".train_template.lora_layers.targets]]
module_path = "transformer_blocks.{block}.attn.to_q"
blocks = { start = 0, end = 5 }
rank = 16
[[models."trainer-test".train_template.lora_layers.targets]]
module_path = "single_transformer_blocks.{block}.attn.to_out"
blocks = { start = 0, end = 20 }
rank = 16
"""


def build(tmp_path: Path, *, binary: bool = True, base: bool = True) -> Rig:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    if binary:
        stub = bindir / "stub-train"
        stub.write_text(STUB)
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    base_dir = tmp_path / "base"
    if base:
        base_dir.mkdir(exist_ok=True)
    s = make_settings(tmp_path, services=f'[tools.lora_train]\ncommand = "{bindir / "stub-train"}"\n'
                                         f'base_model_dir = "{base_dir}"\n', models=MODELS)
    return make_rig(s, LoraTrainAdapter(poll_interval_sec=0.02))


def photos(tmp_path: Path, n: int = 5) -> list[str]:
    d = tmp_path / "photos"
    d.mkdir(exist_ok=True)
    out = []
    for i in range(n):
        p = d / f"p{i}.png"
        p.write_bytes(png_bytes(64, 96))
        out.append(str(p))
    return out


def inputs(tmp_path: Path, **over: object) -> dict[str, object]:
    return {"images": photos(tmp_path), "trigger": "lwx woman", "consent": True, **over}


def seen(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "bin" / "seen.json").read_text("utf-8"))


def test_trains_and_registers_the_newest_adapter(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rec, assets = rig.run(job(MediaKind.TRAIN_LORA, "trainer-test", inputs(tmp_path, description="young woman"),
                              params={"epochs": 3, "rank": 8, "blocks": 2, "single_blocks": 4,
                                      "learning_rate": 0.0002}))
    assert rec.status is JobStatus.COMPLETED, (rec.error_code, rec.error_message)
    [a] = assets
    assert a.kind == "train.lora" and a.path.endswith(".safetensors")
    assert (rig.settings.home / a.path).read_bytes() == b"lora-10"        # the newest checkpoint
    assert a.metadata["trigger"] == "lwx woman" and a.metadata["rank"] == 8 and a.metadata["epochs"] == 3
    assert a.metadata["images"] == 5 and a.metadata["base_license"] == "apache-test"
    assert a.metadata["final_loss"] == round((1.0 + 0.5 + 0.25) / 3, 5) and a.metadata["loss_points"] == 3
    s = seen(tmp_path)
    assert s["cfg"]["training_loop"]["num_epochs"] == 3 and s["cfg"]["optimizer"]["learning_rate"] == 0.0002
    assert s["cfg"]["model_path"] == str(tmp_path / "base") and s["cfg"]["model"] == "flux2-klein-base-4b"
    tq, out = s["cfg"]["lora_layers"]["targets"]
    assert tq["rank"] == out["rank"] == 8 and tq["blocks"]["end"] == 2 and out["blocks"]["end"] == 4
    assert s["data"] == ["01.png", "01.txt", "02.png", "02.txt", "03.png", "03.txt", "04.png", "04.txt", "05.png",
                         "05.txt", "preview_1.txt", "preview_2.txt"]
    assert s["caption"].strip() == "lwx woman young woman"
    assert a.provenance["model"]["license_tag"] == "apache-test"


def test_custom_captions_previews_and_raw_overrides(tmp_path: Path) -> None:
    rig = build(tmp_path)
    caps = [f"caption {i}" for i in range(5)]
    rec, _ = rig.run(job(MediaKind.TRAIN_LORA, "trainer-test",
                         inputs(tmp_path, captions=caps, previews=["only one"]),
                         params={"train": {"optimizer": {"name": "Lion"}, "gradient_checkpointing": False}}))
    assert rec.status is JobStatus.COMPLETED, rec.error_message
    s = seen(tmp_path)
    assert s["caption"].strip() == "caption 0" and s["data"].count("preview_1.txt") == 1
    assert "preview_2.txt" not in s["data"]
    assert s["cfg"]["optimizer"] == {"learning_rate": 0.0001, "name": "Lion"}   # deep merge keeps siblings
    assert s["cfg"]["gradient_checkpointing"] is False


def test_validation(tmp_path: Path) -> None:
    rig = build(tmp_path)

    def run(**over: object) -> tuple[JobStatus, str]:
        rec, _ = rig.run(job(MediaKind.TRAIN_LORA, "trainer-test", inputs(tmp_path, **over)))
        return rec.status, rec.error_code or ""

    assert run(trigger="x") == (JobStatus.FAILED_VALIDATION, "invalid_input")
    assert run(images=photos(tmp_path, 2)) == (JobStatus.FAILED_VALIDATION, "invalid_input")        # < min_images
    assert run(images=[*photos(tmp_path), str(tmp_path / "nope.png")])[1] == "invalid_input"
    assert run(captions=["a", "b"])[1] == "invalid_input"
    assert run(consent=False) == (JobStatus.FAILED_VALIDATION, ErrorCode.CONSENT_REQUIRED.value)


def test_consent_gate_can_be_switched_off_per_project(tmp_path: Path) -> None:
    rig = build(tmp_path)
    rig.db.conn.execute("UPDATE projects SET settings='{\"compliance\": {\"require_likeness_consent\": false}}'")
    rec, _ = rig.run(job(MediaKind.TRAIN_LORA, "trainer-test", inputs(tmp_path, consent=False)))
    assert rec.status is JobStatus.COMPLETED, rec.error_message


def test_missing_tool_base_model_and_failures(tmp_path: Path) -> None:
    rec, _ = build(tmp_path / "a", binary=False).run(job(MediaKind.TRAIN_LORA, "trainer-test", inputs(tmp_path)))
    assert rec.error_code == "missing_tool"
    rec, _ = build(tmp_path / "b", base=False).run(job(MediaKind.TRAIN_LORA, "trainer-test", inputs(tmp_path)))
    assert rec.error_code == "missing_model"
    rig = build(tmp_path / "c")
    (tmp_path / "c" / "bin" / "FAIL").write_text("")
    rec, _ = rig.run(job(MediaKind.TRAIN_LORA, "trainer-test", inputs(tmp_path)))
    assert rec.status is JobStatus.WORKFLOW_ERROR and rec.error_code == "tool_failed"
    (tmp_path / "c" / "bin" / "FAIL").unlink()
    (tmp_path / "c" / "bin" / "NOADAPTER").write_text("")
    rec, _ = rig.run(job(MediaKind.TRAIN_LORA, "trainer-test", inputs(tmp_path, trigger="other word")))
    assert rec.error_code == "output_missing"


def test_images_by_asset_reference_and_the_lora_goes_to_the_library(tmp_path: Path) -> None:
    rig = build(tmp_path)
    refs = []
    for i, p in enumerate(photos(tmp_path)):
        refs.append(f"asset:{import_file(rig.settings, rig.ledger, Path(p), project_id=1, slot=f'ph{i}').id}")
    rec, [*_, lora] = rig.run(job(MediaKind.TRAIN_LORA, "trainer-test", inputs(tmp_path, images=refs)))
    assert rec.status is JobStatus.COMPLETED, rec.error_message
    lora = next(a for a in rig.ledger.list_assets(project_id=1) if a.kind == "train.lora")
    item = Library(rig.db, rig.settings, rig.ledger).publish(
        lora.id, "lora", "lin-wan", meta={"trigger": lora.metadata["trigger"], "base_model": "flux2-klein-base-4b"})
    assert item.ref == "library:lora/lin-wan" and item.meta["trigger"] == "lwx woman"


def test_a_loose_adapter_file_is_also_found(tmp_path: Path) -> None:
    rig = build(tmp_path)
    (tmp_path / "bin" / "LOOSE").write_text("")
    rec, [a] = rig.run(job(MediaKind.TRAIN_LORA, "trainer-test", inputs(tmp_path)))
    assert rec.status is JobStatus.COMPLETED, rec.error_message
    assert (rig.settings.home / a.path).read_bytes() == b"loose"


def test_deep_merge_does_not_mutate_its_inputs() -> None:
    base = {"a": {"x": 1, "y": 2}, "l": [1]}
    out = deep_merge(base, {"a": {"y": 3}, "l": [2]})
    assert out == {"a": {"x": 1, "y": 3}, "l": [2]} and base == {"a": {"x": 1, "y": 2}, "l": [1]}
