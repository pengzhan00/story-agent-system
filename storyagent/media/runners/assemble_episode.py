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


def fades(seg: dict[str, Any]) -> list[str]:
    """Dip in from / out to black at the segment's ends (never longer than half the segment)."""
    dur = float(seg["duration"])
    out: list[str] = []
    fin = min(float(seg.get("fade_in", 0) or 0), dur / 2)
    fout = min(float(seg.get("fade_out", 0) or 0), dur / 2)
    if fin > 0:
        out.append(f"fade=t=in:st=0:d={fin}")
    if fout > 0:
        out.append(f"fade=t=out:st={round(dur - fout, 3)}:d={fout}")
    return out


def segment_cmd(
    ffmpeg: str, seg: dict[str, Any], vid: dict[str, Any], dest: Path, card_ass: str | None = None,
    overlap: float = 0.0,
) -> list[str]:
    """One segment as an intermediate clip. ``overlap`` extra seconds are rendered after the nominal end: they are the
    part that melts into the next shot in a cross-dissolve (the joined film is as long as the timeline says)."""
    w, h, fps = vid["width"], vid["height"], vid["fps"]
    dur = float(seg["duration"]) + overlap
    src = seg["source"]
    fill = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    base = [ffmpeg, "-y", "-loglevel", "error", "-nostdin"]
    tail = fades(seg)
    if src["type"] == "video":
        chain = [f"setpts={src['speed']}*PTS"] if src["speed"] != 1.0 else []
        chain += [fill, f"fps={fps}"]
        if src.get("hold_after", 0) > 0:
            chain.append(f"tpad=stop_mode=clone:stop_duration={src['hold_after']}")
        if overlap > 0:
            chain.append(f"tpad=stop_mode=clone:stop_duration={overlap}")
        chain += tail
        return [*base, "-i", src["path"], "-vf", ",".join(chain), "-t", f"{dur}", "-an", *INTERMEDIATE, str(dest)]
    if src["type"] == "image":
        frames = max(int(dur * fps), 1)
        zoom = (f"{fill},zoompan=z='min(zoom+0.0004,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={frames}:s={w}x{h}:fps={fps}")
        zoomed = ",".join([zoom, *tail])
        return [*base, "-loop", "1", "-i", src["path"], "-vf", zoomed, "-t", f"{dur}", "-an", *INTERMEDIATE, str(dest)]
    black = f"color=c=black:s={w}x{h}:r={fps}"
    chain = [f"ass=filename='{card_ass}'"] if card_ass else []
    chain += tail
    vf = ["-vf", ",".join(chain)] if chain else []
    return [*base, "-f", "lavfi", "-i", black, *vf, "-t", f"{dur}", "-an", *INTERMEDIATE, str(dest)]


def join_cmd(ffmpeg: str, files: list[str], lengths: list[float], overlaps: list[float], dest: str) -> list[str]:
    """One clip from ``files`` melted together: ``overlaps[k]`` seconds of file k run into file k+1 (xfade)."""
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-nostdin"]
    for f in files:
        cmd += ["-i", f]
    chain, cur, length = [], "[0:v]", lengths[0]
    for k in range(1, len(files)):
        d = overlaps[k - 1]
        chain.append(f"{cur}[{k}:v]xfade=transition=fade:duration={d}:offset={round(length - d, 3)}[x{k}]")
        cur, length = f"[x{k}]", length + lengths[k] - d
    return [*cmd, "-filter_complex", ";".join(chain), "-map", cur, "-an", *INTERMEDIATE, dest]


def ass_colour(rgb: str, default: str) -> str:
    """``RRGGBB`` (as written in the config) -> the ``&H00BBGGRR`` libass wants."""
    text = str(rgb or default).lstrip("#").upper()
    if len(text) != 6 or any(c not in "0123456789ABCDEF" for c in text):
        text = default
    return f"&H00{text[4:6]}{text[2:4]}{text[0:2]}"


def subtitle_style(sub: dict[str, Any]) -> str:
    """The libass ``force_style`` string from ``[subtitles]``: font, size, colours, bold, box or outline, position."""
    return (f"FontName={sub.get('font', 'Arial')},FontSize={sub.get('font_size', 16)},"
            f"PrimaryColour={ass_colour(sub.get('colour', ''), 'FFFFFF')},"
            f"OutlineColour={ass_colour(sub.get('outline_colour', ''), '000000')},"
            f"BackColour={ass_colour(sub.get('back_colour', ''), '000000')},Bold={1 if sub.get('bold') else 0},"
            f"BorderStyle={3 if int(sub.get('border_style', 1)) == 3 else 1},MarginV={sub.get('margin_v', 42)},"
            f"Outline={sub.get('outline', 1)},Alignment={int(sub.get('alignment', 2))}")


