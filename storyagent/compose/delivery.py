"""One episode for one destination: re-encoded to the platform's frame, loudness and size limits, with a cover frame, a
teaser and subtitle files in every wanted language (``platforms.toml``).

    <dest>/<platform>/episode.mp4          the film in the platform's frame (crop | pad | blurred backdrop), loudness
                                           normalised in two passes (measure, then a linear correction)
    <dest>/<platform>/cover.jpg            a frame from ``cover_sec``
    <dest>/<platform>/trailer.mp4          the first ``trailer_sec`` seconds with a short fade out (0 = none)
    <dest>/<platform>/subtitles.<lang>.srt the episode's subtitles, and one file per requested translation
    <dest>/<platform>/delivery.json        what was done: platform, measured results, warnings, sha256 of every file

Nothing is ever cut to fit: a film longer than the platform allows, or an encode over its size limit, is reported as a
warning and delivered as it is. The AIGC metadata of the container is carried over; a visible label that a ``crop``
reframe may cut away is reported.
"""
from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storyagent import __version__
from storyagent.config import Platform, Settings
from storyagent.content import check_export
from storyagent.domain import MediaKind
from storyagent.media.compliance import redact_for_export
from storyagent.media.ledger import Ledger
from storyagent.media.probe import MediaInfo, find_tool, probe
from storyagent.util import now_iso

from .export import ExportError, _sha256, find_episode

SCHEMA = "storyagent.delivery.v1"
ASPECT_SAME = 0.01
LABEL_RE = re.compile(r"\{[\s\S]*\}\s*$")
_LUFS = re.compile(r"I:\s+(-?[\d.]+|-inf) LUFS")
_CUE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")
TRANSLATE_CHUNK = 40

Translate = Callable[[Sequence[str], str], list[str]]
Progress = Callable[[float, str], None]


class DeliveryError(ExportError):
    """Delivery could not be produced; ``code`` is an ``delivery.error.<code>`` i18n key."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def _seconds(match: re.Match[str]) -> float:
    h, m, s, ms = (int(x) for x in match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip().lstrip("\ufeff")):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            if "-->" in line:
                a, _, b = line.partition("-->")
                ma, mb = _CUE.search(a), _CUE.search(b)
                if ma and mb:
                    cues.append(Cue(_seconds(ma), _seconds(mb), "\n".join(lines[i + 1:])))
                break
    return cues


def _stamp(sec: float) -> str:
    ms = max(round(sec * 1000), 0)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def format_srt(cues: Sequence[Cue]) -> str:
    return "".join(f"{i}\n{_stamp(c.start)} --> {_stamp(c.end)}\n{c.text}\n\n" for i, c in enumerate(cues, 1))


def translate_cues(cues: Sequence[Cue], language: str, translate: Translate) -> list[Cue]:
    """The same cues (same timing) with their text translated; the callback gets chunks and must return one text per
    input text."""
    out: list[Cue] = []
    for i in range(0, len(cues), TRANSLATE_CHUNK):
        chunk = cues[i:i + TRANSLATE_CHUNK]
        texts = translate([c.text for c in chunk], language)
        if len(texts) != len(chunk):
            raise DeliveryError("translation_mismatch", f"{language}: {len(texts)} lines for {len(chunk)} cues")
        out += [Cue(c.start, c.end, str(t).strip() or c.text) for c, t in zip(chunk, texts, strict=True)]
    return out


def video_chain(src_w: int, src_h: int, p: Platform) -> str:
    """The ``-filter_complex`` that puts a ``src_w x src_h`` picture into the platform's frame, output label ``[v]``."""
    w, h = p.width, p.height
    tail = f"setsar=1,fps={p.fps:g},format=yuv420p[v]"
    if src_w and src_h and abs(src_w / src_h - w / h) <= ASPECT_SAME:
        return f"[0:v]scale={w}:{h}:flags=lanczos,{tail}"
    if p.reframe == "crop":
        return f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},{tail}"
    if p.reframe == "pad":
        return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,{tail}")
    return (f"[0:v]split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=24:3[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}")


