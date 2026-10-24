"""The configuration this machine needs, as changes to the operator's own ``$STORY_AGENT_HOME/config/*.toml``.

Only what differs from the configuration already in effect is written: tool paths that a chosen install root moves, the
ffmpeg that has libass, the mflux folder, the language model, the healthcheck's required list, the memory budget and
pipeline plans trimmed to what is installed (a plan without video shows no video clips). Previewing writes nothing;
writing keeps a copy of every file it replaces.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from storyagent.config import ConfigError, Settings, load_settings

from .detect import Detector
from .layout import Layout, SetupError, merge_into, nested, tilde
from .probe import Probe
from .report import Report
from .tomlout import dumps

# a pipeline plan's `requires` entries that stand for video; without it the plan makes no clips
VIDEO_ENTRY = "phosphene"


@dataclass(frozen=True)
class ConfigFile:
    name: str                       # services.toml
    path: Path
    exists: bool
    changed: bool
    changes: tuple[str, ...]        # "tools.ffmpeg.binary = ..." lines
    text: str                       # the file as it would be written
    previous: str = ""


def _flatten(prefix: str, node: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for k, v in node.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, Mapping):
            out.extend(_flatten(key, v))
        else:
            out.append(f"{key} = {v!r}")
    return out


def _libass_ffmpeg(det: Detector) -> Path | None:
    cands: list[Path] = []
    configured = det.layout.path("tool:ffmpeg.binary")
    if configured is not None:
        cands.append(configured)
    brew = det.probe.run(["brew", "--prefix", "ffmpeg-full"])
    if brew and brew.returncode == 0 and brew.stdout.strip():
        cands.append(Path(brew.stdout.strip()) / "bin" / "ffmpeg")
    on_path = det.probe.which("ffmpeg")
    if on_path:
        cands.append(on_path)
    for c in cands:
        if not c.is_file():
            continue
        res = det.probe.run([str(c), "-hide_banner", "-filters"])
        if res and " subtitles " in res.stdout:
            return c
    return None


def _mflux_bin(det: Detector) -> Path | None:
    configured = det.layout.path("tool:mflux.bin_dir")
    if configured is not None and (configured / "mflux-generate-flux2").is_file():
        return configured
    found = det.probe.which("mflux-generate-flux2")
    if found:
        return found.parent
    res = det.probe.run(["uv", "tool", "dir", "--bin"])
    if res and res.returncode == 0 and res.stdout.strip():
        d = Path(res.stdout.strip())
        if (d / "mflux-generate-flux2").is_file():
            return d
    return None


def compute(settings: Settings, report: Report, *, probe: Probe, root: Path | None, extra: tuple[str, ...] = (),
            llm_model: str = "") -> dict[str, dict[str, Any]]:
    """``{file: nested changes}`` for services, models, llm and pipelines."""
    del extra
    layout = Layout(settings, root)
    det = Detector(settings, layout, probe)
    cfg = settings.setup
    scope = [cfg.components[c.name] for c in report.components if c.need != "optional"]
    changes: dict[str, dict[str, Any]] = {}

    merge_into(changes, nested(layout.overrides(scope)))

    if any(c.name == "ffmpeg_full" for c in scope):
        ff = _libass_ffmpeg(det)
        configured = layout.path("tool:ffmpeg.binary")
        if ff is not None and ff != configured:
            tools = changes.setdefault("services", {}).setdefault("tools", {}).setdefault("ffmpeg", {})
            tools["binary"] = tilde(ff)
            probe_bin = det.ffprobe_beside(ff)
            if probe_bin is not None:
                tools["ffprobe"] = tilde(probe_bin)

    if any(c.name == "mflux" for c in scope):
        bin_dir = _mflux_bin(det)
        if bin_dir is not None and bin_dir != layout.path("tool:mflux.bin_dir"):
            t = changes.setdefault("services", {}).setdefault("tools", {})
            t.setdefault("mflux", {})["bin_dir"] = tilde(bin_dir)
            t.setdefault("lora_train", {})["command"] = tilde(bin_dir / "mflux-train")

    if report.health_required and set(report.health_required) != set(settings.healthcheck_required):
        changes.setdefault("services", {}).setdefault("healthcheck", {})["required"] = list(report.health_required)

    mem = probe.machine().memory_gib
    if mem > 0:
        budget = int(mem * cfg.machine.memory_budget_share)
        if budget != int(settings.runtime.get("memory_budget_gib", 0)):
            changes.setdefault("services", {}).setdefault("runtime", {})["memory_budget_gib"] = budget

    if llm_model:
        roles = {r: {"model": llm_model} for r, spec in settings.llm_roles.items() if spec.model != llm_model}
        if roles:
            changes.setdefault("llm", {}).setdefault("llm", {})["roles"] = roles

    provided = {h for c in scope for h in c.health}
    known = {h for c in cfg.components.values() for h in c.health}
    for name, plan in settings.pipelines.items():
        kept = [r for r in plan.requires if r in provided or r not in known]
        if tuple(kept) != plan.requires:
            node = changes.setdefault("pipelines", {}).setdefault("pipelines", {}).setdefault(name, {})
            node["requires"] = kept
            if VIDEO_ENTRY in plan.requires and VIDEO_ENTRY not in kept:
                node["workflow"] = {"video_share": 0.0, "video_min_per_episode": 0}
    return changes


def preview(settings: Settings, report: Report, *, probe: Probe, root: Path | None, extra: tuple[str, ...] = (),
            llm_model: str = "") -> list[ConfigFile]:
    delta = compute(settings, report, probe=probe, root=root, extra=extra, llm_model=llm_model)
    files: list[ConfigFile] = []
    for name, node in delta.items():
        path = settings.config_dir / f"{name}.toml"
        previous = path.read_text("utf-8") if path.is_file() else ""
        current = tomllib.loads(previous) if previous else {}
        merged = merge_into(current, node)
        text = dumps(merged)
        if not previous:
            text = (f"# Written by `storyagent setup` for the profile {report.profile!r} on "
                    f"{datetime.now().strftime('%Y-%m-%d')}. Edit freely; run it again to re-detect.\n\n") + text
        files.append(ConfigFile(name=f"{name}.toml", path=path, exists=bool(previous),
                                changed=_semantic(text) != _semantic(previous), changes=tuple(_flatten("", node)),
                                text=text, previous=previous))
    return files


def _semantic(text: str) -> dict[str, Any]:
    return tomllib.loads(text) if text.strip() else {}


def validate(settings: Settings, files: list[ConfigFile]) -> None:
    """Load the configuration as it would be after writing ``files`` (in a scratch copy); ``SetupError`` if it is
    refused. Nothing in the real configuration folder is touched."""
    with tempfile.TemporaryDirectory(prefix="storyagent-setup-") as tmp:
        scratch = Path(tmp)
        if settings.config_dir.is_dir():
            for existing in settings.config_dir.glob("*.toml"):
                shutil.copy2(existing, scratch / existing.name)
        for f in files:
            if f.changed:
                (scratch / f.name).write_text(f.text, "utf-8")
        try:
            load_settings({"STORY_AGENT_HOME": str(settings.home), "STORY_AGENT_CONFIG_DIR": str(scratch)})
        except ConfigError as exc:
            raise SetupError("config_invalid", str(exc)[:300]) from exc


def write(files: list[ConfigFile]) -> list[tuple[Path, Path | None]]:
    """Write every changed file; one it replaces is kept as ``<name>.bak-setup-<time>``. Returns (written, backup)."""
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    done: list[tuple[Path, Path | None]] = []
    for f in files:
        if not f.changed:
            continue
        f.path.parent.mkdir(parents=True, exist_ok=True)
        backup: Path | None = None
        if f.exists:
            backup = f.path.with_name(f"{f.path.name}.bak-setup-{stamp}")
            backup.write_text(f.previous, "utf-8")
        tmp = f.path.with_name(f.path.name + ".tmp")
        tmp.write_text(f.text, "utf-8")
        os.replace(tmp, f.path)
        done.append((f.path, backup))
    return done