def write_card_ass(lines: list[str], vid: dict[str, Any], font: str, duration: float, dest: Path) -> None:
    """A text card: the heading big and bold, the rest smaller, all centred."""
    w, h = vid["width"], vid["height"]
    big, small = max(h // 22, 30), max(h // 36, 20)
    text = "\\N".join(str(x).replace("\n", " ") for x in lines[1:])
    body = f"{{\\b1\\fs{big}}}{str(lines[0]) if lines else ''}"
    if text:
        body += f"{{\\b0\\fs{small}}}\\N\\N{text}"
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV",
        f"Style: Card,{font},{small},&H00FFFFFF,&H00000000,&H00000000,0,1,0,0,5,60,60,60", "",
        "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},Card,,0,0,0,,{body}"]
    dest.write_text("\n".join(head) + "\n", encoding="utf-8")


def swell_expr(swell: dict[str, Any]) -> str:
    """Volume factor over time: 1 normally, ``db`` louder inside each interval (ramped at both ends)."""
    boost = 10 ** (float(swell["db"]) / 20) - 1.0
    ramp = max(float(swell.get("ramp", 0.4)), 0.05)
    terms = [f"clip(min((t-{a})/{ramp},({b}-t)/{ramp}),0,1)" for a, b in swell["intervals"][:40]]
    return "1+" + f"{boost:.4f}*(" + "+".join(terms) + ")"


def audio_cmd(ffmpeg: str, tl: dict[str, Any], dest: Path) -> list[str]:
    a, total = tl["audio"], float(tl["total_sec"])
    rate = a["rate"]
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-t", f"{total}",
           "-i", f"anullsrc=r={rate}:cl=stereo"]
    chains: list[str] = []
    n = 1

    def delayed(item: dict[str, Any], gain: float, label: str, trim: bool = False) -> str:
        nonlocal n
        cmd.extend(["-i", item["path"]])
        ms = int(round(float(item["start"]) * 1000))
        cut = ""
        if trim and item.get("duration"):          # end with the shot: cut, with a short fade so it does not click
            length = float(item["duration"])
            cut = f"atrim=0:{length},afade=t=out:st={max(length - 0.1, 0):.3f}:d=0.1,"
        chains.append(f"[{n}:a]aresample={rate},aformat=channel_layouts=stereo,{cut}adelay={ms}|{ms},"
                      f"volume={gain}[{label}]")
        n += 1
        return f"[{label}]"

    voice = ["[0:a]"] + [delayed(s, a["voice_gain"], f"v{i}") for i, s in enumerate(tl["speech"])]
    chains.append(f"{''.join(voice)}amix=inputs={len(voice)}:duration=first:normalize=0[voice]")
    sfx = [delayed(s, a["sfx_gain"], f"x{i}", trim=True) for i, s in enumerate(tl["sfx"])]
    mixed = ["[vmix]"]
    chains.append("[voice]asplit=2[vmix][vkey]")
    if tl["music"]:
        beds: list[str] = []
        for k, m in enumerate(tl["music"]):
            cmd.extend(["-stream_loop", "-1", "-i", m["path"]])
            dur, ms = float(m["duration"]), int(round(float(m["start"]) * 1000))
            fin, fout = min(float(m.get("fade_in", 0) or 0), dur / 2), min(float(m.get("fade_out", 0) or 0), dur / 2)
            shape = (f"afade=t=in:st=0:d={fin}," if fin > 0 else "") + (
                f"afade=t=out:st={round(dur - fout, 3)}:d={fout}," if fout > 0 else "")
            chains.append(f"[{n}:a]aresample={rate},aformat=channel_layouts=stereo,atrim=0:{dur},{shape}"
                          f"volume={m['gain']},adelay={ms}|{ms}[bed{k}]")
            beds.append(f"[bed{k}]")
            n += 1
        if len(beds) == 1:
            chains.append(f"{beds[0]}anull[bgm0]")
        else:
            chains.append(f"{''.join(beds)}amix=inputs={len(beds)}:duration=longest:normalize=0[bgm0]")
        swell = tl.get("music_swell")
        if swell and swell.get("intervals"):
            chains.append(f"[bgm0]volume='{swell_expr(swell)}':eval=frame[bgm]")
        else:
            chains.append("[bgm0]anull[bgm]")
        chains.append("[bgm][vkey]sidechaincompress=threshold=0.04:ratio=8:attack=20:release=400[duck]")
        mixed.append("[duck]")
    else:
        chains.append("[vkey]anullsink")
    tone = tl.get("room_tone")
    if tone and tone.get("blocks"):
        amp = 10 ** (float(tone["db"]) / 20)
        fade = float(tone.get("fade", 0.4))
        for k, blk in enumerate(tone["blocks"]):
            dur, ms = float(blk["duration"]), int(round(float(blk["start"]) * 1000))
            f = min(fade, dur / 2)
            cmd.extend(["-f", "lavfi", "-t", f"{dur}", "-i", f"anoisesrc=color={tone['color']}:r={rate}:a={amp:.6f}"])
            chains.append(f"[{n}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,lowpass=f=600,"
                          f"afade=t=in:st=0:d={f},afade=t=out:st={round(dur - f, 3)}:d={f},adelay={ms}|{ms}[tone{k}]")
            mixed.append(f"[tone{k}]")
            n += 1
    mixed += sfx
    ln = a["loudnorm"]
    # loudnorm on pure silence yields NaN and breaks aac; a room tone alone is not content to be brought up to -16 LUFS
    audible = bool(tl["speech"] or tl["sfx"] or tl["music"])
    norm = f"loudnorm=I={ln['i']}:TP={ln['tp']}:LRA={ln['lra']}," if audible else ""
    chains.append(f"{''.join(mixed)}amix=inputs={len(mixed)}:duration=first:normalize=0,"
                  f"{norm}aresample={rate},atrim=0:{total}[out]")
    return [*cmd, "-filter_complex", ";".join(chains), "-map", "[out]", "-c:a", a["codec"], "-b:a", a["bitrate"],
            str(dest)]


