from __future__ import annotations

from pathlib import Path

import pytest

from storyagent.config import Settings
from storyagent.db import Database
from storyagent.domain import JobStatus, MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.adapters.fake import FakeAdapter
from storyagent.media.assetrefs import RefError, Resolver
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger
from storyagent.media.library import Library, LibraryError, import_file, upload_kind
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker


@pytest.fixture
def parts(fsettings: Settings, fdb: Database) -> tuple[Ledger, Library, Resolver]:
    led = Ledger(fdb, fsettings)
    lib = Library(fdb, fsettings, led)
    return led, lib, Resolver(fsettings, led, lib)


def src_file(tmp_path: Path, name: str = "voice.wav", data: bytes = b"RIFFfake") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_import_file_registers_an_upload_asset(fsettings: Settings, parts, tmp_path: Path) -> None:  # noqa: ANN001
    led, _, _ = parts
    a = import_file(fsettings, led, src_file(tmp_path), project_id=1, slot="ref")
    assert a.kind == "upload.audio" and a.project_id == 1 and a.is_current
    assert (fsettings.home / a.path).read_bytes() == b"RIFFfake"
    assert a.metadata["original_name"] == "voice.wav" and a.provenance["origin"] == "upload"
    assert upload_kind(Path("x.safetensors")) == "upload.model" and upload_kind(Path("x.xyz")) == "upload.file"
    with pytest.raises(LibraryError):
        import_file(fsettings, led, tmp_path / "missing.wav", project_id=1)
    with pytest.raises(LibraryError):
        import_file(fsettings, led, src_file(tmp_path), project_id=99)


def test_publish_copies_the_file_so_the_library_outlives_the_project(
    fsettings: Settings, fdb: Database, parts, tmp_path: Path  # noqa: ANN001
) -> None:
    led, lib, _ = parts
    a = import_file(fsettings, led, src_file(tmp_path), project_id=1)
    item = lib.publish(a.id, "voice", "Lin Wan", description="heroine", tags=["female", "zh"],
                       meta={"text": "hello there", "consent": True})
    assert item.ref == "library:voice/Lin Wan" and item.asset is not None
    assert item.asset.id != a.id and item.asset.project_id is None and item.asset.path.startswith("library/voice/")
    assert item.meta["consent"] is True and item.source_asset_id == a.id and item.source_project_id == 1
    assert (fsettings.home / item.asset.path).read_bytes() == b"RIFFfake"
    # the source is unrelated afterwards
    led.trash_asset(a.id)
    (fsettings.home / a.path).unlink()
    assert (fsettings.home / item.asset.path).is_file()
    assert [i.name for i in lib.list(role="voice")] == ["Lin Wan"]
    assert [i.name for i in lib.list(tag="female")] == ["Lin Wan"] and lib.list(tag="male") == []
    assert [i.name for i in lib.list(q="heroine")] == ["Lin Wan"]


def test_names_are_unique_per_role_until_replaced_or_removed(fsettings: Settings, parts, tmp_path: Path) -> None:  # noqa: ANN001
    led, lib, _ = parts
    a = import_file(fsettings, led, src_file(tmp_path), project_id=1)
    first = lib.publish(a.id, "voice", "X")
    with pytest.raises(LibraryError, match="already exists"):
        lib.publish(a.id, "voice", "X")
    second = lib.publish(a.id, "voice", "X", replace=True)
    assert second.id != first.id and lib.find("voice", "X").id == second.id  # type: ignore[union-attr]
    lib.remove(second.id)
    assert lib.find("voice", "X") is None
    lib.publish(a.id, "portrait", "X")  # same name, other role is fine
    with pytest.raises(LibraryError, match="role must be"):
        lib.publish(a.id, "nonsense", "Y")
    with pytest.raises(LibraryError, match="slash"):
        lib.publish(a.id, "voice", "a/b")


def test_add_file_goes_straight_into_the_library(fsettings: Settings, parts, tmp_path: Path) -> None:  # noqa: ANN001
    _, lib, _ = parts
    item = lib.add_file(src_file(tmp_path, "lora.safetensors", b"weights"), "lora", "heroine-v1",
                        meta={"trigger": "lwx woman", "base_model": "flux2-klein-base-4b"})
    assert item.asset is not None and item.asset.kind == "upload.model"
    assert (fsettings.home / item.asset.path).read_bytes() == b"weights"
    assert item.meta["trigger"] == "lwx woman"
    upd = lib.update(item.id, tags=["character"], meta={"scale": 0.8})
    assert upd.tags == ["character"] and upd.meta["scale"] == 0.8 and upd.meta["trigger"] == "lwx woman"


