"""Opt-in real-model smoke runs in an isolated data home; never modifies the production DB.

Run with an absolute --home. Uses installed endpoints and weights, preserves memory gates,
executes cases serially, and writes result.json after every case. No downloads or service stops.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from storyagent.config import load_settings
from storyagent.control.graph import GraphStore
from storyagent.db import Database, migrate
from storyagent.media.broker import memory_snapshot
from storyagent.media.ledger import Ledger, NewAsset
from storyagent.media.probe import probe
from storyagent.media.registry import default_registry
from storyagent.media.worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--cases", default="image,speech,sfx,t2v,i2v,a2v,dtvideo,music,cover,ace")
    parser.add_argument("--demucs-repo", type=Path, help="Existing local Demucs repo; no download")
    args = parser.parse_args()
    home = args.home.resolve()
    original = load_settings()
    if home == original.home.resolve() or home == Path.cwd():
        raise ValueError("Use a separate test home")
    home.mkdir(parents=True, exist_ok=True)
    if not (home / "config").exists():
        shutil.copytree(original.config_dir, home / "config")
    settings = load_settings({"STORY_AGENT_HOME": str(home)})
    db = Database(settings.db_path)
    migrate(db)
    ledger = Ledger(db, settings)
    graph = GraphStore(settings, db, ledger)
    result_file = home / "result.json"
    results = json.loads(result_file.read_text()) if result_file.exists() else {"cases": {}, "assets": {}}
    results["memory_at_start"] = memory_snapshot()

    def save() -> None:
        temporary = result_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        temporary.replace(result_file)

    def import_file(path: Path, kind: str) -> str:
        info = probe(settings, path)
        assert info is not None
        asset = ledger.register_asset(
            NewAsset(
                kind=f"upload.{kind}",
                slot=path.stem,
                path=str(path.relative_to(home)),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bytes=path.stat().st_size,
                duration_sec=info.duration_sec,
                width=info.width,
                height=info.height,
                fps=info.fps,
                has_audio=info.has_audio,
            ),
            project_id=None,
        )
        return asset.id

    if "reference" not in results["assets"]:
        reference = home / "synthetic-reference.wav"
        subprocess.run(
            [
                "/usr/bin/say",
                "-v",
                "Tingting",
                "-r",
                "165",
                "-o",
                str(reference),
                "--data-format=LEI16@22050",
                "这是一段系统合成的测试语音。今天阳光明媚，我们一起测试声音和画面的同步效果。",
            ],
            check=True,
            capture_output=True,
        )
        results["assets"]["reference"] = import_file(reference, "audio")
        save()

    def ref(name: str) -> str:
        return "asset:" + results["assets"][name]

    def node(kind: str, model: str, inputs: dict, params: dict | None = None) -> dict:
        return {
            "id": "sample",
            "kind": kind,
            "model_ref": model,
            "inputs": inputs,
            "params": params or {},
            "seed": 72819,
            "tier": "draft",
        }

    cases = {
        "image": lambda: node(
            "image.generate",
            "flux2-klein-4b-drawthings",
            {
                "prompt": "A photorealistic head and shoulders portrait of an adult woman in a plain blue shirt, "
                "facing the camera, closed mouth, neutral expression, soft studio lighting, "
                "simple gray background, no text."
            },
            {"width": 512, "height": 768, "steps": 4},
        ),
        "speech": lambda: node(
            "audio.tts",
            "cosyvoice3-0.5b",
            {
                "text": "你好，欢迎来到故事工坊。让声音带动画面，让创意成为故事。",
                "voice": ref("reference"),
                "voice_text": "这是一段系统合成的测试语音。今天阳光明媚，我们一起测试声音和画面的同步效果。",
                "consent": True,
            },
        ),
        "sfx": lambda: node(
            "audio.sfx",
            "stable-audio-3-medium",
            {"prompt": "A single small wooden door knocking twice, dry close recording, no music."},
            {"duration": 2},
        ),
        "t2v": lambda: node(
            "video.generate",
            "ltx-2.5-video",
            {"prompt": "A red ceramic teapot on a wooden table, a very slow camera push in, warm studio lighting."},
            {"mode": "t2v", "width": 384, "height": 640, "frames": 25, "upscale": "off", "quality": "balanced"},
        ),
        "i2v": lambda: node(
            "video.generate",
            "ltx-2.5-video",
            {"prompt": "A woman faces the camera and blinks gently, her head stays still.", "image": ref("image")},
            {"mode": "i2v", "width": 384, "height": 640, "frames": 25, "upscale": "off", "quality": "balanced"},
        ),
        # Draft size on purpose (fast): proves the run works, says nothing about lipsync quality (see
        # docs/reviews/2026-10-30-a2v-ab.md: the same line scored 1 at 384x640 and 3 at the model's own defaults).
        "a2v": lambda: node(
            "video.lipsync",
            "ltx-2.5-lipsync",
            {
                "prompt": "The woman speaks naturally to the camera with small lip movements and a stable head.",
                "image": ref("image"),
                "audio": ref("speech"),
            },
            {
                "duration": 2,
                "audio_start_time": 0,
                "audio_stem_auto": False,
                "width": 384,
                "height": 640,
                "quality": "balanced",
            },
        ),
        "dtvideo": lambda: node(
            "video.generate",
            "ltx-2.3-drawthings",
            {"prompt": "A woman looks into the camera and blinks slowly, stable camera.", "image": ref("image")},
            {"width": 320, "height": 576, "frames": 25, "fps": 24, "steps": 8},
        ),
        "music": lambda: node(
            "audio.music",
            "yue2-3b",
            {
                "prompt": "Gentle acoustic pop, warm female singer, piano and light guitar, clear Mandarin vocals.",
                "lyrics": "[Verse]\n清风吹过窗边\n故事在眼前",
            },
            {"duration": 15, "music_quality": "draft"},
        ),
        "cover": lambda: node(
            "audio.music",
            "yue2-3b",
            {
                "prompt": "Soft piano pop cover with warm female vocals.",
                "source": ref("music" if "music" in results["assets"] else "ace"),
                "lyrics": "[Verse]\n清风吹过窗边\n故事在眼前",
            },
            {"duration": 15, "music_quality": "draft", "music_task": "cover", "music_cover_task": "full"},
        ),
        "ace": lambda: node(
            "audio.music",
            "acestep-1.5-turbo",
            {"prompt": "gentle piano, acoustic pop, instrumental"},
            {"duration": 10, "instrumental": True, "steps": 8},
        ),
    }
    cases["acecover"] = lambda: node(
        "audio.music",
        "acestep-1.5-turbo",
        {"prompt": "gentle piano, acoustic pop, instrumental", "source": ref("ace")},
        {"task_type": "cover", "duration": 10, "instrumental": True, "steps": 8},
    )
    cases["acerepaint"] = lambda: node(
        "audio.music",
        "acestep-1.5-turbo",
        {"prompt": "gentle piano, acoustic pop, instrumental", "source": ref("ace")},
        {
            "task_type": "repaint",
            "duration": 10,
            "instrumental": True,
            "steps": 8,
            "repainting_start": 2,
            "repainting_end": 4,
        },
    )

    def sequence_case() -> dict:
        spec = cases["a2v"]()
        spec["params"].update(duration=3.5, max_part_seconds=3)
        return spec

    cases["a2v_sequence"] = sequence_case
    cases["stems"] = lambda: node(
        "audio.separate",
        "demucs-htdemucs",
        {"audio": ref("music")},
        {"model_dir": str(args.demucs_repo or Path.home() / "models/demucs"), "device": "cpu"},
    )
    cases["stems_configured"] = lambda: node(
        "audio.separate",
        "demucs-htdemucs",
        {"audio": ref("music")},
        {"device": "cpu"},
    )
    logging.basicConfig(level=logging.INFO)
    worker = Worker(settings, db, default_registry(settings), ledger=ledger, probes=[], max_threads=1)
    try:
        for case in args.cases.split(","):
            if results["cases"].get(case, {}).get("status") == "succeeded":
                continue
            if case in results["cases"]:
                results.setdefault("history", []).append({"case": case, **results["cases"][case]})
            started = time.monotonic()
            try:
                definition = {"name": f"Live smoke: {case}", "nodes": [cases[case]()]}
                run = graph.submit(definition, "codex-live-smoke")
                results["cases"][case] = {"run_id": run["id"], "status": "running", "definition": definition}
                save()
                queued_since = None
                while True:
                    worker.tick()
                    run = graph.get(run["id"])
                    jobs = [ledger.get_job(n["job_id"]) for n in run["nodes"] if n["job_id"]]
                    results["cases"][case].update(
                        status=run["status"],
                        elapsed_sec=round(time.monotonic() - started, 2),
                        jobs=[
                            {
                                "id": j.id,
                                "status": j.status.value,
                                "progress": j.progress,
                                "message": j.progress_msg,
                                "error_code": j.error_code,
                                "error": j.error_message,
                            }
                            for j in jobs
                        ],
                    )
                    save()
                    if run["status"] in {"succeeded", "failed", "cancelled"}:
                        break
                    elapsed = time.monotonic() - started
                    active_jobs = [j for j in jobs if not j.status.is_terminal]
                    queued_only = bool(active_jobs) and all(j.status.value == "queued" for j in active_jobs)
                    if queued_only:
                        queued_since = queued_since or time.monotonic()
                    else:
                        queued_since = None
                    if (queued_since is not None and time.monotonic() - queued_since > 30) or elapsed > 1800:
                        graph.action(run["id"], "cancel", "codex-live-smoke")
                        worker.tick()
                        results["cases"][case]["status"] = "blocked" if queued_only else "timeout"
                        results["cases"][case]["memory"] = memory_snapshot()
                        save()
                        # Do not overlap a still-running/cancelling external task with the next case.
                        while ledger.running():
                            worker.tick()
                            time.sleep(1)
                        break
                    time.sleep(1)
                final = graph.get(run["id"])
                if results["cases"][case]["status"] == "succeeded":
                    aid = final["nodes"][0]["outputs"]["main"]
                    results["assets"][case] = aid
                    asset = ledger.get_asset(aid)
                    info = probe(settings, home / asset.path)
                    results["cases"][case]["artifact"] = {
                        "id": aid,
                        "path": asset.path,
                        "probe": asdict(info) if info else None,
                    }
                results["cases"][case]["nodes"] = final["nodes"]
            except Exception as exc:
                logging.exception("Smoke case %s failed", case)
                results["cases"][case] = {"status": "error", "error": str(exc), "type": type(exc).__name__}
            save()
            print(case, results["cases"][case]["status"], flush=True)
    finally:
        worker.shutdown()
        save()


if __name__ == "__main__":
    main()