def _run(argv: Sequence[str | Path], *, timeout: float = 3600) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run([str(a) for a in argv], capture_output=True, text=True,  # noqa: S603 - fixed argv
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeliveryError("encode_failed", str(exc)) from exc


def _loudnorm_args(p: Platform) -> str:
    return f"I={p.loudnorm_i:g}:TP={p.loudnorm_tp:g}:LRA={p.loudnorm_lra:g}"


def measure_loudness(ffmpeg: Path, film: Path, p: Platform) -> dict[str, float] | None:
    """Pass 1 of loudnorm: the film's integrated loudness, true peak, range, threshold and the offset it suggests."""
    proc = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", film, "-vn", "-af",
                 f"loudnorm={_loudnorm_args(p)}:print_format=json", "-f", "null", "-"])
    found = LABEL_RE.search(proc.stderr or "")
    if proc.returncode != 0 or not found:
        return None
    try:
        raw = json.loads(found.group(0))
        values = {k: float(raw[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError):
        return None
    return values if all(abs(v) != float("inf") for v in values.values()) else None


def _audio_filter(p: Platform, measured: dict[str, float] | None) -> str:
    base = f"loudnorm={_loudnorm_args(p)}"
    if measured:
        base += (f":measured_I={measured['input_i']:g}:measured_TP={measured['input_tp']:g}"
                 f":measured_LRA={measured['input_lra']:g}:measured_thresh={measured['input_thresh']:g}"
                 f":offset={measured['target_offset']:g}:linear=true")
    return base + ",aresample=48000"


def _kbps(text: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", text.strip())
    if not m:
        return 192.0
    return float(m.group(1)) * {"m": 1000.0, "k": 1.0, "": 0.001}[m.group(2).lower()]


def _measured_lufs(ffmpeg: Path, path: Path) -> float | None:
    proc = _run([ffmpeg, "-hide_banner", "-nostdin", "-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"])
    found = _LUFS.findall(proc.stderr or "")
    try:
        return float(found[-1]) if found else None
    except ValueError:
        return None


def _encode(ffmpeg: Path, film: Path, out: Path, info: MediaInfo, p: Platform, warnings: list[str]
            ) -> dict[str, Any]:
    dur = info.duration_sec or 0.0
    argv: list[str | Path] = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-i", film, "-filter_complex",
                              video_chain(info.width or 0, info.height or 0, p), "-map", "[v]"]
    measured = None
    if info.has_audio:
        measured = measure_loudness(ffmpeg, film, p)
        if measured is None:
            warnings.append("loudness_unmeasured")
        argv += ["-map", "0:a:0", "-af", _audio_filter(p, measured), "-c:a", "aac", "-b:a", p.audio_bitrate]
    else:
        warnings.append("no_audio")
    argv += ["-c:v", "libx264", "-preset", "medium", "-map_metadata", "0", "-movflags", "+faststart"]
    video_kbps = 0.0
    if p.max_mb > 0 and dur > 0:
        total_kbps = p.max_mb * 8 * 1024 * 1024 * 0.95 / dur / 1000
        video_kbps = total_kbps - (_kbps(p.audio_bitrate) if info.has_audio else 0)
        if video_kbps < 150:
            warnings.append("size_unreachable")
            video_kbps = max(video_kbps, 150)
        argv += ["-b:v", f"{video_kbps:.0f}k", "-maxrate", f"{video_kbps * 1.5:.0f}k",
                 "-bufsize", f"{video_kbps * 2:.0f}k"]
    else:
        argv += ["-crf", str(p.crf)]
    proc = _run([*argv, out])
    if proc.returncode != 0 or not out.is_file():
        tail = (proc.stderr or "").strip().splitlines()
        raise DeliveryError("encode_failed", tail[-1] if tail else "ffmpeg failed")
    return {"measured": measured, "video_kbps": round(video_kbps) if video_kbps else None}


def _cover(ffmpeg: Path, film: Path, out: Path, at: float) -> bool:
    proc = _run([ffmpeg, "-y", "-hide_banner", "-nostdin", "-ss", f"{at:.3f}", "-i", film, "-frames:v", "1",
                 "-q:v", "2", out])
    return proc.returncode == 0 and out.is_file()


def _trailer(ffmpeg: Path, film: Path, out: Path, info: MediaInfo, p: Platform) -> bool:
    length = min(p.trailer_sec, info.duration_sec or 0.0)
    fade = min(0.6, length / 4)
    argv: list[str | Path] = [ffmpeg, "-y", "-hide_banner", "-nostdin", "-i", film, "-t", f"{length:.3f}", "-vf",
                              f"fade=t=out:st={length - fade:.3f}:d={fade:.3f}"]
    if info.has_audio:
        argv += ["-af", f"afade=t=out:st={length - fade:.3f}:d={fade:.3f}", "-c:a", "aac", "-b:a", p.audio_bitrate]
    argv += ["-c:v", "libx264", "-preset", "medium", "-crf", str(p.crf), "-pix_fmt", "yuv420p", "-map_metadata", "0",
             "-movflags", "+faststart", out]
    proc = _run(argv)
    return proc.returncode == 0 and out.is_file()


def deliver(settings: Settings, ledger: Ledger, project_id: int, episode: int, platform: str | None, dest: Path,
            *, languages: Sequence[str] = (), translate: Translate | None = None, content_language: str = "",
            confirm_restricted: bool = False, progress: Progress | None = None) -> dict[str, Any]:
    """Write ``<dest>/<platform>/`` and return its ``delivery.json``. A project of a rating that does not allow
    export is refused (``ContentError``) unless ``confirm_restricted``. ``progress(fraction, step)`` is called between
    the steps (``encode``, ``cover``, ``trailer``, ``subtitles``); it may raise to stop the delivery."""
    step: Progress = progress or (lambda *_: None)
    rating = check_export(settings, ledger.db, project_id, confirmed=confirm_restricted)
    try:
        p = settings.platforms.get(platform)
    except KeyError as exc:
        raise DeliveryError("unknown_platform", str(platform)) from exc
    ffmpeg = find_tool(settings, "ffmpeg")
    if ffmpeg is None:
        raise DeliveryError("ffmpeg_missing")
    try:
        main = find_episode(ledger, project_id, episode)
    except ExportError as exc:
        raise DeliveryError("episode_not_ready", str(exc)) from exc
    film = settings.home / main.path
    info = probe(settings, film) if film.is_file() else None
    if info is None or not info.has_video:
        raise DeliveryError("episode_not_ready", "the episode file is missing or unreadable")
    job = ledger.get_job(main.job_id) if main.job_id else None
    cfg = settings.compliance_for()
    out_dir = dest / p.name
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    dur = info.duration_sec or 0.0
    reframed = bool(info.width and info.height) and abs((info.width or 1) / (info.height or 1)
                                                         - p.width / p.height) > ASPECT_SAME
    if reframed and p.reframe == "crop":
        if cfg.enabled and cfg.explicit_label != "off":
            warnings.append("crop_label")
        if job is not None and job.params.get("burn_subtitles"):
            warnings.append("crop_burned_subtitles")

    files: dict[str, Path] = {}
    step(0.02, "encode")
    result = _encode(ffmpeg, film, out_dir / "episode.mp4", info, p, warnings)
    files["episode.mp4"] = out_dir / "episode.mp4"
    out_info = probe(settings, files["episode.mp4"])
    size_mb = files["episode.mp4"].stat().st_size / (1024 * 1024)
    if p.max_duration_sec and dur > p.max_duration_sec:
        warnings.append("over_duration")
    if p.max_mb and size_mb > p.max_mb:
        warnings.append("over_size")
    lufs = _measured_lufs(ffmpeg, files["episode.mp4"]) if info.has_audio else None

    step(0.75, "cover")
    if p.cover_sec >= 0 and _cover(ffmpeg, files["episode.mp4"], out_dir / "cover.jpg",
                                   min(p.cover_sec, max(dur - 0.1, 0.0))):
        files["cover.jpg"] = out_dir / "cover.jpg"
    step(0.8, "trailer")
    if p.trailer_sec > 0:
        if dur <= p.trailer_sec + 0.5:
            warnings.append("trailer_too_long")
        elif _trailer(ffmpeg, files["episode.mp4"], out_dir / "trailer.mp4", info, p):
            files["trailer.mp4"] = out_dir / "trailer.mp4"

    subtitles: list[str] = []
    step(0.9, "subtitles")
    if p.subtitles == "sidecar":
        srt = next((a for a in ledger.list_assets(project_id=project_id, kind=MediaKind.COMPOSE_ASSEMBLE.value,
                                                  slot="srt")
                    if a.job_id == main.job_id and (settings.home / a.path).is_file()), None)
        if srt is None:
            warnings.append("no_subtitles")
        else:
            cues = parse_srt((settings.home / srt.path).read_text("utf-8"))
            base = content_language or "src"
            wanted = [x for x in dict.fromkeys(languages) if x and x != base]
            if wanted and translate is None:
                raise DeliveryError("translate_unavailable")
            versions = {base: cues}
            for lang in wanted:
                assert translate is not None
                versions[lang] = translate_cues(cues, lang, translate)
            for lang, items in versions.items():
                name = f"subtitles.{lang}.srt"
                (out_dir / name).write_text(format_srt(items), "utf-8")
                files[name] = out_dir / name
                subtitles.append(lang)

    record = {
        "schema": SCHEMA, "created_at": now_iso(), "generator": {"app": "storyagent", "version": __version__},
        "platform": p.as_dict(), "project_id": project_id, "episode": episode,
        "source": {"asset": main.id, "sha256": main.sha256, "width": info.width, "height": info.height,
                   "duration_sec": round(dur, 3), "provenance": redact_for_export(main.provenance)
                   if main.provenance else {}},
        "result": {"width": out_info.width if out_info else None, "height": out_info.height if out_info else None,
                   "fps": out_info.fps if out_info else None,
                   "duration_sec": round(out_info.duration_sec or 0.0, 3) if out_info else None,
                   "size_mb": round(size_mb, 2), "loudness_lufs": lufs, "target_lufs": p.loudnorm_i,
                   "input_loudness": result["measured"], "video_kbps": result["video_kbps"], "reframed": reframed},
        "rating": rating, "subtitles": subtitles, "warnings": list(dict.fromkeys(warnings)),
        "files": [{"name": n, "bytes": f.stat().st_size, "sha256": _sha256(f)} for n, f in files.items()],
    }
    (out_dir / "delivery.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), "utf-8")
    return record
