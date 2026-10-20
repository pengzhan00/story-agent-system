"""Seed-VC voice conversion pipeline. Executed by the Seed-VC virtualenv's Python, NOT by the storyagent environment.

    python seedvc_pipeline.py <config.json>

config.json:
    root        Seed-VC checkout (cwd)          script   inference_v2.py | inference.py
    args        extra CLI arguments for the script (already flattened, excluding --source/--target/--output)
    source      recording to convert            target   reference voice clip
    work_dir    scratch + output folder         env      extra environment for the child processes
    separate    true: split vocals from accompaniment (Demucs) first, convert only the vocals, mix back
    demucs_model / demucs_device / accompaniment_gain / ffmpeg   (separate=true only)
Writes <work_dir>/final.wav. Progress is printed as JSON lines (event = stage | done).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def emit(**kw: Any) -> None:
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def run(argv: list[str], cwd: str, env: dict[str, str]) -> None:
    proc = subprocess.run(argv, cwd=cwd, env={**os.environ, **env}, stdin=subprocess.DEVNULL, check=False)  # noqa: S603
    if proc.returncode != 0:
        raise SystemExit(f"{Path(argv[1]).name if len(argv) > 1 else argv[0]} failed with exit code {proc.returncode}")


def main(cfg: dict[str, Any]) -> int:
    work = Path(cfg["work_dir"])
    env = dict(cfg.get("env", {}))
    source, vocals, accompaniment = cfg["source"], cfg["source"], ""
    if cfg.get("separate"):
        emit(event="stage", name="separate")
        sep = work / "sep"
        run([sys.executable, "-m", "demucs.separate", "--two-stems=vocals", "-n", cfg.get("demucs_model", "htdemucs"),
             "-d", cfg.get("demucs_device", "mps"), "-o", str(sep), source], cfg["root"], env)
        stem = Path(source).stem
        found = sorted(sep.rglob(f"{stem}/vocals.wav"))
        rest = sorted(sep.rglob(f"{stem}/no_vocals.wav"))
        if not found or not rest:
            raise SystemExit("Demucs wrote no vocals/no_vocals stems")
        vocals, accompaniment = str(found[0]), str(rest[0])
    emit(event="stage", name="convert")
    out = work / "converted"
    out.mkdir(parents=True, exist_ok=True)
    run([sys.executable, cfg["script"], "--source", vocals, "--target", cfg["target"], "--output", str(out),
         *cfg.get("args", [])], cfg["root"], env)
    converted = sorted(out.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    if not converted:
        raise SystemExit("Seed-VC wrote no audio")
    final = work / "final.wav"
    if accompaniment:
        emit(event="stage", name="mix")
        gain = float(cfg.get("accompaniment_gain", 1.0))
        run([cfg["ffmpeg"], "-y", "-loglevel", "error", "-i", str(converted[-1]), "-i", accompaniment,
             "-filter_complex", f"[1:a]volume={gain}[b];[0:a][b]amix=inputs=2:duration=longest:normalize=0",
             str(final)], cfg["root"], env)
    else:
        converted[-1].replace(final)
    emit(event="done")
    return 0


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as fh:
        sys.exit(main(json.load(fh)))
