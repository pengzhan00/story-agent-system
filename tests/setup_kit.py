"""A made-up machine for the install guide: an empty home, a PATH with a few stub programs, canned command output and
service answers. Nothing here touches the real machine's tools, services or home folder."""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import requests

from storyagent.config import Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.setup.probe import MachineInfo, Probe, RunResult

SUBTITLES = " .. subtitles  V->V\n .. whisper  A->A\n"


class _Resp:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self.status_code, self._payload = status, payload

    def json(self) -> Any:
        return self._payload


class FakeMachine:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, memory_gib: float = 128.0) -> None:
        self.root = tmp_path
        self.home = tmp_path / "home"
        self.bin = tmp_path / "bin"
        self.apps = tmp_path / "Applications"
        self.data = tmp_path / "data"
        for d in (self.home, self.bin, self.apps, self.data / "config"):
            d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("STORY_AGENT_HOME", str(self.data))
        monkeypatch.setenv("STORY_AGENT_LOCALE", "en")
        self.ffmpeg = tmp_path / "ffmpeg-full" / "bin" / "ffmpeg"
        (self.data / "config" / "setup.toml").write_text(f'[machine]\napp_dirs = ["{self.apps}"]\n')
        (self.data / "config" / "services.toml").write_text(
            f'[tools.ffmpeg]\nbinary = "{self.ffmpeg}"\nffprobe = ""\n[api]\nweb_dir = "{tmp_path / "webdist"}"\n')
        self.memory_gib = memory_gib
        self.xcode = False
        self.brew_prefix = ""                         # `brew --prefix ffmpeg-full` answer
        self.filters = SUBTITLES                      # `ffmpeg -filters` answer (for any ffmpeg)
        self.services: dict[str, Any] = {}            # url fragment -> JSON answer; absent = connection refused
        self.runs: list[list[str]] = []

    # -- the pieces the guide asks about --------------------------------------------------------------
    def settings(self) -> Settings:
        return load_settings({"STORY_AGENT_HOME": str(self.data)})

    def probe(self) -> Probe:
        return Probe(path=str(self.bin), environ={"PATH": str(self.bin)}, run=self._run,
                     machine=lambda: MachineInfo("arm64", "Darwin", "15.1", self.memory_gib),
                     http_get=self._get, free_gib=lambda _p: 500.0)

    def _run(self, argv: Sequence[str]) -> RunResult | None:
        self.runs.append(list(argv))
        head = Path(argv[0]).name
        if head == "xcode-select":
            return RunResult(0 if self.xcode else 2, "/Library/Developer/CommandLineTools\n" if self.xcode else "")
        if head == "brew" and list(argv[1:]) == ["--prefix", "ffmpeg-full"]:
            return RunResult(0, self.brew_prefix + "\n") if self.brew_prefix else RunResult(1, "")
        if head in ("ffmpeg", "ffmpeg-full") and "-filters" in argv:
            return RunResult(0, self.filters)
        if head == "uv" and list(argv[1:]) == ["tool", "dir", "--bin"]:
            return RunResult(0, f"{self.home}/.local/bin\n")
        return None

    def _get(self, url: str, **_kw: Any) -> _Resp:
        for fragment, payload in self.services.items():
            if fragment in url:
                return _Resp(payload(url) if callable(payload) else payload)
        raise requests.ConnectionError("refused")

    # -- installing things into the made-up machine -------------------------------------------------
    def exe(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return path

    def touch(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return path

    def program(self, name: str) -> None:
        self.exe(self.bin / name)

    def install_base(self) -> None:
        for name in ("brew", "uv", "hf", "ollama", "whisper-cli"):
            self.program(name)
        self.xcode = True
        self.exe(self.ffmpeg)
        self.touch(self.root / "webdist" / "index.html")
        db = Database(self.settings().db_path)
        migrate(db)
        db.close()

    def add_voice(self) -> None:
        con = sqlite3.connect(self.settings().db_path)
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("INSERT INTO library_items (id, role, name, asset_id) VALUES ('v1', 'voice', 'anna', 'a1')")
        con.commit()
        con.close()

    def install_llm(self) -> None:
        self.touch(self.home / ".ollama/models/manifests/registry.ollama.ai/library/gemma4/31b")

    def install_image(self) -> None:
        for n in ("mflux-generate-flux2", "mflux-generate-flux2-edit", "mflux-train"):
            self.exe(self.home / ".local/bin" / n)
        self.touch(self.home / "models/flux2-klein-base-4B/model_index.json")
        (self.home / "models/flux2-klein-base-4B/transformer").mkdir(parents=True)

    def install_voice(self) -> None:
        self.exe(self.home / "cosyvoice/venv/bin/python")
        self.touch(self.home / "cosyvoice/CosyVoice/cosyvoice/cli/cosyvoice.py")
        self.touch(self.home / "models/cosyvoice3/Fun-CosyVoice3-0.5B/cosyvoice3.yaml")
        self.touch(self.home / "models/cosyvoice3/Fun-CosyVoice3-0.5B/llm.pt")
        self.add_voice()

    def install_asr(self) -> None:
        self.touch(self.home / "myworkspace/models/whisper/ggml-large-v3-turbo.bin")

    def install_music(self) -> None:
        base = self.home / "ACE-Step-1.5"
        self.exe(base / ".venv/bin/python3")
        (base / "acestep").mkdir(parents=True)
        for d in ("acestep-v15-turbo", "acestep-5Hz-lm-1.7B", "vae", "Qwen3-Embedding-0.6B"):
            (base / "checkpoints" / d).mkdir(parents=True)

    def install_video(self) -> None:
        (self.apps / "Pinokio.app").mkdir(parents=True)
        base = self.home / "pinokio/api/phosphene"
        self.touch(base / "mlx_ltx_panel.py")
        (base / "mlx_models/ltx-2.5-mlx-q4").mkdir(parents=True)

    def install_comfy(self, files: dict[str, list[str]] | None = None) -> None:
        """ComfyUI app + a running server that lists ``{node class: [file names]}``."""
        (self.apps / "Comfy Desktop.app").mkdir(parents=True, exist_ok=True)
        listing = files or {}
        inputs = {"CheckpointLoaderSimple": "ckpt_name", "CLIPLoader": "clip_name", "VAELoader": "vae_name"}

        def answer(url: str) -> Any:
            cls = url.rsplit("/", 1)[-1]
            return {cls: {"input": {"required": {inputs[cls]: [listing.get(cls, [])]}}}}

        self.services["8188/object_info"] = answer
        self.services["8188/system_stats"] = {}

    def install_everything_for_full(self) -> None:
        self.install_base()
        self.install_llm()
        self.install_image()
        self.install_voice()
        self.install_asr()
        self.install_music()
        self.install_video()
        self.install_comfy({"CheckpointLoaderSimple": ["stable_audio_3_medium.safetensors"],
                            "CLIPLoader": ["t5gemma_b_b_ul2.safetensors"]})
