"""
Model Manager — ComfyUI 模型发现 + 下载
- 从 ComfyUI object_info 解析已安装模型列表
- 搜索/过滤
- 下载 HuggingFace / 直链 到正确目录
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

COMFYUI_URL = "http://127.0.0.1:8188"
COMFYUI_BASE_DIR = Path(os.path.expanduser("~/Documents/ComfyUI"))

# ComfyUI node → (class_type, input_field)
_NODE_FIELD = {
    "checkpoint":   ("CheckpointLoaderSimple", "ckpt_name"),
    "lora":         ("LoraLoader",             "lora_name"),
    "vae":          ("VAELoader",              "vae_name"),
    "controlnet":   ("ControlNetLoader",       "control_net_name"),
    "upscale":      ("UpscaleModelLoader",     "model_name"),
    "ipadapter":    ("IPAdapterModelLoader",   "ipadapter_file"),
    "clip_vision":  ("CLIPVisionLoader",       "clip_name"),
}

# 各类型在 ComfyUI 目录下的子路径
_MODEL_SUBDIR = {
    "checkpoint":  "models/checkpoints",
    "lora":        "models/loras",
    "vae":         "models/vae",
    "controlnet":  "models/controlnet",
    "upscale":     "models/upscale_models",
    "ipadapter":   "models/ipadapter",
    "clip_vision": "models/clip_vision",
}


# ── object_info 缓存（ComfyUI object_info 缓存）──

_cache: Optional[dict] = None
_cache_time: float = 0.0
_CACHE_TTL = 120  # 2 分钟


def _get_object_info(force: bool = False) -> dict:
    global _cache, _cache_time
    if not force and _cache is not None and (time.time() - _cache_time) < _CACHE_TTL:
        return _cache
    import requests
    try:
        r = requests.get(f"{COMFYUI_URL}/object_info", timeout=10)
        if r.status_code == 200:
            _cache = r.json()
            _cache_time = time.time()
            return _cache
    except Exception:
        pass
    return _cache or {}


def comfyui_online() -> bool:
    import requests
    try:
        return requests.get(f"{COMFYUI_URL}/queue", timeout=4).status_code == 200
    except Exception:
        return False


# ── 模型列表 ──────────────────────────────────────────

def list_models(model_type: str, force_refresh: bool = False) -> list[str]:
    """从 ComfyUI object_info 获取指定类型的已安装模型列表。"""
    info = _get_object_info(force=force_refresh)
    node_class, field = _NODE_FIELD.get(model_type, (None, None))
    if not node_class or node_class not in info:
        return []
    node_def = info[node_class]
    for section in ("required", "optional"):
        entry = node_def.get("input", {}).get(section, {}).get(field)
        if entry and isinstance(entry[0], list):
            return sorted(entry[0])
    return []


def search_models(query: str, model_type: str, force_refresh: bool = False) -> list[str]:
    """对已安装模型列表做大小写不敏感的子串搜索。"""
    models = list_models(model_type, force_refresh=force_refresh)
    if not query.strip():
        return models
    q = query.strip().lower()
    return [m for m in models if q in m.lower()]


def all_installed() -> dict[str, list[str]]:
    """一次性返回所有类型的已安装模型，ComfyUI 离线时返回空。"""
    return {t: list_models(t) for t in _NODE_FIELD}


# ── 本地目录 ──────────────────────────────────────────

def get_model_dir(model_type: str) -> Path:
    subdir = _MODEL_SUBDIR.get(model_type, "models/checkpoints")
    return COMFYUI_BASE_DIR / subdir


def is_installed(filename: str, model_type: str) -> bool:
    """检查文件是否在 ComfyUI 对应目录中。"""
    d = get_model_dir(model_type)
    return (d / filename).exists()


# ── 下载 ─────────────────────────────────────────────

def download_model(
    source: str,
    model_type: str,
    filename: str = "",
    progress_fn: Optional[Callable[[str, float], None]] = None,
) -> tuple[bool, str]:
    """
    下载模型到 ComfyUI 对应目录。
    source 支持：
      - 直链 URL (https://...)
      - HuggingFace 格式 (user/repo/filepath 或 user/repo@filename)
    返回 (success, message)。
    """
    def _prog(msg: str, pct: float = 0.0):
        if progress_fn:
            progress_fn(msg, pct)

    dest_dir = get_model_dir(model_type)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # 解析来源
    url, dest_filename = _resolve_source(source, filename)
    if not url:
        return False, f"无法解析来源: {source}"

    dest_path = dest_dir / dest_filename
    if dest_path.exists():
        return True, f"已存在: {dest_path.name}"

    _prog(f"开始下载 {dest_filename} → {dest_dir.name}/", 0.0)

    try:
        import requests
        with requests.get(url, stream=True, timeout=30,
                          headers={"User-Agent": "story-agent/1.0"}) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1MB
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = downloaded / total
                            mb = downloaded / 1024 / 1024
                            total_mb = total / 1024 / 1024
                            _prog(f"下载中: {mb:.1f}/{total_mb:.1f} MB", pct)
        size_mb = dest_path.stat().st_size / 1024 / 1024
        msg = f"✅ 下载完成: {dest_filename} ({size_mb:.1f} MB)  → 重启 ComfyUI 后生效"
        _prog(msg, 1.0)
        return True, msg
    except Exception as e:
        if dest_path.exists():
            dest_path.unlink()
        msg = f"❌ 下载失败: {e}"
        _prog(msg, 0.0)
        return False, msg


def _resolve_source(source: str, filename: str) -> tuple[str, str]:
    """将用户输入解析为 (download_url, dest_filename)。"""
    source = source.strip()
    if not source:
        return "", ""

    # 直链
    if source.startswith("http://") or source.startswith("https://"):
        fname = filename or Path(source.split("?")[0]).name
        return source, fname

    # HuggingFace 格式 1: "user/repo/path/to/file.safetensors"
    if "/" in source and not source.startswith("hf:"):
        parts = source.split("/")
        # user/repo/...path... → HF URL
        if len(parts) >= 3:
            user, repo = parts[0], parts[1]
            filepath = "/".join(parts[2:])
            fname = filename or parts[-1]
            url = f"https://huggingface.co/{user}/{repo}/resolve/main/{filepath}"
            return url, fname

    # HuggingFace 格式 2: "hf:user/repo@filename"
    if source.startswith("hf:"):
        rest = source[3:]
        if "@" in rest:
            repo, fname = rest.split("@", 1)
            url = f"https://huggingface.co/{repo}/resolve/main/{fname}"
            return url, filename or fname
        fname = filename or rest.split("/")[-1]
        url = f"https://huggingface.co/{rest}/resolve/main/{fname}"
        return url, fname

    return "", ""


# ── 刷新 ComfyUI 模型缓存 ─────────────────────────────

def refresh_comfyui_cache() -> bool:
    """强制刷新 object_info 缓存（下载新模型后需要重启 ComfyUI 才真正生效）。"""
    _get_object_info(force=True)
    return bool(_cache)
