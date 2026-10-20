"""Render one episode from a timeline (see ``storyagent/compose/timeline.py``). Standard library only.

    python assemble_episode.py <config.json>

config: {"ffmpeg": path, "ffprobe": path, "timeline": {... asset refs already resolved to file paths ...},
         "out_dir": dir, "burn_subtitles": bool}
Writes ``episode.mp4`` and ``episode.srt`` into out_dir and one JSON progress line per step on stdout:
    {"event": "step", "done": 3, "total": 9, "msg": "shot 3/6"}
Steps: one per segment, then "video", "audio", "final".
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

INTERMEDIATE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p"]


def emit(done: int, total: int, msg: str) -> None:
    print(json.dumps({"event": "step", "done": done, "total": total, "msg": msg}, ensure_ascii=False), flush=True)


def run(argv: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603 - argv, no shell
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        print(f"ffmpeg failed ({proc.returncode}): {' '.join(argv[:6])} ...\n{tail}", file=sys.stderr, flush=True)
        raise SystemExit(2)


def srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[dict[str, Any]], dest: Path) -> None:
    lines: list[str] = []
    for i, c in enumerate(cues, 1):
        lines += [str(i), f"{srt_time(c['start'])} --> {srt_time(c['end'])}", str(c["text"]).strip(), ""]
    dest.write_text("\n".join(lines), encoding="utf-8")


def segment_cmd(ffmpeg: str, seg: dict[str, Any], vid: dict[str, Any], dest: Path) -> list[str]:
    w, h, fps, dur = vid["width"], vid["height"], vid["fps"], float(seg["duration"])
    src = seg["source"]
    fill = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    base = [ffmpeg, "-y", "-loglevel", "error", "-nostdin"]
    if src["type"] == "video":
        chain = [f"setpts={src['speed']}*PTS"] if src["speed"] != 1.0 else []
        chain += [fill, f"fps={fps}"]
        if src.get("hold_after", 0) > 0:
            chain.append(f"tpad=stop_mode=clone:stop_duration={src['hold_after']}")
        return [*base, "-i", src["path"], "-vf", ",".join(chain), "-t", f"{dur}", "-an", *INTERMEDIATE, str(dest)]
    if src["type"] == "image":
        frames = max(int(dur * fps), 1)
        zoom = (f"{fill},zoompan=z='min(zoom+0.0004,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={frames}:s={w}x{h}:fps={fps}")
        return [*base, "-loop", "1", "-i", src["path"], "-vf", zoom, "-t", f"{dur}", "-an", *INTERMEDIATE, str(dest)]
    black = f"color=c=black:s={w}x{h}:r={fps}"
    return [*base, "-f", "lavfi", "-i", black, "-t", f"{dur}", "-an", *INTERMEDIATE, str(dest)]


def audio_cmd(ffmpeg: str, tl: dict[str, Any], dest: Path) -> list[str]:
    a, total = tl["audio"], float(tl["total_sec"])
    rate = a["rate"]
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-t", f"{total}",
           "-i", f"anullsrc=r={rate}:cl=stereo"]
    chains: list[str] = []
    n = 1

    def delayed(item: dict[str, Any], gain: float, label: str) -> str:
        nonlocal n
        cmd.extend(["-i", item["path"]])
        ms = int(round(float(item["start"]) * 1000))
        chains.append(f"[{n}:a]aresample={rate},aformat=channel_layouts=stereo,adelay={ms}|{ms},"
                      f"volume={gain}[{label}]")
        n += 1
        return f"[{label}]"

    voice = ["[0:a]"] + [delayed(s, a["voice_gain"], f"v{i}") for i, s in enumerate(tl["speech"])]
    chains.append(f"{''.join(voice)}amix=inputs={len(voice)}:duration=first:normalize=0[voice]")
    sfx = [delayed(s, a["sfx_gain"], f"x{i}") for i, s in enumerate(tl["sfx"])]
    mixed = ["[vmix]"]
    chains.append("[voice]asplit=2[vmix][vkey]")
    if tl["music"]:
        m = tl["music"][0]
        cmd.extend(["-stream_loop", "-1", "-i", m["path"]])
        chains.append(f"[{n}:a]aresample={rate},aformat=channel_layouts=stereo,atrim=0:{total},"
                      f"volume={m['gain']}[bgm]")
        chains.append("[bgm][vkey]sidechaincompress=threshold=0.04:ratio=8:attack=20:release=400[duck]")
        mixed.append("[duck]")
        n += 1
    else:
        chains.append("[vkey]anullsink")
    mixed += sfx
    ln = a["loudnorm"]
    audible = bool(tl["speech"] or tl["sfx"] or tl["music"])     # loudnorm on pure silence yields NaN and breaks aac
    norm = f"loudnorm=I={ln['i']}:TP={ln['tp']}:LRA={ln['lra']}," if audible else ""
    chains.append(f"{''.join(mixed)}amix=inputs={len(mixed)}:duration=first:normalize=0,"
                  f"{norm}aresample={rate},atrim=0:{total}[out]")
    return [*cmd, "-filter_complex", ";".join(chains), "-map", "[out]", "-c:a", a["codec"], "-b:a", a["bitrate"],
            str(dest)]


def has_filter(ffmpeg: str, name: str) -> bool:
    proc = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, check=False)  # noqa: S603
    return any(line.split()[1:2] == [name] for line in proc.stdout.splitlines())


def main(config_path: str) -> int:
    cfg = json.loads(Path(config_path).read_text("utf-8"))
    ffmpeg, tl = cfg["ffmpeg"], cfg["timeline"]
    out = Path(cfg["out_dir"])
    work = out / "parts"
    work.mkdir(parents=True, exist_ok=True)
    vid = tl["video"]
    segs = tl["segments"]
    total = len(segs) + 3

    parts: list[Path] = []
    for i, seg in enumerate(segs):
        emit(i, total, f"shot {i + 1}/{len(segs)}")
        dest = work / f"seg_{i:03d}.mp4"
        run(segment_cmd(ffmpeg, seg, vid, dest))
        parts.append(dest)

    emit(len(segs), total, "video")
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts), "utf-8")
    joined = work / "video_all.mp4"
    run([ffmpeg, "-y", "-loglevel", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i", listing.name, "-c", "copy",
         joined.name], cwd=work)

    emit(len(segs) + 1, total, "audio")
    mixed = work / "audio.m4a"
    run(audio_cmd(ffmpeg, tl, mixed))

    emit(len(segs) + 2, total, "final")
    cues = tl["subtitles"].get("cues", [])
    srt = out / "episode.srt"
    write_srt(cues, srt)
    final = out / "episode.mp4"
    burn = bool(cfg.get("burn_subtitles", True)) and bool(cues) and has_filter(ffmpeg, "subtitles")
    sub = tl["subtitles"]
    style = (f"FontName={sub.get('font', 'Arial')},FontSize={sub.get('font_size', 16)},"
             f"MarginV={sub.get('margin_v', 42)},Outline={sub.get('outline', 1)},Alignment=2")
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-nostdin", "-i", str(joined), "-i", str(mixed)]
    if burn:
        cmd += ["-vf", f"subtitles=filename='{srt.name}':force_style='{style}'",
                "-c:v", vid["codec"], "-crf", str(vid["crf"]), "-pix_fmt", vid["pix_fmt"], "-preset", "medium"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy", "-movflags", "+faststart", "-shortest", str(final)]
    run(cmd, cwd=out)
    emit(total, total, "done")
    (out / "report.json").write_text(json.dumps({"burned_subtitles": burn, "total_sec": tl["total_sec"],
                                                  "warnings": tl.get("warnings", [])}, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
