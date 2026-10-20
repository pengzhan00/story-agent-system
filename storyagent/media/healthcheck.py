"""One-screen pipeline health: is each service up, is each tool/model in place, is there disk.

    python -m storyagent.media.healthcheck [--json] [--require a,b]

Exit code: 0 = every required entry is ``ok``; 1 = something required is not; 2 = bad configuration.
Cron-friendly. Read-only: it probes, it never starts, stops or downloads anything.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import requests

from storyagent.config import ConfigError, Settings, get_settings
from storyagent.i18n import set_locale, t

from .asr import find_whisper
from .contracts import HealthState
from .llm_resident import ResidentLLMProbe, build_probes
from .probe import find_tool
from .registry import AdapterRegistry

HttpGet = Callable[..., Any]
Runner = Callable[[Sequence[str]], str]


@dataclass(frozen=True)
class Entry:
    name: str
    category: str  # service | tool | system | llm | adapter
    state: HealthState
    detail: str = ""
    missing: tuple[str, ...] = ()
    required: bool = False

    @property
    def ok(self) -> bool:
        return self.state is HealthState.OK


@dataclass
class Report:
    entries: list[Entry] = field(default_factory=list)

    @property
    def failing_required(self) -> list[Entry]:
        return [e for e in self.entries if e.required and not e.ok]

    @property
    def exit_code(self) -> int:
        return 0 if not self.failing_required else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "entries": [{**asdict(e), "state": e.state.value, "ok": e.ok} for e in self.entries],
        }


def _service(svc: Any, http_get: HttpGet) -> tuple[HealthState, str]:
    if not svc.enabled:
        return HealthState.DISABLED, ""
    try:
        r = http_get(svc.health_url, timeout=3)
    except requests.RequestException as exc:
        return HealthState.NOT_RUNNING, type(exc).__name__
    if r.status_code < 400:
        return (HealthState.PROBE_ONLY if svc.probe_only else HealthState.OK), f"HTTP {r.status_code}"
    return HealthState.DEGRADED, f"HTTP {r.status_code}"


def _exists(p: Path | None, *, executable: bool = False) -> bool:
    if p is None or not p.exists():
        return False
    import os

    return os.access(p, os.X_OK) if executable else True


def _check_tools(settings: Settings, run: Runner) -> list[Entry]:
    tp = settings.tool_path
    out: list[Entry] = []

    def entry(name: str, tool_missing: list[str], model_missing: list[str], detail: str = "") -> None:
        if tool_missing:
            state, miss = HealthState.MISSING_TOOL, tool_missing
        elif model_missing:
            state, miss = HealthState.MISSING_MODEL, model_missing
        else:
            state, miss = HealthState.OK, []
        out.append(Entry(f"tool.{name}", "tool", state, detail, tuple(miss)))

    entry(
        "h3",
        [] if _exists(tp("h3", "binary"), executable=True) else ["h3 binary"],
        [] if _exists((tp("h3", "model_dir") or Path("/nonexistent")) / "model_index.json") else ["MiniMax-H3 weights"],
    )
    lc_root = tp("longcat", "root")
    lc_w, lc_v = tp("longcat", "weights_dir"), settings.tools.get("longcat", {}).get("variant_dir", "")
    entry(
        "longcat",
        [] if _exists(tp("longcat", "python"), executable=True) and _exists(lc_root) else ["longcat runtime"],
        [] if lc_w and lc_v and _exists(lc_w / lc_v) else [f"weights {lc_v or '(variant_dir unset)'}"],
    )
    tts = settings.models.get(settings.default_models.get("audio.tts", ""))
    cosy_model_dir = (
        Path(str(tts.extra["model_dir"])).expanduser()
        if tts and tts.extra.get("model_dir") else tp("cosyvoice", "model_dir")
    )
    entry(
        "cosyvoice",
        [] if _exists(tp("cosyvoice", "python"), executable=True) and _exists(tp("cosyvoice", "script"))
        else ["cosyvoice runtime"],
        [] if _exists(cosy_model_dir) else [cosy_model_dir.name if cosy_model_dir else "CosyVoice model"],
    )
    mflux_bin = tp("mflux", "bin_dir")
    entry(
        "mflux",
        [] if mflux_bin and (mflux_bin / "mflux-generate-flux2").is_file() else ["mflux-generate-flux2"],
        [] if _exists(tp("lora_train", "base_model_dir")) else ["FLUX.2 klein base 4B weights"],
    )
    sv_root = tp("seedvc", "root")
    vc = settings.models.get(settings.default_models.get("audio.convert", ""))
    vc_script = "inference_v2.py" if not vc or vc.extra.get("engine", "v2") == "v2" else "inference.py"
    vc_ckpts = [str(vc.extra[k]) for k in ("ar_checkpoint", "cfm_checkpoint") if vc and vc.extra.get(k)]
    vc_missing = [c for c in vc_ckpts if not (sv_root and (sv_root / c).is_file())]
    entry(
        "seedvc",
        [] if _exists(tp("seedvc", "python"), executable=True) and sv_root and (sv_root / vc_script).is_file()
        else ["Seed-VC runtime"],
        vc_missing if sv_root and (sv_root / "checkpoints").is_dir() else ["Seed-VC checkpoints"],
    )
    wh_model = tp("whisper", "model")
    entry(
        "whisper",
        [] if find_whisper(settings) else ["whisper-cli"],
        [] if wh_model and wh_model.is_file() else ["whisper model"],
    )
    fv_w = tp("fastvideo", "weights_dir")
    entry(
        "fastvideo",
        [] if _exists(tp("fastvideo", "python"), executable=True) and _exists(tp("fastvideo", "root"))
        else ["fastvideo runtime"],
        [] if fv_w and _exists(fv_w) else ["FastH3 weights (not downloaded/converted)"],
    )
    entry(
        "lora_train",
        [] if _exists(tp("lora_train", "command"), executable=True) else ["mflux-train"],
        [] if _exists(tp("lora_train", "base_model_dir")) else ["FLUX.2 klein base model"],
    )

    ff = find_tool(settings, "ffmpeg")
    if ff is None:
        out.append(Entry("tool.ffmpeg", "tool", HealthState.MISSING_TOOL, "", ("ffmpeg",)))
    else:
        try:
            filters = run([str(ff), "-hide_banner", "-filters"])
        except (OSError, subprocess.SubprocessError):
            filters = ""
        has_subs = " subtitles " in filters
        has_whisper = " whisper " in filters
        missing = [f for f, ok in (("libass subtitles filter", has_subs),) if not ok]
        detail = f"{ff}; whisper={'yes' if has_whisper else 'no'}"
        out.append(
            Entry("tool.ffmpeg", "tool", HealthState.OK if has_subs else HealthState.DEGRADED, detail, tuple(missing))
        )
    return out


def run_checks(
    settings: Settings,
    *,
    registry: AdapterRegistry | None = None,
    http_get: HttpGet = requests.get,
    run: Runner | None = None,
    probes: Sequence[ResidentLLMProbe] | None = None,
    require: Sequence[str] = (),
) -> Report:
    runner = run or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False).stdout)
    required = set(settings.healthcheck_required) | set(require)
    report = Report()

    for svc in settings.services.values():
        state, detail = _service(svc, http_get)
        report.entries.append(Entry(svc.name, "service", state, detail or svc.health_url))
    report.entries.extend(_check_tools(settings, runner))

    probe_list = list(probes) if probes is not None else build_probes(settings)
    resident: list[str] = []
    for p in probe_list:
        resident.extend(p.resident())
    report.entries.append(
        Entry("llm.resident", "llm", HealthState.OK, t("health.llm_resident", models=", ".join(resident) or "-"))
    )

    home = settings.home
    probe_dir = next((p for p in (home, *home.parents) if p.exists()), Path("."))
    free_gib = shutil.disk_usage(probe_dir).free / 2**30
    min_free = float(settings.healthcheck_min_free_gib)
    report.entries.append(
        Entry(
            "system.disk", "system",
            HealthState.OK if free_gib >= min_free else HealthState.DEGRADED,
            t("health.disk_free", gib=f"{free_gib:.0f}"),
        )
    )

    if registry is not None:
        for prov in registry.providers():
            adapter = registry.get(prov)
            assert adapter is not None
            h = adapter.health()
            report.entries.append(Entry(f"adapter.{prov}", "adapter", h.state, h.detail, h.missing))

    unknown = sorted(required - {e.name for e in report.entries})
    if unknown:
        raise ConfigError(f"required entries do not exist: {unknown}")
    report.entries = [Entry(**{**asdict(e), "required": e.name in required}) for e in report.entries]
    return report


def format_report(report: Report) -> str:
    rows = [(e.name, t(f"health.{e.state.value}"), e.detail + (f" [{', '.join(e.missing)}]" if e.missing else ""),
             "*" if e.required else "")
            for e in report.entries]
    wn = max(len(r[0]) for r in rows)
    ws = max(len(r[1]) for r in rows)
    head = f"{t('health.col.name'):<{wn + 2}}{t('health.col.state'):<{ws + 2}}{t('health.col.detail')}"
    lines = [t("health.title"), head]
    lines += [f"{r[0]:<{wn}}{r[3]:>2}{r[1]:<{ws + 2}}{r[2]}" for r in rows]
    bad = report.failing_required
    lines.append(
        t("health.exit_ok") if not bad else t("health.exit_bad", names=", ".join(e.name for e in bad))
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="storyagent.media.healthcheck")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--require", default="", help="comma-separated extra entries that must be ok")
    args = ap.parse_args(argv)
    try:
        settings = get_settings()
        set_locale(settings.locale)
        report = run_checks(settings, require=[r for r in args.require.split(",") if r])
    except ConfigError as exc:
        print(t("error.config.invalid", detail=str(exc)), file=sys.stderr)
        return 2
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) if args.json else format_report(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
