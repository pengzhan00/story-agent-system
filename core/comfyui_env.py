from __future__ import annotations

import os
from pathlib import Path


COMFYUI_DIR = Path(os.path.expanduser(os.getenv("COMFYUI_DIR", "~/Documents/ComfyUI")))


def resolve_comfyui_python() -> Path:
    """Return the ComfyUI-managed Python interpreter and avoid system fallback."""
    override = os.getenv("COMFYUI_PYTHON", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(os.path.expanduser(override)))
    candidates.extend([
        COMFYUI_DIR / ".venv" / "bin" / "python3",
        COMFYUI_DIR / ".venv" / "bin" / "python",
        COMFYUI_DIR / "venv" / "bin" / "python3",
        COMFYUI_DIR / "venv" / "bin" / "python",
        COMFYUI_DIR / "python_embeded" / "python.exe",
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def comfyui_main_py() -> Path:
    return COMFYUI_DIR / "main.py"
