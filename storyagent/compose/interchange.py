"""Edit interchange with Final Cut Pro (FCPXML 1.10) and back.

* ``build_fcpxml``      - the episode's timeline as an FCPXML project: picture on the primary storyline (one clip per
                          shot, slowed shots as a retime), dialogue / effects / music on their own audio lanes with FCP
                          roles, cards as gaps with a note. Media is addressed relative to the file (``./media/...``)
                          unless an absolute base is given.
* ``build_fcp_package`` - writes ``<dest>/episode.fcpxml``, the media it points to, ``episode.srt`` (FCP
                          imports captions from it) and ``interchange.json`` (which media file is which shot / asset).
* ``parse_fcpxml``      - reads a project edited in FCP back into a flat list of clips.
* ``diff_edit``         - what the edit changed against the timeline we exported: per shot kept / trimmed / moved /
                          removed, clips added from elsewhere, the new length.
* ``import_final``      - registers the finished film rendered by FCP as the episode (a new version, current), so
                          QC and the delivery package describe that file.

The output is validated against Apple's FCPXML 1.10 DTD (``xmllint --dtdvalid``) in the tests when xmllint and Final
Cut Pro are present; opening it in Final Cut Pro itself is the operator's acceptance step.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from storyagent.config import Settings
from storyagent.domain import MediaKind
from storyagent.media.ledger import Ledger, LedgerError, NewAsset
from storyagent.media.probe import probe
from storyagent.util import now_iso

from .export import ExportError, _refs, _sha256, find_episode

FCPXML_VERSION = "1.10"
INTERCHANGE_SCHEMA = "storyagent.interchange.v1"
ROLES = {"speech": "dialogue", "sfx": "effects", "music": "music"}
LANES = {"speech": -1, "sfx": -2, "music": -3}


class InterchangeError(ValueError):
    """``code`` is an i18n key suffix (``interchange.error.<code>``)."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code, self.params = code, params


@dataclass(frozen=True)
class MediaFile:
    ref: str                  # "asset:<id>"
    rel: str                  # path inside the package, forward slashes
    kind: str                 # the asset kind
    has_video: bool
    has_audio: bool
    duration_sec: float | None
    width: int | None = None
    height: int | None = None


def _tc(frames: int, fps: int) -> str:
    return "0s" if frames == 0 else f"{frames}/{fps}s"


def _frames(sec: float, fps: int) -> int:
    return max(int(round(sec * fps)), 0)


def _db(gain: float) -> str:
    return f"{20 * math.log10(gain):.1f}dB" if gain > 0 else "-96dB"


def _src(rel: str, absolute: Path | None) -> str:
    if absolute is not None:
        return (absolute / rel).resolve().as_uri()
    return "./" + quote(rel)


