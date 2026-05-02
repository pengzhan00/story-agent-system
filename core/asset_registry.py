"""
Asset Registry — 统一资产查询与复用层

所有管线在生成前先问这里：
  "这个 shot 的视频已经有了吗？"
  "这个角色的 LoRA 在哪？"
  "这段 TTS 已经生成过了吗？"

设计原则：
  - 只读查询，不写 DB（写操作留给各管线）
  - DB 是权威来源，磁盘文件存在是必要条件
  - 跨 shot 的同一资产（LoRA、BGM）只解析一次后缓存到内存
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
import sys

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import core.database as db

OUTPUT_DIR = PROJECT_ROOT / "output"


# ── Shot 视频 ─────────────────────────────────────────────

def get_shot_video(project_id: int, shot_id: int, project_name: str = "") -> Optional[str]:
    """
    查找 shot 的已渲染视频路径。
    优先从 render_jobs 精确匹配，回退到 composed/scenes 目录 glob。
    """
    # 1. render_jobs 精确匹配
    jobs = db.list_render_jobs(project_id=project_id, shot_id=shot_id)
    for job in jobs:
        vp = job.get("output_path", "") if isinstance(job, dict) else getattr(job, "output_path", "")
        status = job.get("status", "") if isinstance(job, dict) else getattr(job, "status", "")
        if status == "completed" and vp and Path(vp).exists():
            return vp

    # 2. 目录 glob 回退（project_name 可选）
    if project_name:
        proj_dir = OUTPUT_DIR / "projects" / project_name
        for subdir in ("composed", "scenes"):
            for p in (proj_dir / subdir).glob(f"*{shot_id}*"):
                if p.suffix in (".mp4", ".webm", ".avi"):
                    return str(p)
        # composed 精确命名
        composed = proj_dir / "composed" / f"shot_{shot_id:04d}_composed.mp4"
        if composed.exists():
            return str(composed)

    return None


def is_shot_rendered(shot_id: int) -> bool:
    """shot.status 是否已完成渲染（不校验磁盘，快速判断）。"""
    shot = db.get_shot(shot_id)
    return shot is not None and shot.status in ("rendered", "approved")


def is_shot_video_on_disk(project_id: int, shot_id: int, project_name: str = "") -> bool:
    """shot 视频是否确实存在于磁盘（慢，需要 IO）。"""
    return get_shot_video(project_id, shot_id, project_name) is not None


# ── TTS 音频 ──────────────────────────────────────────────

def get_shot_tts(project_id: int, shot_id: int) -> list[dict]:
    """
    返回该 shot 已生成的 TTS 音频列表（仅返回文件存在的条目）。
    格式: [{"file": str, "line_idx": int, "duration": float, "character": str}]
    """
    assets = db.list_audio_assets(project_id, shot_id=shot_id)
    result = []
    for a in assets:
        if isinstance(a, dict):
            asset_type = a.get("asset_type", "")
            fp = a.get("file_path", "")
            meta_raw = a.get("metadata") or "{}"
        else:
            asset_type = getattr(a, "asset_type", "")
            fp = getattr(a, "file_path", "")
            meta_raw = getattr(a, "metadata", "{}") or "{}"

        if asset_type != "tts" or not fp or not Path(fp).exists():
            continue

        try:
            meta = json.loads(meta_raw)
        except Exception:
            meta = {}

        result.append({
            "file": fp,
            "line_idx": meta.get("line_idx", 0),
            "duration": a.get("duration_sec", 0.0) if isinstance(a, dict) else getattr(a, "duration_sec", 0.0),
            "character": meta.get("character", ""),
        })

    return sorted(result, key=lambda x: x["line_idx"])


def is_shot_tts_complete(project_id: int, shot_id: int) -> bool:
    """
    判断 shot 的 TTS 是否已全部生成。
    条件：dialogue 行数 > 0 且 audio_assets 中 tts 记录数 >= dialogue 行数。
    """
    shot = db.get_shot(shot_id)
    if not shot:
        return False

    try:
        dialogue = json.loads(shot.dialogue) if shot.dialogue else []
    except Exception:
        dialogue = []

    # 无对白的 shot 视为已完成
    non_empty = [l for l in dialogue if isinstance(l, dict) and l.get("line", "").strip()]
    if not non_empty:
        return True

    existing = get_shot_tts(project_id, shot_id)
    return len(existing) >= len(non_empty)


# ── 合成视频 ──────────────────────────────────────────────

def get_composed_shot(project_name: str, shot_id: int) -> Optional[str]:
    """返回已合成的 shot 视频路径（带字幕/音频的最终版本）。"""
    p = OUTPUT_DIR / "projects" / project_name / "composed" / f"shot_{shot_id:04d}_composed.mp4"
    return str(p) if p.exists() else None


def is_shot_composed(project_name: str, shot_id: int) -> bool:
    return get_composed_shot(project_name, shot_id) is not None


# ── 音乐 / 音效 ────────────────────────────────────────────

def get_project_bgm(project_id: int) -> Optional[str]:
    """返回项目 BGM 文件路径（取第一个已生成的 bgm/theme）。"""
    music_list = db.list_music(project_id)
    for m in music_list:
        fp = m.file_path if hasattr(m, "file_path") else m.get("file_path", "")
        mtype = m.type if hasattr(m, "type") else m.get("type", "")
        if mtype in ("bgm", "theme") and fp and Path(fp).exists():
            return fp
    return None


def get_shot_bgm(project_id: int, shot_id: int) -> Optional[str]:
    assets = db.list_audio_assets(project_id, shot_id=shot_id)
    for asset in assets:
        fp = asset.get("file_path", "")
        if asset.get("asset_type") == "bgm_shot" and fp and Path(fp).exists():
            return fp
    return None


def get_shot_sfx(project_id: int, shot_id: int) -> list[dict]:
    assets = db.list_audio_assets(project_id, shot_id=shot_id)
    result = []
    for asset in assets:
        fp = asset.get("file_path", "")
        if asset.get("asset_type") == "sfx_shot" and fp and Path(fp).exists():
            result.append(asset)
    return result


def get_project_sfx(project_id: int) -> list[dict]:
    """返回已生成的音效文件列表。"""
    sfx_list = db.list_sfx(project_id)
    result = []
    for s in sfx_list:
        fp = s.file_path if hasattr(s, "file_path") else s.get("file_path", "")
        if fp and Path(fp).exists():
            sid = s.id if hasattr(s, "id") else s.get("id", 0)
            name = s.name if hasattr(s, "name") else s.get("name", "")
            result.append({"id": sid, "name": name, "file": fp})
    return result


# ── LoRA 查询 ──────────────────────────────────────────────

def get_char_loras(project_id: int, char_names: list[str]) -> list[dict]:
    """
    根据角色名列表，返回需要注入的 LoRA 配置。
    [{"name": "char_lora.safetensors", "strength": 0.8, "type": "character"}]
    """
    chars = db.list_characters(project_id)
    char_map = {c.name: c for c in chars}
    loras = []
    seen = set()
    for name in char_names:
        char = char_map.get(name)
        if char and char.lora_ref and char.lora_ref not in seen:
            seen.add(char.lora_ref)
            loras.append({"name": char.lora_ref, "strength": 0.8, "type": "character"})
    return loras


def get_scene_lora(project_id: int, scene_name: str) -> Optional[dict]:
    """根据场景名返回场景 LoRA 配置（如有）。"""
    scenes = db.list_scene_assets(project_id)
    scene = next((s for s in scenes if s.name == scene_name), None)
    if scene and scene.lora_ref:
        return {"name": scene.lora_ref, "strength": 0.6, "type": "scene"}
    return None


def get_shot_loras(project_id: int, shot) -> list[dict]:
    """
    为一个 shot 汇总所有需要注入的 LoRA（角色 + 场景）。
    shot 可以是 Shot dataclass 或 dict。
    """
    if hasattr(shot, "characters"):
        try:
            char_names = json.loads(shot.characters) if shot.characters else []
        except Exception:
            char_names = []
        location = shot.location
    else:
        try:
            char_names = json.loads(shot.get("characters", "[]"))
        except Exception:
            char_names = []
        location = shot.get("location", "")

    loras = get_char_loras(project_id, char_names)
    scene_lora = get_scene_lora(project_id, location)
    if scene_lora:
        loras.append(scene_lora)
    return loras


# ── 项目整体状态快照 ──────────────────────────────────────

def project_snapshot(project_id: int, project_name: str = "") -> dict:
    """
    返回整个项目的资产完成状态快照（用于 UI 显示和 resume 决策）。
    """
    if not project_name:
        proj = db.get_project(project_id)
        project_name = proj.name if proj else str(project_id)

    shots = db.list_shots(project_id=project_id)
    shot_states = []
    for shot in shots:
        shot_states.append({
            "id": shot.id,
            "label": f"A{shot.act_number}S{shot.scene_number}#{shot.shot_number}",
            "render_done": is_shot_rendered(shot.id) and is_shot_video_on_disk(project_id, shot.id, project_name),
            "tts_done": is_shot_tts_complete(project_id, shot.id),
            "compose_done": is_shot_composed(project_name, shot.id),
        })

    rendered = sum(1 for s in shot_states if s["render_done"])
    tts_done = sum(1 for s in shot_states if s["tts_done"])
    composed = sum(1 for s in shot_states if s["compose_done"])
    total = len(shot_states)

    return {
        "project_id": project_id,
        "project_name": project_name,
        "total_shots": total,
        "rendered": rendered,
        "tts_done": tts_done,
        "composed": composed,
        "bgm_ready": get_project_bgm(project_id) is not None,
        "shots": shot_states,
    }
