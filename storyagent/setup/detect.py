"""Is a component there? Every check answers ``ok`` / ``missing`` / ``unknown`` and says what it looked for.

``unknown`` is only for what cannot be seen without a running service (the files ComfyUI offers): it is neither a
failure nor a pass. Nothing here changes the machine.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

import storyagent
from storyagent.config import Settings
from storyagent.config.setup import SetupCheck, SetupComponent

from .layout import Layout, SetupError
from .probe import Probe

OK, MISSING, UNKNOWN = "ok", "missing", "unknown"


@dataclass(frozen=True)
class CheckResult:
    label: str
    state: str          # ok | missing | unknown
    detail: str = ""


@dataclass
class Detector:
    settings: Settings
    layout: Layout
    probe: Probe

    # -- helpers -----------------------------------------------------------------------------------
    def service_up(self, name: str) -> bool:
        svc = self.settings.services.get(name)
        if svc is None or not svc.enabled:
            return False
        try:
            return bool(self.probe.http_get(svc.health_url, timeout=3).status_code < 400)
        except requests.RequestException:
            return False

    def ffmpeg(self) -> Path | None:
        """The ffmpeg the system would use (configured, then Homebrew's ffmpeg-full, then PATH)."""
        configured = self.layout.path("tool:ffmpeg.binary")
        if configured is not None and configured.is_file():
            return configured
        prefix = self.probe.run(["brew", "--prefix", "ffmpeg-full"])
        if prefix and prefix.returncode == 0 and prefix.stdout.strip():
            cand = Path(prefix.stdout.strip()) / "bin" / "ffmpeg"
            if cand.is_file():
                return cand
        return self.probe.which("ffmpeg")

    def ffprobe_beside(self, ffmpeg: Path) -> Path | None:
        cand = ffmpeg.with_name("ffprobe")
        return cand if cand.is_file() else self.probe.which("ffprobe")

    def hf_hub(self) -> Path:
        env = self.probe.environ
        if env.get("HF_HUB_CACHE"):
            return Path(env["HF_HUB_CACHE"]).expanduser()
        if env.get("HF_HOME"):
            return Path(env["HF_HOME"]).expanduser() / "hub"
        return Path("~/.cache/huggingface/hub").expanduser()

    def llm_models(self) -> list[str]:
        """The models the LLM roles name on an Ollama backend (each once, in role order)."""
        out: list[str] = []
        for role in self.settings.llm_roles.values():
            backend = self.settings.llm_backends.get(role.backend)
            if backend and backend.kind == "ollama" and role.model not in out:
                out.append(role.model)
        return out

    # -- the checks --------------------------------------------------------------------------------
    def run(self, check: SetupCheck) -> list[CheckResult]:
        fn = getattr(self, f"_check_{check.kind}")
        results: list[CheckResult] = fn(check.args)
        return results

    def component(self, comp: SetupComponent) -> list[CheckResult]:
        out: list[CheckResult] = []
        for check in comp.detect:
            out.extend(self.run(check))
        return out

    def _check_command(self, a: dict[str, Any]) -> list[CheckResult]:
        found = self.probe.which(str(a["name"]))
        return [CheckResult(str(a["name"]), OK, str(found)) if found else CheckResult(str(a["name"]), MISSING)]

    def _check_run(self, a: dict[str, Any]) -> list[CheckResult]:
        label = str(a.get("label") or a["argv"][0])
        res = self.probe.run([str(x) for x in a["argv"]])
        if res is None or res.returncode != 0:
            return [CheckResult(label, MISSING)]
        needle = str(a.get("contains", ""))
        if needle and needle not in res.stdout:
            return [CheckResult(label, MISSING)]
        return [CheckResult(label, OK, res.stdout.strip().splitlines()[0] if res.stdout.strip() else "")]

    def _check_path(self, a: dict[str, Any]) -> list[CheckResult]:
        ref = str(a["ref"])
        base = self.layout.path(ref)
        sub = self.layout.expand(str(a.get("sub", "")))
        label = str(a.get("label") or sub or (base.name if base else ref))
        if base is None:
            return [CheckResult(label, MISSING, ref)]
        p = base / sub if sub else base
        want = str(a.get("is", "any"))
        if want == "file":
            ok = p.is_file()
        elif want == "dir":
            ok = p.is_dir()
        elif want == "exec":
            ok = p.is_file() and os.access(p, os.X_OK)
        else:
            ok = p.exists()
        return [CheckResult(label, OK if ok else MISSING, str(p))]

    def _check_app(self, a: dict[str, Any]) -> list[CheckResult]:
        names = [str(n) for n in a["names"]]
        for d in self.settings.setup.machine.app_dirs:
            for n in names:
                p = Path(d).expanduser() / n
                if p.is_dir():
                    return [CheckResult(names[0], OK, str(p))]
        # a standalone/other install is fine too when its service already answers
        return [CheckResult(names[0], MISSING)]

    def _check_ffmpeg_filter(self, a: dict[str, Any]) -> list[CheckResult]:
        ff = self.ffmpeg()
        if ff is None:
            return [CheckResult("ffmpeg", MISSING)]
        res = self.probe.run([str(ff), "-hide_banner", "-filters"])
        text = res.stdout if res else ""
        return [CheckResult(f"ffmpeg {n}", OK if f" {n} " in text else MISSING, str(ff)) for n in a["names"]]

    def _manifest(self, model: str) -> Path:
        root = Path(self.probe.environ.get("OLLAMA_MODELS") or "~/.ollama/models").expanduser()
        name, _, tag = model.partition(":")
        parts = name.split("/") if "/" in name else ["library", name]
        return root / "manifests" / "registry.ollama.ai" / Path(*parts) / (tag or "latest")

    def _check_ollama_models(self, _a: dict[str, Any]) -> list[CheckResult]:
        models = self.llm_models()
        known: set[str] | None = None
        if self.service_up("ollama"):
            try:
                tags = self.probe.http_get(self.settings.service("ollama").url + "/api/tags", timeout=5).json()
                known = {str(m.get("name", "")) for m in tags.get("models", [])}
            except (requests.RequestException, ValueError):
                known = None
        out: list[CheckResult] = []
        for m in models:
            if known is not None:
                have = m in known or (":" not in m and f"{m}:latest" in known)
            else:
                have = self._manifest(m).is_file()
            out.append(CheckResult(m, OK if have else MISSING))
        return out or [CheckResult("llm", MISSING)]

    def _check_comfy_files(self, a: dict[str, Any]) -> list[CheckResult]:
        files = list(a["files"])
        if not self.service_up("comfy"):
            return [CheckResult(str(f["name"]), UNKNOWN) for f in files]
        url = self.settings.service("comfy").url
        cache: dict[str, set[str] | None] = {}
        out: list[CheckResult] = []
        for f in files:
            cls = str(f["class"])
            if cls not in cache:
                try:
                    info = self.probe.http_get(f"{url}/object_info/{cls}", timeout=10).json()
                    entry = info[cls]["input"]["required"][str(f["input"])][0]
                    cache[cls] = {str(x) for x in entry}
                except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
                    cache[cls] = None
            names = cache[cls]
            if names is None:
                out.append(CheckResult(str(f["name"]), UNKNOWN))
            else:
                out.append(CheckResult(str(f["name"]), OK if str(f["name"]) in names else MISSING))
        return out

    def _check_hf_cache(self, a: dict[str, Any]) -> list[CheckResult]:
        hub = self.hf_hub()
        out: list[CheckResult] = []
        for repo in a["repos"]:
            d = hub / ("models--" + str(repo).replace("/", "--")) / "snapshots"
            ok = d.is_dir() and any(d.iterdir())
            out.append(CheckResult(str(repo), OK if ok else MISSING, str(d.parent)))
        return out

    def _check_web_dist(self, _a: dict[str, Any]) -> list[CheckResult]:
        raw = str(self.settings.api.get("web_dir", "")).strip()
        root = Path(raw).expanduser() if raw else Path(storyagent.__file__).resolve().parents[1] / "web" / "dist"
        ok = (root / "index.html").is_file()
        return [CheckResult("web/dist", OK if ok else MISSING, str(root))]

    def _check_database(self, _a: dict[str, Any]) -> list[CheckResult]:
        p = self.settings.db_path
        return [CheckResult(p.name, OK if p.is_file() else MISSING, str(p))]

    def _check_voices(self, a: dict[str, Any]) -> list[CheckResult]:
        need = int(a.get("min", 1))
        count = 0
        p = self.settings.db_path
        if p.is_file():
            try:
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=2)
                try:
                    count = int(con.execute("SELECT COUNT(*) FROM library_items WHERE role='voice'").fetchone()[0])
                finally:
                    con.close()
            except sqlite3.Error:
                count = 0
        return [CheckResult("voice", OK if count >= need else MISSING, str(count))]


def combine(results: list[CheckResult]) -> str:
    """ok (all ok) | missing (nothing there) | partial (some of it) | unverified (nothing missing, some unknown)."""
    states = [r.state for r in results]
    if MISSING in states:
        return "partial" if OK in states or UNKNOWN in states else "missing"
    if UNKNOWN in states:
        return "unverified"
    return "ok"


__all__ = ["CheckResult", "Detector", "SetupError", "combine"]