def build_fcpxml(timeline: Mapping[str, Any], media: Mapping[str, MediaFile], *, name: str,
                 absolute: Path | None = None) -> str:
    video = timeline["video"]
    fps, width, height = int(video["fps"]), int(video["width"]), int(video["height"])
    total = _frames(float(timeline["total_sec"]), fps)
    root = ET.Element("fcpxml", version=FCPXML_VERSION)
    res = ET.SubElement(root, "resources")
    ET.SubElement(res, "format", id="r1", name=f"FFVideoFormat{width}x{height}p{fps}",
                  frameDuration=f"1/{fps}s", width=str(width), height=str(height), colorSpace="1-1-1 (Rec. 709)")
    ids: dict[str, str] = {}

    def resource(ref: str) -> str | None:
        m = media.get(ref)
        if m is None:
            return None
        if ref not in ids:
            ids[ref] = f"a{len(ids) + 1}"
            attrs = {"id": ids[ref], "name": Path(m.rel).stem, "start": "0s",
                     "duration": _tc(_frames(m.duration_sec or 0.0, fps), fps) if m.has_audio or m.kind.startswith(
                         "video") else "0s",
                     "hasVideo": "1" if m.has_video else "0", "hasAudio": "1" if m.has_audio else "0"}
            if m.has_video:
                attrs["format"] = "r1"
                attrs["videoSources"] = "1"
            if m.has_audio:
                attrs["audioSources"] = "1"
                attrs["audioChannels"] = "2"
                attrs["audioRate"] = str(int(timeline["audio"].get("rate", 48000)))
            asset = ET.SubElement(res, "asset", attrs)
            ET.SubElement(asset, "media-rep", kind="original-media", src=_src(m.rel, absolute))
        return ids[ref]

    lib = ET.SubElement(root, "library")
    event = ET.SubElement(lib, "event", name=name)
    project = ET.SubElement(event, "project", name=name)
    seq = ET.SubElement(project, "sequence", format="r1", duration=_tc(total, fps), tcStart="0s", tcFormat="NDF",
                        audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(seq, "spine")
    spine_items: list[tuple[ET.Element, int, bool]] = []         # (element, offset frames, retimed)
    for seg in timeline["segments"]:
        offset, dur = _frames(float(seg["start"]), fps), max(_frames(float(seg["duration"]), fps), 1)
        src = seg.get("source", {"type": "black"})
        rid = resource(src["asset"]) if src.get("type") in ("video", "image") and "asset" in src else None
        label = str(seg.get("label") or f"shot {seg.get('shot_id', '')}")
        if rid is None:
            gap = ET.SubElement(spine, "gap", name=label, offset=_tc(offset, fps), start="0s", duration=_tc(dur, fps))
            lines = src.get("lines")
            if lines:
                ET.SubElement(gap, "note").text = " / ".join(str(x) for x in lines)
            spine_items.append((gap, offset, False))
            continue
        clip = ET.SubElement(spine, "asset-clip", ref=rid, offset=_tc(offset, fps), name=label, start="0s",
                             duration=_tc(dur, fps), format="r1", tcFormat="NDF")
        retimed = src.get("mode") == "slow" and float(src.get("speed", 1.0)) > 1.0
        if retimed:
            clip_sec = media[src["asset"]].duration_sec or dur / fps
            played = min(clip_sec * float(src["speed"]), dur / fps)
            tm = ET.SubElement(clip, "timeMap")
            ET.SubElement(tm, "timept", time="0s", value="0s", interp="linear")
            ET.SubElement(tm, "timept", time=_tc(_frames(played, fps), fps), value=_tc(_frames(clip_sec, fps), fps),
                          interp="linear")
            if played < dur / fps - 1 / fps:
                ET.SubElement(tm, "timept", time=_tc(dur, fps), value=_tc(_frames(clip_sec, fps), fps),
                              interp="linear")
        spine_items.append((clip, offset, retimed))
    anchor_el, anchor_off, _ = next((it for it in spine_items if not it[2]), spine_items[0]) if spine_items \
        else (None, 0, False)
    if anchor_el is None:
        raise InterchangeError("empty_timeline")
    voice, sfx_gain = float(timeline["audio"].get("voice_gain", 1.0)), float(timeline["audio"].get("sfx_gain", 0.5))

    def connect(kind: str, ref: str, start: float, duration: float, gain: float, label: str) -> None:
        rid = resource(ref)
        if rid is None or duration <= 0:
            return
        rel = _frames(start, fps) - anchor_off
        clip = ET.SubElement(anchor_el, "asset-clip", ref=rid, lane=str(LANES[kind]),
                             offset=_tc(rel, fps) if rel >= 0 else f"{rel}/{fps}s", name=label, start="0s",
                             duration=_tc(max(_frames(duration, fps), 1), fps), audioRole=ROLES[kind])
        if abs(gain - 1.0) > 1e-3:
            ET.SubElement(clip, "adjust-volume", amount=_db(gain))

    for i, s in enumerate(timeline.get("speech", []), start=1):
        connect("speech", s["asset"], float(s["start"]), float(s["duration"]), voice, f"speech {i}")
    for i, s in enumerate(timeline.get("sfx", []), start=1):
        connect("sfx", s["asset"], float(s["start"]), float(s["duration"]), sfx_gain, f"effect {i}")
    for m in timeline.get("music", []):
        mf = media.get(m["asset"])
        left, at, n = float(m["duration"]), float(m["start"]), 1
        length = mf.duration_sec if mf and mf.duration_sec else left
        while left > 1e-3:              # a bed shorter than the film repeats back to back
            piece = min(length, left)
            connect("music", m["asset"], at, piece, float(m.get("gain", 1.0)), f"music {n}")
            at, left, n = at + piece, left - piece, n + 1
            if not m.get("loop"):
                break
    ET.indent(root)
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n\n' + body + "\n"


# ----------------------------------------------------------------------------------------------- package
def _media_files(
    settings: Settings, ledger: Ledger, timeline: Mapping[str, Any]
) -> tuple[dict[str, MediaFile], dict[str, Path]]:
    ids: list[str] = []
    _refs(timeline, ids)
    shot_of: dict[str, int] = {}
    for seg in timeline.get("segments", []):
        a = seg.get("source", {}).get("asset")
        if a:
            shot_of[a] = int(seg.get("shot_id") or 0)
    media: dict[str, MediaFile] = {}
    files: dict[str, Path] = {}
    counters = {"speech": 0, "sfx": 0}
    sfx_refs = {s["asset"] for s in timeline.get("sfx", [])}
    for asset_id in ids:
        try:
            a = ledger.get_asset(asset_id)
        except LedgerError:
            continue
        src = settings.home / a.path
        if not src.is_file():
            continue
        ref, ext = f"asset:{asset_id}", src.suffix.lower()
        if a.kind == MediaKind.VIDEO_GENERATE.value or a.kind.startswith("video"):
            rel, kind = f"media/video/{shot_of.get(ref, 0):04d}_{asset_id[:8]}{ext}", "video"
        elif a.kind.startswith("image"):
            rel, kind = f"media/image/{shot_of.get(ref, 0):04d}_{asset_id[:8]}{ext}", "image"
        elif ref in sfx_refs:
            counters["sfx"] += 1
            rel, kind = f"media/sfx/{counters['sfx']:03d}_{asset_id[:8]}{ext}", "sfx"
        elif a.kind == MediaKind.AUDIO_MUSIC.value:
            rel, kind = f"media/music/{asset_id[:8]}{ext}", "music"
        else:
            counters["speech"] += 1
            rel, kind = f"media/speech/{counters['speech']:03d}_{asset_id[:8]}{ext}", "speech"
        media[ref] = MediaFile(ref, rel, a.kind, has_video=kind in ("video", "image"),
                               has_audio=kind in ("speech", "sfx", "music") or bool(a.has_audio),
                               duration_sec=a.duration_sec, width=a.width, height=a.height)
        files[rel] = src
    return media, files


def _plan(settings: Settings, ledger: Ledger, project_id: int, episode: int) -> tuple[
        Any, dict[str, Any], dict[str, MediaFile], dict[str, Path], dict[str, Any]]:
    main = find_episode(ledger, project_id, episode)
    job = ledger.get_job(main.job_id) if main.job_id else None
    timeline = (job.inputs.get("timeline") if job else None) or {}
    if not timeline.get("segments"):
        raise InterchangeError("no_timeline")
    media, files = _media_files(settings, ledger, timeline)
    core = {
        "schema": INTERCHANGE_SCHEMA, "project_id": project_id, "episode": episode, "episode_asset": main.id,
        "fps": timeline["video"]["fps"], "total_sec": timeline["total_sec"],
        "media": {m.rel: {"asset": m.ref[6:], "kind": m.kind} for m in media.values()},
        "segments": [{"shot_id": s.get("shot_id", 0), "label": s.get("label", ""), "start": s["start"],
                      "duration": s["duration"],
                      "media": (media[s["source"]["asset"]].rel if s.get("source", {}).get("asset") in media else None)}
                     for s in timeline["segments"]],
    }
    return main, timeline, media, files, core


def episode_manifest(settings: Settings, ledger: Ledger, project_id: int, episode: int) -> dict[str, Any]:
    """What an export of this episode would contain (the reference an edited project is compared with)."""
    return _plan(settings, ledger, project_id, episode)[4]


def build_fcp_package(settings: Settings, ledger: Ledger, project_id: int, episode: int, dest: Path, *,
                      absolute: bool = False, name: str = "") -> dict[str, Any]:
    """Write the FCP project for an assembled episode into ``dest`` (created if needed); returns the manifest."""
    main, timeline, media, files, core = _plan(settings, ledger, project_id, episode)
    dest.mkdir(parents=True, exist_ok=True)
    for rel, src in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    title = name or str(timeline.get("title") or f"Episode {episode}")
    (dest / "episode.fcpxml").write_text(
        build_fcpxml(timeline, media, name=title, absolute=dest if absolute else None), "utf-8")
    for a in ledger.list_assets(project_id=project_id, kind=MediaKind.COMPOSE_ASSEMBLE.value, slot="srt"):
        if a.job_id == main.job_id and (settings.home / a.path).is_file():
            shutil.copyfile(settings.home / a.path, dest / "episode.srt")
            break
    manifest = {**core, "created_at": now_iso(), "absolute_media": absolute,
                "files": [{"name": rel, "bytes": (dest / rel).stat().st_size, "sha256": _sha256(dest / rel)}
                          for rel in ["episode.fcpxml", *files]]}
    (dest / "interchange.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    return manifest


# ----------------------------------------------------------------------------------------------- read back
_TIME = re.compile(r"^(-?\d+)(?:/(\d+))?s$")


def _sec(value: str | None) -> float:
    m = _TIME.match(value or "0s")
    if not m:
        return 0.0
    return float(Fraction(int(m.group(1)), int(m.group(2) or 1)))


def parse_fcpxml(text: str) -> dict[str, Any]:
    """The clips of the first project of an FCPXML file: spine clips and connected clips with their media file names."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise InterchangeError("bad_xml", detail=str(exc)) from exc
    if root.tag != "fcpxml":
        raise InterchangeError("not_fcpxml")
    srcs: dict[str, str] = {}
    for asset in root.iter("asset"):
        rep = asset.find("media-rep")
        raw = rep.get("src") if rep is not None else asset.get("src")
        if raw:
            srcs[asset.get("id", "")] = Path(unquote(urlparse(raw).path)).name
    seq = root.find(".//sequence")
    if seq is None:
        raise InterchangeError("no_sequence")
    fmt = next((f for f in root.iter("format") if f.get("id") == seq.get("format")), None)
    fd = Fraction(1, 24)
    if fmt is not None and fmt.get("frameDuration"):
        m = _TIME.match(fmt.get("frameDuration", ""))
        fd = Fraction(int(m.group(1)), int(m.group(2) or 1)) if m else fd
    clips: list[dict[str, Any]] = []

    def walk(el: ET.Element, parent_offset: float, lane: int, spine: bool) -> None:
        for child in el:
            if child.tag in ("asset-clip", "clip", "ref-clip", "gap", "video", "audio", "title"):
                offset = _sec(child.get("offset"))
                start_in_parent = offset if spine else parent_offset + offset
                ref = child.get("ref", "")
                clips.append({"name": child.get("name", ""), "tag": child.tag, "media": srcs.get(ref, ""),
                              "offset": round(start_in_parent, 3), "start": round(_sec(child.get("start")), 3),
                              "duration": round(_sec(child.get("duration")), 3), "lane": int(child.get("lane", 0) or 0),
                              "role": child.get("audioRole") or child.get("videoRole") or "",
                              "retimed": child.find("timeMap") is not None})
                walk(child, start_in_parent if spine else start_in_parent, lane, False)

    spine = seq.find("spine")
    if spine is not None:
        walk(spine, 0.0, 0, True)
    return {"fps": float(1 / fd), "duration_sec": round(_sec(seq.get("duration")), 3), "clips": clips}


def diff_edit(
    manifest: Mapping[str, Any], edited: Mapping[str, Any], *, tolerance: float | None = None
) -> dict[str, Any]:
    """Compare an edited project with what was exported (``interchange.json``): per shot kept/trimmed/moved/removed."""
    tol = tolerance if tolerance is not None else 1.5 / float(manifest["fps"])
    spine = [c for c in edited["clips"] if c["lane"] == 0 and c["media"]]
    by_media: dict[str, list[dict[str, Any]]] = {}
    for c in spine:
        by_media.setdefault(c["media"], []).append(c)
    original = [s for s in manifest["segments"] if s["media"]]
    order_before = [Path(s["media"]).name for s in original]
    order_after = [c["media"] for c in spine if c["media"] in set(order_before)]
    shots: list[dict[str, Any]] = []
    for s in original:
        name = Path(s["media"]).name
        found = by_media.get(name)
        if not found:
            shots.append({"shot_id": s["shot_id"], "status": "removed", "duration": s["duration"], "new_duration": 0.0})
            continue
        new = sum(c["duration"] for c in found)
        status = "trimmed" if abs(new - float(s["duration"])) > tol else "kept"
        if order_after.index(name) != [n for n in order_before if n in order_after].index(name):
            status = "moved" if status == "kept" else status
        shots.append({"shot_id": s["shot_id"], "status": status, "duration": s["duration"],
                      "new_duration": round(new, 3)})
    known = set(order_before)
    added = [{"name": c["name"], "media": c["media"], "duration": c["duration"]} for c in spine
             if c["media"] not in known]
    changed = [s for s in shots if s["status"] != "kept"]
    return {"original_sec": manifest["total_sec"], "edited_sec": edited["duration_sec"], "shots": shots,
            "added": added, "changed": len(changed) + len(added)}


# ----------------------------------------------------------------------------------------------- finished film
def import_final(settings: Settings, ledger: Ledger, project_id: int, episode: int, src: Path, *,
                 note: str = "") -> Any:
    """Register the film finished in an editor as the episode (a new current version of ``compose.assemble``)."""
    if not src.is_file():
        raise InterchangeError("file_missing", path=str(src))
    ext = src.suffix.lower()
    if ext not in (".mp4", ".mov", ".m4v"):
        raise InterchangeError("not_a_film", ext=ext)
    row = ledger.db.conn.execute("SELECT slug FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise InterchangeError("unknown_project", id=project_id)
    kind = MediaKind.COMPOSE_ASSEMBLE.value
    dest_dir = settings.project_media_dir(row["slug"], kind)
    dest_dir.mkdir(parents=True, exist_ok=True)
    sha = _sha256(src)
    dest = dest_dir / f"external_ep{episode:02d}_{sha[:12]}{ext}"
    shutil.copyfile(src, dest)
    info = probe(settings, dest)
    if info is None or not info.duration_sec:
        dest.unlink(missing_ok=True)
        raise InterchangeError("unreadable_film", path=src.name)
    prior = None
    try:
        prior = find_episode(ledger, project_id, episode)
    except ExportError:
        pass
    return ledger.register_asset(
        NewAsset(kind=kind, slot=f"episode_{episode:02d}", path=str(dest.relative_to(settings.home)), sha256=sha,
                 bytes=dest.stat().st_size, duration_sec=info.duration_sec, width=info.width, height=info.height,
                 fps=info.fps, has_audio=info.has_audio,
                 metadata={"original_name": src.name, "external_edit": True, "replaces": prior.id if prior else None,
                           "note": note},
                 provenance={"schema": "storyagent.provenance.v1", "origin": "external_edit",
                             "original_name": src.name, "imported_at": now_iso(),
                             "derived_from": prior.id if prior else None}),
        project_id=project_id, adopt=True)
