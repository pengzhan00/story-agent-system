"""Delivery package of one episode: the film, subtitles, source stems, a manifest and a redacted provenance record.

    <dest>/episode.mp4          the assembled episode (AIGC label/metadata already in it when compliance asks for it)
    <dest>/episode.srt          subtitles (also when they are burned in)
    <dest>/stems/               the music bed and every spoken line exactly as used, named by their place in the film
    <dest>/provenance.json      one entry per source asset: model, version, licence, attribution - no prompts, no paths
    <dest>/manifest.json        file list with sha256/bytes, the QC report, the timeline warnings

Everything is read from the ledger (the episode asset -> its compose job -> the timeline it ran with), so a package can
be rebuilt at any time and always describes what was really rendered.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from storyagent import __version__
from storyagent.config import Settings
from storyagent.domain import MediaKind
from storyagent.media.compliance import redact_for_export
from storyagent.media.ledger import Ledger, LedgerError
from storyagent.util import now_iso

from .qc import run_qc

MANIFEST_SCHEMA = "storyagent.package.v1"


class ExportError(RuntimeError):
    """The episode cannot be packaged (no episode yet, or its files are gone)."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _refs(node: Any, found: list[str]) -> None:
    """Every ``asset:<id>`` string in a timeline, in document order, without duplicates."""
    if isinstance(node, str):
        if node.startswith("asset:") and node[6:] not in found:
            found.append(node[6:])
    elif isinstance(node, dict):
        for value in node.values():
            _refs(value, found)
    elif isinstance(node, list):
        for value in node:
            _refs(value, found)


def find_episode(ledger: Ledger, project_id: int, episode: int) -> Any:
    slot = f"episode_{episode:02d}"
    for a in ledger.list_assets(project_id=project_id, kind=MediaKind.COMPOSE_ASSEMBLE.value, current_only=True):
        if a.slot == slot:
            return a
    raise ExportError(f"episode {episode} has not been assembled yet")


def build_package(settings: Settings, ledger: Ledger, project_id: int, episode: int, dest: Path,
                  profile: dict[str, Any]) -> dict[str, Any]:
    """Write the package into ``dest`` (created if needed) and return the manifest."""
    main = find_episode(ledger, project_id, episode)
    film = settings.home / main.path
    if not film.is_file():
        raise ExportError("the episode file is missing from the data home")
    job = ledger.get_job(main.job_id) if main.job_id else None
    timeline = (job.inputs.get("timeline") if job else None) or {}
    dest.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {"episode.mp4": film}
    for a in ledger.list_assets(project_id=project_id, kind=MediaKind.COMPOSE_ASSEMBLE.value, slot="srt"):
        if a.job_id == main.job_id and (settings.home / a.path).is_file():
            files["episode.srt"] = settings.home / a.path
            break

    sources: list[dict[str, Any]] = []
    ids: list[str] = []
    _refs(timeline, ids)
    music_n = speech_n = 0
    stems: dict[str, Path] = {}
    for asset_id in ids:
        try:
            a = ledger.get_asset(asset_id)
        except LedgerError:
            continue
        path = settings.home / a.path
        if a.kind == MediaKind.AUDIO_MUSIC.value and path.is_file():
            music_n += 1
            stems[f"stems/music_{music_n:02d}{path.suffix}"] = path
        elif a.kind == MediaKind.AUDIO_TTS.value and path.is_file():
            speech_n += 1
            stems[f"stems/speech_{speech_n:03d}{path.suffix}"] = path
        sources.append({"asset": a.id, "kind": a.kind, "version": a.version, "sha256": a.sha256,
                        "provenance": redact_for_export(a.provenance) if a.provenance else {}})
    files.update(stems)
    for name, src in files.items():
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)

    cfg = settings.compliance_for()
    qc = run_qc(settings, film, profile, timeline.get("segments", []))
    provenance = {
        "schema": "storyagent.package.provenance.v1", "aigc": True,
        "generator": {"app": "storyagent", "version": __version__}, "created_at": now_iso(),
        "compliance": {"enabled": cfg.enabled, "explicit_label": cfg.explicit_label,
                       "implicit_metadata": cfg.implicit_metadata},
        "episode": {"asset": main.id, "sha256": main.sha256, "provenance": redact_for_export(main.provenance)
                    if main.provenance else {}},
        "sources": sources,
        "attribution": sorted({s["provenance"]["model"]["attribution"] for s in sources
                               if s["provenance"].get("model", {}).get("attribution")}),
    }
    (dest / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), "utf-8")
    manifest = {
        "schema": MANIFEST_SCHEMA, "created_at": provenance["created_at"], "project_id": project_id,
        "episode": episode, "duration_sec": main.duration_sec, "warnings": main.metadata.get("warnings", []),
        "qc": qc,
        "files": [{"name": name, "bytes": (dest / name).stat().st_size, "sha256": _sha256(dest / name)}
                  for name in [*files, "provenance.json"]],
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    return manifest