def ass_time(sec: float) -> str:
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    return f"{h}:{m:02d}:{cs // 100:02d}.{cs % 100:02d}"


def write_label_ass(label: dict[str, Any], vid: dict[str, Any], font: str, dest: Path) -> None:
    """The visible AIGC label: small in the top-left corner all along; with an end card also big in the middle."""
    w, h = vid["width"], vid["height"]
    small, big = max(h // 56, 14), max(h // 24, 28)
    text = str(label["text"]).replace("\n", " ")
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BackColour,Bold,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV",
        f"Style: Tag,{font},{small},&H00FFFFFF,&H00000000,&H80000000,0,1,1,0,7,24,24,24",
        f"Style: Card,{font},{big},&H00FFFFFF,&H00000000,&H80000000,1,1,2,0,5,40,40,40", "",
        "[Events]", "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    end = float(label["total"])
    lines.append(f"Dialogue: 0,{ass_time(0)},{ass_time(end)},Tag,,0,0,0,,{text}")
    if label.get("mode") == "both" and "card_start" in label:
        lines.append(f"Dialogue: 1,{ass_time(float(label['card_start']))},{ass_time(end)},Card,,0,0,0,,{text}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


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

    libass = has_filter(ffmpeg, "subtitles") and has_filter(ffmpeg, "ass")
    dissolve_ok = has_filter(ffmpeg, "xfade")
    font = str(tl["subtitles"].get("font", "Arial"))
    notes: list[dict[str, Any]] = []
    parts: list[Path] = []
    overlaps: list[float] = []              # overlaps[i]: seconds segment i melts into segment i+1
    for i, seg in enumerate(segs):
        emit(i, total, f"shot {i + 1}/{len(segs)}")
        dest = work / f"seg_{i:03d}.mp4"
        want = float(seg.get("dissolve_out", 0) or 0) if i + 1 < len(segs) else 0.0
        if want and not dissolve_ok:
            notes.append({"code": "dissolve_unavailable"})
            want = 0.0
        card_ass: str | None = None
        if seg["source"]["type"] == "card":
            if libass:
                card_ass = f"card_{i:03d}.ass"
                write_card_ass(seg["source"].get("lines", []), vid, font, float(seg["duration"]) + want,
                               work / card_ass)
            else:
                notes.append({"code": "card_without_libass", "card": seg["source"].get("kind", "")})
        run(segment_cmd(ffmpeg, seg, vid, dest, card_ass, want), cwd=work)
        parts.append(dest)
        overlaps.append(want)

    emit(len(segs), total, "video")
    units: list[Path] = []
    i = 0
    while i < len(parts):
        j = i
        while j < len(parts) - 1 and overlaps[j] > 0:
            j += 1
        if j == i:
            units.append(parts[i])
        else:                                  # a run of shots joined by dissolves becomes one clip
            unit = work / f"unit_{i:03d}.mp4"
            lengths = [float(segs[k]["duration"]) + overlaps[k] for k in range(i, j + 1)]
            run(join_cmd(ffmpeg, [parts[k].name for k in range(i, j + 1)], lengths, overlaps[i:j], unit.name),
                cwd=work)
            units.append(unit)
        i = j + 1
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in units), "utf-8")
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
    label = tl.get("label")
    if label and not libass:                      # a required label is never silently dropped
        print("this ffmpeg has no libass: cannot burn the visible AIGC label", file=sys.stderr, flush=True)
        return 3
    burn = bool(cfg.get("burn_subtitles", True)) and bool(cues) and libass
    sub = tl["subtitles"]
    style = subtitle_style(sub)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-nostdin", "-i", str(joined), "-i", str(mixed)]
    filters: list[str] = []
    if burn:
        filters.append(f"subtitles=filename='{srt.name}':force_style='{style}'")
    if label:
        write_label_ass(label, vid, str(sub.get("font", "Arial")), out / "label.ass")
        filters.append("ass=filename='label.ass'")
    if filters:
        cmd += ["-vf", ",".join(filters), "-c:v", vid["codec"], "-crf", str(vid["crf"]), "-pix_fmt", vid["pix_fmt"],
                "-preset", "medium"]
    else:
        cmd += ["-c:v", "copy"]
    for key, value in (tl.get("metadata") or {}).items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy", "-movflags", "+faststart", "-shortest", str(final)]
    run(cmd, cwd=out)
    emit(total, total, "done")
    report = {"burned_subtitles": burn, "label": bool(label), "total_sec": tl["total_sec"],
              "warnings": [*tl.get("warnings", []), *notes]}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