def test_resolver_handles_strings_dicts_lists_and_voice_objects(fsettings: Settings, parts, tmp_path: Path) -> None:  # noqa: ANN001
    led, lib, res = parts
    a = import_file(fsettings, led, src_file(tmp_path), project_id=1)
    lib.publish(a.id, "voice", "Lin", meta={"text": "ref words", "consent": True})
    lib.publish(a.id, "lora", "L1")
    inputs = {
        "prompt": "asset: not a ref because of the space after",   # only a prefix at position 0 counts
        "refs": [f"asset:{a.id}", {"asset": a.id}, "plain/path.png"],
        "voice_ref": "library:voice/Lin",
    }
    params = {"loras": [{"path": {"library": "lora/L1"}, "scale": 0.7}]}
    out_in, out_par, r = res.resolve(inputs, params)
    path = str(fsettings.home / a.path)
    assert out_in["refs"] == [path, path, "plain/path.png"]
    assert out_in["prompt"] == inputs["prompt"]
    assert out_in["voice_ref"]["text"] == "ref words" and out_in["voice_ref"]["consent"] is True
    assert out_in["voice_ref"]["audio"].startswith(str(fsettings.home / "library" / "voice"))
    assert out_par["loras"][0]["path"].startswith(str(fsettings.home / "library" / "lora"))
    assert out_par["loras"][0]["scale"] == 0.7
    assert len(r.sha256) == 3 and bool(r)
    assert inputs["refs"][0] == f"asset:{a.id}"  # the stored snapshot is untouched


def test_voice_ref_audio_can_be_a_plain_asset(fsettings: Settings, parts, tmp_path: Path) -> None:  # noqa: ANN001
    led, _, res = parts
    a = import_file(fsettings, led, src_file(tmp_path), project_id=1)
    out, _, _ = res.resolve({"voice_ref": {"audio": f"asset:{a.id}", "text": "t", "consent": True}}, {})
    assert out["voice_ref"] == {"audio": str(fsettings.home / a.path), "text": "t", "consent": True}


def test_unresolvable_references_are_explicit_errors(fsettings: Settings, parts, tmp_path: Path) -> None:  # noqa: ANN001
    led, lib, res = parts
    a = import_file(fsettings, led, src_file(tmp_path), project_id=1)
    with pytest.raises(RefError, match="unknown asset"):
        res.resolve({"refs": ["asset:" + "0" * 32]}, {})
    assert res.resolve({"refs": ["asset:nope"]}, {})[0]["refs"] == ["asset:nope"]  # malformed = plain text
    with pytest.raises(RefError, match="no such library item"):
        res.resolve({"refs": ["library:voice/ghost"]}, {})
    led.trash_asset(a.id)
    with pytest.raises(RefError, match="trash"):
        res.resolve({"refs": [f"asset:{a.id}"]}, {})
    b = import_file(fsettings, led, src_file(tmp_path), project_id=1, slot="other")
    (fsettings.home / b.path).unlink()
    with pytest.raises(RefError, match="missing"):
        res.resolve({"refs": [f"asset:{b.id}"]}, {})


def make_worker(fsettings: Settings, fdb: Database, led: Ledger) -> tuple[Worker, FakeAdapter]:
    reg, adapter = AdapterRegistry(), FakeAdapter()
    reg.register(adapter)
    return Worker(fsettings, fdb, reg, ledger=led, probes=[]), adapter


def test_worker_hands_adapters_plain_paths_and_logs_the_resolution(
    fsettings: Settings, fdb: Database, parts, tmp_path: Path  # noqa: ANN001
) -> None:
    led, lib, _ = parts
    a = import_file(fsettings, led, src_file(tmp_path), project_id=1)
    lib.publish(a.id, "voice", "Lin", meta={"text": "t", "consent": True})
    rec, _ = led.enqueue(MediaJob(MediaKind.AUDIO_TTS, {"text": "hi", "voice_ref": "library:voice/Lin",
                                                         "refs": [f"asset:{a.id}"]},
                                  project_id=1, model_ref="fake-tts"))
    w, adapter = make_worker(fsettings, fdb, led)
    from storyagent.media.worker import run_once

    assert run_once(w, 20)
    done = led.get_job(rec.id)
    assert done.status is JobStatus.COMPLETED, (done.error_code, done.error_message)
    seen_inputs, _ = adapter.seen[rec.id]
    assert seen_inputs["voice_ref"]["audio"].startswith(str(fsettings.home))
    assert seen_inputs["refs"] == [str(fsettings.home / a.path)]
    assert done.inputs["refs"] == [f"asset:{a.id}"]  # the ledger keeps the reference, not the path
    ev = [e for e in led.events(rec.id) if e["event"] == "refs_resolved"]
    assert ev and a.id in ev[0]["data"]["sha256"]


def test_worker_fails_a_job_with_a_missing_reference_as_invalid_input(
    fsettings: Settings, fdb: Database, parts  # noqa: ANN001
) -> None:
    led, _, _ = parts
    rec, _ = led.enqueue(MediaJob(MediaKind.AUDIO_TTS, {"text": "hi", "refs": ["library:voice/ghost"]},
                                  project_id=1, model_ref="fake-tts"))
    w, _ = make_worker(fsettings, fdb, led)
    from storyagent.media.worker import run_once

    run_once(w, 20)
    done = led.get_job(rec.id)
    assert done.status is JobStatus.FAILED_VALIDATION and done.error_code == ErrorCode.INVALID_INPUT.value
