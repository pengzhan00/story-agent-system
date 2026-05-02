#!/usr/bin/env python3
"""
漫剧故事工坊 — 两步走 UI
Phase 1: 一键生成全部内容（不渲染）→ 可读查看 + JSON 编辑
Phase 2: 渲染 + 导出（用编辑后的数据）
"""
import sys, os, json, re, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import gradio as gr

from core.comfyui_env import COMFYUI_DIR, resolve_comfyui_python, comfyui_main_py
from core.database import init_db, get_script, list_scripts, list_characters, list_scene_assets, list_music, list_sfx
from core.database import (
    update_script, update_character, update_scene_asset, update_music, update_sfx, update_shot, get_shot,
    get_project, list_shots, list_episodes, list_render_jobs, list_projects, delete_project,
    create_shot_review, list_shot_reviews, create_export_manifest, list_export_manifests,
    create_asset_version, create_subtitle_revision, create_delivery_package,
)
from core.ollama_client import list_models, refresh_models, resolve_model_profile
from core.model_manager import (
    list_models as cm_list, search_models as cm_search,
    comfyui_online, is_installed, get_model_dir,
    download_model, refresh_comfyui_cache, all_installed,
)
from core.orchestrator import (
    run_pipeline_generator, run_render_export_generator,
    run_stage_story, run_stage_characters, run_stage_scenes,
    run_stage_art_music_sfx, run_stage_shots, _stage_status,
)
from ui.edit_panel import (
    ai_edit_preview, ai_edit_execute, ai_edit_rollback,
    get_edit_history,
)

# ─── 主题 ───────────────────────────────────────────

CUSTOM_CSS = """
:root {
  --bg-primary: #0f1117; --bg-secondary: #1a1d27; --bg-tertiary: #272b3b;
  --text-primary: #e8eaed; --text-secondary: #9aa0b0;
  --accent: #6366f1; --accent-hover: #818cf8;
  --success: #22c55e; --warning: #f59e0b; --border: #2d3147;
  --radius: 12px;
}
.gradio-container { background: var(--bg-primary) !important; color: var(--text-primary); }
.gr-box { border-radius: var(--radius) !important; border-color: var(--border) !important; }
textarea, input, select { background: var(--bg-tertiary) !important; color: var(--text-primary) !important; border-color: var(--border) !important; border-radius: 8px !important; }
.gr-button-primary { background: linear-gradient(135deg, #6366f1, #8b5cf6) !important; border: none !important; color: white !important; font-weight: 700 !important; border-radius: 12px !important; padding: 12px 32px !important; }
.gr-button-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 20px rgba(99,102,241,.4); }
.gr-button-secondary { background: linear-gradient(135deg, #22c55e, #16a34a) !important; border: none !important; color: white !important; font-weight: 700 !important; border-radius: 12px !important; padding: 12px 32px !important; }
.gr-button-secondary:hover { box-shadow: 0 4px 20px rgba(34,197,94,.4); }
.gr-progress { height: 6px !important; border-radius: 3px !important; background: var(--bg-tertiary) !important; }
.gr-progress > div { background: linear-gradient(90deg, #6366f1, #22c55e) !important; }
.save-btn { background: #22c55e !important; color: white !important; }
.save-btn:hover { background: #16a34a !important; }
.tab-nav { border-bottom: 1px solid var(--border) !important; }
.tab-nav button { color: var(--text-secondary) !important; }
.tab-nav button.selected { color: var(--accent) !important; border-bottom-color: var(--accent) !important; }
"""


def get_ollama_models():
    try:
        refresh_models()
        models = list_models()
        return [m for m in models if "embed" not in m.lower()]
    except Exception:
        return []


# ─── 格式化查看 ──────────────────────────────────────

def format_content_markdown(pid: int) -> str:
    """从 DB 读取内容，生成可读的 Markdown 概览。"""
    if not pid:
        return "请先运行管线生成内容。"
    parts = []

    # 项目
    proj = get_project(pid)
    if proj:
        parts.append(f"## 📁 {proj.name}\n- 类型: {proj.genre}\n- 状态: {proj.status}\n")

    # 剧本
    scripts = list_scripts(pid)
    if scripts:
        s = scripts[0]
        parts.append(f"## 📖 剧本: {s.title}")
        if s.synopsis:
            parts.append(f"\n**简介**: {s.synopsis}")
        try:
            acts = json.loads(s.acts) if s.acts else []
        except:
            acts = []
        for i, act in enumerate(acts):
            scenes = act.get("scenes", [])
            parts.append(f"\n### 第{i+1}幕 — {act.get('title', '')}")
            for j, sc in enumerate(scenes):
                chars = ", ".join(sc.get("characters", []))
                parts.append(f"- 场景{j+1}: {sc.get('location', '')} | {sc.get('mood', '')} | 角色: {chars}")
    else:
        parts.append("\n## 📖 剧本\n（未生成）")

    # 角色
    chars = list_characters(pid)
    if chars:
        parts.append("\n## 👤 角色 ({})".format(len(chars)))
        for c in chars:
            parts.append(f"\n- **{c.name}** ({c.role}, {c.age}岁, {c.gender})")
            if c.appearance: parts.append(f"  - 外貌: {c.appearance[:60]}")
            if c.personality: parts.append(f"  - 性格: {c.personality[:60]}")
    else:
        parts.append("\n## 👤 角色\n（未生成）")

    # 场景
    scenes = list_scene_assets(pid)
    if scenes:
        parts.append(f"\n## 🏞️ 场景 ({len(scenes)})")
        for sc in scenes:
            parts.append(f"- **{sc.name}**: {sc.description[:50] if sc.description else ''} | 氛围: {sc.atmosphere}")
    else:
        parts.append("\n## 🏞️ 场景\n（未生成）")

    # 音乐
    music = list_music(pid)
    if music:
        parts.append(f"\n## 🎵 音乐 ({len(music)})")
        for m in music:
            parts.append(f"- {m.name} ({m.type}/{m.mood})")
    else:
        parts.append("\n## 🎵 音乐\n（未生成）")

    # 音效
    sfx_list = list_sfx(pid)
    if sfx_list:
        parts.append(f"\n## 🔊 音效 ({len(sfx_list)})")
        for sfx in sfx_list:
            parts.append(f"- {sfx.name} ({sfx.category})")
    else:
        parts.append("\n## 🔊 音效\n（未生成）")

    shots = list_shots(project_id=pid)
    if shots:
        parts.append(f"\n## 🎞️ 分镜 ({len(shots)})")
        for shot in shots[:8]:
            chars = ", ".join(json.loads(shot.characters) if shot.characters else [])
            parts.append(
                f"- Act {shot.act_number} / Scene {shot.scene_number} / Shot {shot.shot_number}: "
                f"{shot.location} | {shot.shot_type} | {shot.status} | {chars}"
            )
    else:
        parts.append("\n## 🎞️ 分镜\n（未规划）")

    return "\n".join(parts)


# ─── DB → 编辑 JSON ────────────────────────────────

def load_edit_data(pid: int) -> dict:
    data = {}
    scripts = list_scripts(pid) if pid else []
    if scripts:
        s = scripts[0]
        try: acts = json.loads(s.acts) if s.acts else []
        except: acts = []
        data["script"] = json.dumps({
            "id": s.id, "title": s.title, "synopsis": s.synopsis or "", "acts": acts,
        }, ensure_ascii=False, indent=2)
    else:
        data["script"] = ""

    chars = list_characters(pid) if pid else []
    data["characters"] = json.dumps([
        {"id": c.id, "name": c.name, "role": c.role, "age": c.age,
         "gender": c.gender, "appearance": c.appearance,
         "personality": c.personality, "background": c.background,
         "voice_profile": c.voice_profile}
        for c in chars
    ], ensure_ascii=False, indent=2) if chars else ""

    scenes = list_scene_assets(pid) if pid else []
    data["scenes"] = json.dumps([
        {"id": s.id, "name": s.name, "description": s.description,
         "lighting": s.lighting, "color_palette": s.color_palette,
         "atmosphere": s.atmosphere}
        for s in scenes
    ], ensure_ascii=False, indent=2) if scenes else ""

    music = list_music(pid) if pid else []
    data["music"] = json.dumps([
        {"id": m.id, "name": m.name, "type": m.type, "mood": m.mood,
         "tempo": m.tempo, "instruments": m.instruments, "description": m.description}
        for m in music
    ], ensure_ascii=False, indent=2) if music else ""

    sfx_list = list_sfx(pid) if pid else []
    data["sfx"] = json.dumps([
        {"id": s.id, "name": s.name, "category": s.category,
         "description": s.description, "tags": s.tags}
        for s in sfx_list
    ], ensure_ascii=False, indent=2) if sfx_list else ""

    return data


def format_model_profile(model_selection: str) -> str:
    profile = resolve_model_profile(model_selection)
    lines = ["### 🤖 阶段模型分配"]
    lines.extend([f"- `{stage}` → `{name}`" for stage, name in profile.items()])
    return "\n".join(lines)


def build_shot_table(pid: int) -> list[list[str]]:
    if not pid:
        return []
    from core.asset_registry import get_shot_bgm, get_shot_sfx, is_shot_tts_complete
    rows = []
    for shot in list_shots(project_id=pid):
        characters = json.loads(shot.characters) if shot.characters else []
        jobs = list_render_jobs(project_id=pid, shot_id=shot.id)
        latest_job = jobs[0] if jobs else None
        used_pipeline = latest_job.used_pipeline if latest_job else ""
        fallback_used = bool(getattr(latest_job, "fallback_used", 0)) if latest_job else False
        audio_flags = []
        if is_shot_tts_complete(pid, shot.id):
            audio_flags.append("TTS")
        if get_shot_bgm(pid, shot.id):
            audio_flags.append("BGM")
        if get_shot_sfx(pid, shot.id):
            audio_flags.append("SFX")
        rows.append([
            shot.id,
            shot.act_number,
            shot.scene_number,
            shot.shot_number,
            shot.location,
            shot.shot_type,
            shot.mood,
            ", ".join(characters[:3]),
            shot.status,
            used_pipeline[:14] if used_pipeline else "",
            "⚠️" if fallback_used else "",
            "/".join(audio_flags),
            "🔒" if int(getattr(shot, "locked", 0)) == 1 else "",
        ])
    return rows


def format_production_overview(pid: int) -> str:
    if not pid:
        return "运行管线后自动展示生产指标。"
    from core.asset_registry import project_snapshot
    proj = get_project(pid)
    episodes = list_episodes(pid)
    shots = list_shots(project_id=pid)
    ready = sum(1 for s in shots if s.status == "ready")
    rendered = sum(1 for s in shots if s.status == "rendered")
    approved = sum(1 for s in shots if s.status == "approved")
    rejected = sum(1 for s in shots if s.status == "rejected")
    qc_failed = sum(1 for s in shots if s.status == "qc_failed")
    locked = sum(1 for s in shots if int(getattr(s, "locked", 0)) == 1)
    exports = list_export_manifests(project_id=pid, limit=3)
    snap = project_snapshot(pid, proj.name if proj else "")
    return "\n".join([
        "### 🏭 生产总览",
        f"- 项目: {proj.name if proj else '未知'}",
        f"- 集数: {len(episodes)}",
        f"- 分镜数: {len(shots)}",
        f"- 待渲染: {ready}",
        f"- 已渲染: {rendered}",
        f"- 质检失败: {qc_failed}",
        f"- 已通过审核: {approved}",
        f"- 已退回: {rejected}",
        f"- 已锁定: {locked}",
        f"- TTS 完成: {snap.get('tts_done', 0)}/{snap.get('total_shots', 0)}",
        f"- 已合成: {snap.get('composed', 0)}/{snap.get('total_shots', 0)}",
        f"- 最近导出: {len(exports)}",
    ])


def shot_runtime_summary(pid: int, shot_id: int) -> str:
    from core.asset_registry import get_shot_bgm, get_shot_sfx, get_shot_tts
    jobs = list_render_jobs(project_id=int(pid), shot_id=int(shot_id))
    latest = jobs[0] if jobs else None
    lines = [f"### Shot {shot_id} 运行状态"]
    if latest:
        lines.append(f"- 渲染任务: `{latest.status}`")
        if latest.used_pipeline:
            lines.append(f"- 实际管线: `{latest.used_pipeline}`")
        if latest.requested_pipeline:
            lines.append(f"- 请求管线: `{latest.requested_pipeline}`")
        if int(getattr(latest, 'fallback_used', 0)) == 1:
            lines.append(f"- 降级回退: `{latest.fallback_from}` → `{latest.used_pipeline}`")
        meta_raw = getattr(latest, "output_meta", "{}") or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except Exception:
            meta = {}
        qg = meta.get("quality_gate", {}) if isinstance(meta, dict) else {}
        if qg:
            if qg.get("black_ratio") is not None:
                lines.append(f"- 黑帧比例: {float(qg.get('black_ratio', 0)):.1%}")
            if qg.get("freeze_detected") is not None:
                lines.append(f"- 静帧检测: {'是' if qg.get('freeze_detected') else '否'}")
    else:
        lines.append("- 渲染任务: 暂无")
    tts_count = len(get_shot_tts(int(pid), int(shot_id)))
    bgm_ready = bool(get_shot_bgm(int(pid), int(shot_id)))
    sfx_count = len(get_shot_sfx(int(pid), int(shot_id)))
    lines.append(f"- 音频命中: TTS {tts_count} 条 / BGM {'1' if bgm_ready else '0'} / SFX {sfx_count} 条")
    return "\n".join(lines)


def load_shot_form(pid: int, shot_id_text: str):
    empty = (
        "❌ 请输入有效 Shot ID",
        0, 1, 1, 1, "", "中景", "", "白天", "晴", "", "", "", "ready", False,
        "[]", "{}",
    )
    if not pid:
        return empty
    text = (shot_id_text or "").strip()
    if not text:
        return empty
    try:
        shot_id = int(text)
    except ValueError:
        return empty
    shot = next((s for s in list_shots(project_id=int(pid)) if s.id == shot_id), None)
    if not shot:
        return (f"❌ Shot {shot_id} 不存在",) + empty[1:]
    render_payload = shot.render_payload
    if isinstance(render_payload, str):
        try:
            render_payload = json.loads(render_payload) if render_payload else {}
        except Exception:
            render_payload = {}
    return (
        f"✅ 已载入 Shot {shot.id}",
        shot.id,
        shot.act_number,
        shot.scene_number,
        shot.shot_number,
        shot.location,
        shot.shot_type,
        shot.mood,
        shot.time_of_day,
        shot.weather,
        shot.narration,
        shot.camera_notes,
        shot.status,
        bool(int(shot.locked or 0)),
        shot.characters or "[]",
        json.dumps(render_payload, ensure_ascii=False, indent=2),
    )


def save_shot_form(
    pid: int,
    shot_id: int,
    act_number: int,
    scene_number: int,
    shot_number: int,
    location: str,
    shot_type: str,
    mood: str,
    time_of_day: str,
    weather: str,
    narration: str,
    camera_notes: str,
    status: str,
    locked: bool,
    characters_text: str,
    payload_text: str,
):
    if not pid or not shot_id:
        return "❌ 请先载入 Shot", build_shot_edit_json(pid), build_shot_table(pid), format_production_overview(pid)
    try:
        characters = json.loads(characters_text or "[]")
        payload = json.loads(payload_text or "{}")
    except Exception as e:
        return f"❌ JSON 解析失败: {e}", build_shot_edit_json(pid), build_shot_table(pid), format_production_overview(pid)
    payload.update({
        "location": location or "",
        "time_of_day": time_of_day or "白天",
        "weather": weather or "晴",
        "mood": mood or "",
        "narration": narration or "",
        "camera_angle": shot_type or "中景",
        "shot_type": shot_type or "中景",
        "characters": payload.get("characters") or characters,
    })
    update_shot(int(shot_id), {
        "act_number": int(act_number or 1),
        "scene_number": int(scene_number or 1),
        "shot_number": int(shot_number or 1),
        "location": location or "",
        "shot_type": shot_type or "中景",
        "mood": mood or "",
        "time_of_day": time_of_day or "白天",
        "weather": weather or "晴",
        "characters": characters,
        "narration": narration or "",
        "camera_notes": camera_notes or "",
        "status": status or "ready",
        "locked": 1 if locked else 0,
        "render_payload": payload,
    })
    _record_shot_asset_version(
        pid,
        int(shot_id),
        {
            "act_number": int(act_number or 1),
            "scene_number": int(scene_number or 1),
            "shot_number": int(shot_number or 1),
            "location": location or "",
            "shot_type": shot_type or "中景",
            "mood": mood or "",
            "time_of_day": time_of_day or "白天",
            "weather": weather or "晴",
            "characters": characters,
            "narration": narration or "",
            "camera_notes": camera_notes or "",
            "status": status or "ready",
            "locked": 1 if locked else 0,
            "render_payload": payload,
        },
        source_stage="shot_form_editor",
        notes="structured shot save",
    )
    return (
        f"✅ Shot {shot_id} 已保存",
        build_shot_edit_json(pid),
        build_shot_table(pid),
        format_production_overview(pid),
    )


# ─── 保存回调 ────────────────────────────────────────

def save_script_text(pid: int, text: str) -> str:
    if not pid or not text: return "❌ 无效数据"
    try:
        obj = json.loads(text)
        update_script(obj["id"], {
            "title": obj.get("title", ""),
            "synopsis": obj.get("synopsis", ""),
            "acts": json.dumps(obj.get("acts", []), ensure_ascii=False),
        })
        return "✅ 剧本已保存"
    except Exception as e: return f"❌ 保存失败: {e}"

def save_chars_text(pid: int, text: str) -> str:
    if not pid or not text: return "❌ 无数据"
    try:
        chars = json.loads(text)
        count = 0
        for c in chars:
            update_character(c["id"], {
                "name": c.get("name", ""), "role": c.get("role", ""),
                "age": c.get("age", ""), "gender": c.get("gender", ""),
                "appearance": c.get("appearance", ""),
                "personality": c.get("personality", ""),
                "background": c.get("background", ""),
                "voice_profile": c.get("voice_profile", ""),
            })
            count += 1
        return f"✅ {count} 个角色已保存"
    except Exception as e: return f"❌ 保存失败: {e}"

def save_scenes_text(pid: int, text: str) -> str:
    if not pid or not text: return "❌ 无数据"
    try:
        scenes = json.loads(text)
        count = 0
        for s in scenes:
            update_scene_asset(s["id"], {
                "name": s.get("name", ""),
                "description": s.get("description", ""),
                "lighting": s.get("lighting", ""),
                "color_palette": s.get("color_palette", ""),
                "atmosphere": s.get("atmosphere", ""),
            })
            count += 1
        return f"✅ {count} 个场景已保存"
    except Exception as e: return f"❌ 保存失败: {e}"

def save_music_text(pid: int, text: str) -> str:
    if not pid or not text: return "❌ 无数据"
    try:
        items = json.loads(text)
        count = 0
        for item in items:
            update_music(item["id"], {
                "name": item.get("name", ""),
                "type": item.get("type", "bgm"),
                "mood": item.get("mood", ""),
                "tempo": item.get("tempo", ""),
                "instruments": item.get("instruments", ""),
                "description": item.get("description", ""),
            })
            count += 1
        return f"✅ {count} 条音乐已保存"
    except Exception as e: return f"❌ 保存失败: {e}"

def save_sfx_text(pid: int, text: str) -> str:
    if not pid or not text: return "❌ 无数据"
    try:
        items = json.loads(text)
        count = 0
        for item in items:
            update_sfx(item["id"], {
                "name": item.get("name", ""),
                "category": item.get("category", ""),
                "description": item.get("description", ""),
                "tags": item.get("tags", ""),
            })
            count += 1
        return f"✅ {count} 条音效已保存"
    except Exception as e: return f"❌ 保存失败: {e}"


def _sanitize_project_name(name: str) -> str:
    safe = re.sub(r"[^\w\-\u4e00-\u9fff ]+", "_", (name or "").strip())
    safe = safe.replace("..", "_")
    safe = re.sub(r"\s+", " ", safe).strip(" ._")
    return safe[:80] or "未命名项目"


def _subtitle_dir(project_name: str) -> Path:
    return Path("output/projects") / project_name / "subtitles"


def _record_shot_asset_version(pid: int, shot_id: int, payload: dict, source_stage: str, notes: str = ""):
    create_asset_version({
        "project_id": int(pid),
        "shot_id": int(shot_id),
        "asset_type": "shot",
        "asset_ref_id": int(shot_id),
        "source_stage": source_stage,
        "content_json": payload,
        "notes": notes,
    })


def build_shot_edit_json(pid: int) -> str:
    if not pid:
        return ""
    shots = list_shots(project_id=int(pid))
    data = []
    for s in shots:
        render_payload = s.render_payload
        if isinstance(render_payload, str):
            try:
                render_payload = json.loads(render_payload) if render_payload else {}
            except Exception:
                render_payload = {}
        data.append({
            "id": s.id,
            "act_number": s.act_number,
            "scene_number": s.scene_number,
            "shot_number": s.shot_number,
            "location": s.location,
            "shot_type": s.shot_type,
            "mood": s.mood,
            "time_of_day": s.time_of_day,
            "weather": s.weather,
            "characters": json.loads(s.characters) if s.characters else [],
            "narration": s.narration,
            "camera_notes": s.camera_notes,
            "status": s.status,
            "locked": s.locked,
            "render_payload": render_payload,
        })
    return json.dumps(data, ensure_ascii=False, indent=2)


def save_shot_edit_text(pid: int, text: str) -> str:
    if not pid or not text:
        return "❌ 无分镜数据"
    try:
        items = json.loads(text)
        count = 0
        for item in items:
            payload = item.get("render_payload", {}) or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            payload.update({
                "location": item.get("location", ""),
                "time_of_day": item.get("time_of_day", "白天"),
                "weather": item.get("weather", "晴"),
                "mood": item.get("mood", ""),
                "narration": item.get("narration", ""),
                "camera_angle": item.get("shot_type", "中景"),
                "characters": payload.get("characters", []),
            })
            update_shot(int(item["id"]), {
                "location": item.get("location", ""),
                "shot_type": item.get("shot_type", "中景"),
                "mood": item.get("mood", ""),
                "time_of_day": item.get("time_of_day", "白天"),
                "weather": item.get("weather", "晴"),
                "characters": item.get("characters", []),
                "narration": item.get("narration", ""),
                "camera_notes": item.get("camera_notes", ""),
                "status": item.get("status", "ready"),
                "locked": int(item.get("locked", 0)),
                "render_payload": payload,
            })
            _record_shot_asset_version(
                pid,
                int(item["id"]),
                {
                    "location": item.get("location", ""),
                    "shot_type": item.get("shot_type", "中景"),
                    "mood": item.get("mood", ""),
                    "time_of_day": item.get("time_of_day", "白天"),
                    "weather": item.get("weather", "晴"),
                    "characters": item.get("characters", []),
                    "narration": item.get("narration", ""),
                    "camera_notes": item.get("camera_notes", ""),
                    "status": item.get("status", "ready"),
                    "locked": int(item.get("locked", 0)),
                    "render_payload": payload,
                },
                source_stage="shot_json_editor",
                notes="bulk shot json save",
            )
            count += 1
        return f"✅ {count} 个分镜已保存"
    except Exception as e:
        return f"❌ 保存失败: {e}"


def review_shot_action(pid: int, shot_id_text: str, action: str, notes: str = "") -> tuple[str, str]:
    if not pid:
        return "❌ 请先加载项目", build_shot_edit_json(pid)
    shot_id_text = (shot_id_text or "").strip()
    if not shot_id_text:
        return "❌ 请输入 Shot ID", build_shot_edit_json(pid)
    try:
        shot_id = int(shot_id_text)
    except ValueError:
        return "❌ Shot ID 必须是整数", build_shot_edit_json(pid)
    shot = next((s for s in list_shots(project_id=int(pid)) if s.id == shot_id), None)
    if not shot:
        return f"❌ Shot {shot_id} 不存在", build_shot_edit_json(pid)
    update_data = {}
    review_status = "pending"
    if action == "approve":
        update_data["status"] = "approved"
        review_status = "approved"
    elif action == "reject":
        update_data["status"] = "rejected"
        review_status = "rejected"
    elif action == "lock":
        update_data["locked"] = 1
    elif action == "unlock":
        update_data["locked"] = 0
    if update_data:
        update_shot(shot_id, update_data)
    create_shot_review({
        "project_id": int(pid),
        "shot_id": shot_id,
        "status": review_status,
        "reviewer": "ui_operator",
        "notes": (notes or action)[:500],
    })
    return f"✅ Shot {shot_id} 已执行: {action}", build_shot_edit_json(pid)


def approve_shot_action(pid: int, shot_id_text: str, notes: str = "", auto_lock: bool = False) -> tuple[str, str]:
    status_msg, shot_json = review_shot_action(pid, shot_id_text, "approve", notes)
    if not auto_lock or not status_msg.startswith("✅"):
        return status_msg, shot_json
    lock_note = f"{notes} / auto-lock".strip(" /") or "auto-lock after approve"
    lock_msg, shot_json = review_shot_action(pid, shot_id_text, "lock", lock_note)
    return f"{status_msg}\n{lock_msg}", shot_json


def run_shot_rerender_flow(
    pid: int,
    shot_id_text: str,
    notes: str = "",
    mode: str = "rerender",
    progress=gr.Progress(),
):
    if not pid:
        yield "❌ 请先加载项目", None
        return
    text = (shot_id_text or "").strip()
    if not text:
        yield "❌ 请输入 Shot ID", None
        return
    try:
        shot_id = int(text)
    except ValueError:
        yield "❌ Shot ID 必须是整数", None
        return
    shot = get_shot(shot_id)
    if not shot or int(shot.project_id) != int(pid):
        yield f"❌ Shot {shot_id} 不存在", None
        return

    operator_note = (notes or "").strip()
    review_status = "rework_requested" if mode == "rework" else "rerender_requested"
    label = "退回并重跑" if mode == "rework" else "重渲染"
    update_data = {
        "status": "ready",
        "locked": 0,
        "error": "",
    }
    update_shot(shot_id, update_data)
    create_shot_review({
        "project_id": int(pid),
        "shot_id": shot_id,
        "status": review_status,
        "reviewer": "ui_operator",
        "notes": (operator_note or label)[:500],
    })
    yield f"### ⏳ {label} Shot {shot_id}\n已重置为 `ready`，准备提交渲染。", None
    for log_md, video_path in run_render_step_flow(pid, str(shot_id), progress=progress):
        yield f"### 🎬 {label} Shot {shot_id}\n\n{log_md}", video_path


def get_shot_review_summary(pid: int, shot_id_text: str) -> str:
    if not pid:
        return "请先加载项目"
    text = (shot_id_text or "").strip()
    if not text:
        return "输入 Shot ID 查看审核历史"
    try:
        shot_id = int(text)
    except ValueError:
        return "Shot ID 必须是整数"
    rows = list_shot_reviews(project_id=int(pid), shot_id=shot_id, limit=10)
    if not rows:
        return f"Shot {shot_id} 暂无审核记录"
    lines = [f"### Shot {shot_id} 审核历史"]
    for row in rows:
        lines.append(f"- `{row['created_at'][:19]}` · **{row['status']}** · {row.get('notes', '')[:120]}")
    return "\n".join(lines)


def record_export_manifest_for_project(pid: int, export_path: str) -> str:
    if not pid or not export_path:
        return ""
    proj = get_project(int(pid))
    if not proj:
        return ""
    shots = list_shots(project_id=int(pid))
    create_export_manifest({
        "project_id": int(pid),
        "episode_id": 0,
        "export_type": "episode",
        "file_path": export_path,
        "manifest_json": {
            "project_name": proj.name,
            "shot_count": len(shots),
            "approved_shots": [s.id for s in shots if s.status == "approved"],
            "rendered_shots": [s.id for s in shots if s.status in ("rendered", "approved")],
        },
    })
    create_delivery_package({
        "project_id": int(pid),
        "episode_id": 0,
        "package_type": "hongguo_short_drama",
        "package_path": export_path,
        "assets_json": {
            "episode_video": export_path,
            "approved_shots": [s.id for s in shots if s.status == "approved"],
            "rendered_shots": [s.id for s in shots if s.status in ("rendered", "approved")],
        },
        "manifest_json": {
            "project_name": proj.name,
            "delivery_target": "hongguo_short_drama",
            "shot_count": len(shots),
        },
        "status": "assembled",
    })
    return f"✅ 已登记导出清单: {Path(export_path).name}"


def load_subtitle_workspace(pid: int, shot_id_text: str = "") -> tuple[str, str, str]:
    if not pid:
        return "", "", "❌ 请先加载项目"
    proj = get_project(int(pid))
    if not proj:
        return "", "", "❌ 项目不存在"
    shot_id_text = (shot_id_text or "").strip()
    if not shot_id_text:
        return "", "", "请输入 Shot ID"
    try:
        shot_id = int(shot_id_text)
    except ValueError:
        return "", "", "Shot ID 必须是整数"
    shot = next((s for s in list_shots(project_id=int(pid)) if s.id == shot_id), None)
    if not shot:
        return "", "", f"❌ Shot {shot_id} 不存在"

    subtitle_dir = _subtitle_dir(proj.name)
    subtitle_dir.mkdir(parents=True, exist_ok=True)
    subtitle_path = subtitle_dir / f"shot_{shot_id:04d}.srt"
    if subtitle_path.exists():
        return subtitle_path.read_text(encoding="utf-8"), str(subtitle_path), f"✅ 已加载已有字幕: {subtitle_path.name}"

    try:
        dialogue = json.loads(shot.dialogue) if shot.dialogue else []
    except Exception:
        dialogue = []
    from core.asset_registry import get_shot_tts
    from pipelines.compositor import dialogue_to_srt
    srt_text = dialogue_to_srt(dialogue, get_shot_tts(int(pid), shot_id))
    return srt_text, str(subtitle_path), "⚪ 由对白自动生成预览字幕，保存后生效"


def save_subtitle_text(pid: int, shot_id_text: str, subtitle_text: str) -> str:
    if not pid:
        return "❌ 请先加载项目"
    proj = get_project(int(pid))
    if not proj:
        return "❌ 项目不存在"
    shot_id_text = (shot_id_text or "").strip()
    subtitle_text = subtitle_text or ""
    if not shot_id_text:
        return "❌ 请输入 Shot ID"
    try:
        shot_id = int(shot_id_text)
    except ValueError:
        return "❌ Shot ID 必须是整数"
    subtitle_dir = _subtitle_dir(proj.name)
    subtitle_dir.mkdir(parents=True, exist_ok=True)
    subtitle_path = subtitle_dir / f"shot_{shot_id:04d}.srt"
    subtitle_path.write_text(subtitle_text, encoding="utf-8")
    create_subtitle_revision({
        "project_id": int(pid),
        "shot_id": shot_id,
        "file_path": str(subtitle_path),
        "subtitle_text": subtitle_text,
        "source": "ui_subtitle_editor",
    })
    create_asset_version({
        "project_id": int(pid),
        "shot_id": shot_id,
        "asset_type": "subtitle",
        "asset_ref_id": shot_id,
        "source_stage": "subtitle_editor",
        "file_path": str(subtitle_path),
        "content_json": {
            "subtitle_text": subtitle_text,
            "file_path": str(subtitle_path),
        },
        "notes": "subtitle save",
    })
    return f"✅ 字幕已保存: {subtitle_path}"


def delete_project_with_outputs(proj_choice: str) -> tuple[object, str]:
    if not proj_choice or not str(proj_choice).startswith("#"):
        return gr.update(choices=get_project_choices(), value=None), "❌ 请先选择项目"
    try:
        pid = int(str(proj_choice).split()[0].lstrip("#"))
    except Exception:
        return gr.update(choices=get_project_choices(), value=None), "❌ 项目标识无效"
    proj = get_project(pid)
    if not proj:
        return gr.update(choices=get_project_choices(), value=None), "❌ 项目不存在"
    out_dir = Path("output/projects") / proj.name
    delete_project(pid)
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    return gr.update(choices=get_project_choices(), value=None), f"✅ 已删除项目 #{pid}: {proj.name}"


# ─── 各阶段独立运行流 ────────────────────────────

def _relay_stage(stage_gen, log_lines: list, result_ref: dict):
    """把阶段 generator 的 (pct, log_md, partial) 转成 (log_md, partial, pid)。"""
    for pct, log_md, partial in stage_gen:
        result_ref.update({k: v for k, v in partial.items() if v})
        log_lines.clear()
        log_lines.extend(log_md.splitlines()[-30:])
        yield log_md, result_ref, result_ref.get("project_id", 0)


def _resolve_model(stage_model: str, fallback: str, global_model_var) -> str:
    """返回阶段模型，空则回退到全局模型组件值（由调用方传入）。"""
    return (stage_model or "").strip() or (fallback or "").strip() or "qwen2.5:7b"


def story_stage_flow(pid, premise, pname, genre, tone, acts, stage_m, global_m, progress=gr.Progress()):
    """步骤1: 剧本生成。"""
    if not premise or not premise.strip():
        yield "### ⚠️ 请输入创作构想", None, int(pid or 0); return
    model = _resolve_model(stage_m, global_m, None)
    result = {}
    for pct, log_md, partial in run_stage_story(
        project_id=int(pid or 0), premise=premise.strip(),
        project_name=_sanitize_project_name(pname) if pname else "",
        genre=genre or "玄幻", tone=tone or "热血",
        acts=int(acts or 3), model=model,
    ):
        result = partial
        progress(pct)
        yield log_md, partial, partial.get("project_id", int(pid or 0))


def chars_stage_flow(pid, stage_m, global_m, progress=gr.Progress()):
    """步骤2: 角色设计。"""
    if not pid:
        yield "### ⚠️ 请先运行步骤1生成剧本", None, 0; return
    model = _resolve_model(stage_m, global_m, None)
    for pct, log_md, partial in run_stage_characters(int(pid), model=model):
        progress(pct)
        yield log_md, partial, int(pid)


def scenes_stage_flow(pid, stage_m, global_m, progress=gr.Progress()):
    """步骤3: 场景设计。"""
    if not pid:
        yield "### ⚠️ 请先运行步骤1生成剧本", None, 0; return
    model = _resolve_model(stage_m, global_m, None)
    for pct, log_md, partial in run_stage_scenes(int(pid), model=model):
        progress(pct)
        yield log_md, partial, int(pid)


def art_music_stage_flow(pid, stage_m, global_m, progress=gr.Progress()):
    """步骤4: 美术/音乐/音效。"""
    if not pid:
        yield "### ⚠️ 请先运行步骤1生成剧本", None, 0; return
    model = _resolve_model(stage_m, global_m, None)
    for pct, log_md, partial in run_stage_art_music_sfx(int(pid), model=model):
        progress(pct)
        yield log_md, partial, int(pid)


def shots_stage_flow(pid, progress=gr.Progress()):
    """步骤5: 分镜规划。"""
    if not pid:
        yield "### ⚠️ 请先运行步骤1-3", None, 0; return
    for pct, log_md, partial in run_stage_shots(int(pid)):
        progress(pct)
        yield log_md, partial, int(pid)


def get_stage_status(pid) -> str:
    """返回当前各阶段完成状态。"""
    if not pid:
        return "无项目"
    try:
        s = _stage_status(int(pid))
        tick = lambda v: "✅" if v else "❌"
        lines = [
            f"### 📋 阶段状态 (项目 {pid})",
            f"- 步骤1 剧本: {tick(s['story'])} {s.get('script_title','')}",
            f"- 步骤2 角色: {tick(s['chars'])} ({s.get('n_chars',0)} 个)",
            f"- 步骤3 场景: {tick(s['scenes'])} ({s.get('n_scenes',0)} 个)",
            f"- 步骤4 音乐/音效: {tick(s['art_music_sfx'])}",
            f"- 步骤5 分镜: {tick(s['shots'])} ({s.get('n_shots',0)} 个)",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 状态查询失败: {e}"


# ─── Phase 1: 全流程生成 ────────────────────────────

def full_pipeline_flow(premise, project_name, genre, tone, acts, model,
                       story_model, char_model, scene_model, art_model,
                       progress=gr.Progress()):
    """yield (gen_log, gen_result, view_md, edit_data..., pid)"""
    if not premise or not premise.strip():
        yield ("### ⚠️ 请先输入创作构想", None, "", "", "", "", "", "", "", [], "", "", "", 0)
        return

    # 构建 per-stage 模型配置，未填则回退到全局 model
    base = model or "qwen2.5:7b"
    stage_models = {
        "director": story_model or base,
        "writer":   story_model or base,
        "character": char_model or base,
        "scene":     scene_model or base,
        "art":       art_model or base,
        "music":     art_model or base,
        "sound":     art_model or base,
        "review":    base,
    }

    result = None
    pid = 0
    try:
        for pct, log_md, partial in run_pipeline_generator(
            premise=premise.strip(),
            project_name=_sanitize_project_name(project_name) if project_name else "",
            genre=genre or "玄幻", tone=tone or "热血",
            acts=int(acts) if acts else 3,
            model=base,
            model_profile=stage_models,
            enable_render=False,
        ):
            progress(pct)
            result = partial
            yield (log_md, partial, gr.update(), gr.update(), gr.update(),
                   gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
                   gr.update(), gr.update(), gr.update(),
                   result.get("project_id", 0) if result else 0)
    except Exception as e:
        import traceback
        yield (f"### ❌ 管线崩溃\n```\n{e}\n{traceback.format_exc()[-2000:]}\n```",
               result, "", "", "", "", "", "", "", [], "", "", "", 0)
        return

    pid = (result or {}).get("project_id", 0)

    # 构建摘要
    summary = (
        f"项目 ID: {pid}  "
        f"角色: {len(result.get('characters', []))}  "
        f"场景: {len(result.get('scenes', []))}  "
        f"音乐: {len(result.get('music', []))}  "
    ) if result else ""
    log_md = f"### 🎉 内容生成完成\n{summary}\n\n---\n{log_md}" if result else log_md

    # 加载查看 + 编辑数据
    view_md = format_content_markdown(pid) if pid else ""
    edit_data = load_edit_data(pid) if pid else {}

    yield (
        log_md, result,
        view_md,
        edit_data.get("script", ""),
        edit_data.get("characters", ""),
        edit_data.get("scenes", ""),
        edit_data.get("music", ""),
        edit_data.get("sfx", ""),
        format_production_overview(pid),
        build_shot_table(pid),
        build_shot_edit_json(pid),
        "",
        "",
        pid,
    )


# ─── Phase 2: 渲染导出 ─────────────────────────────

def render_export_flow(pid, project_name, render_cfg, progress=gr.Progress()):
    """yield (render_log, render_result, render_pid)"""
    if not pid:
        yield ("### ⚠️ 请先生成内容", None, 0)
        return

    proj = get_project(pid)
    pname = project_name or (proj.name if proj else "")

    result = None
    try:
        for pct, log_md, partial in run_render_export_generator(
            project_id=pid, project_name=pname, render_config=render_cfg or {},
        ):
            progress(pct)
            result = partial
            yield (log_md, partial, pid)
    except Exception as e:
        import traceback
        yield (f"### ❌ 渲染出错\n```\n{e}\n{traceback.format_exc()[-2000:]}\n```",
               result, pid)


def resume_pipeline_flow(pid, progress=gr.Progress()):
    """续跑管线：跳过已完成阶段/shot，只处理未完成的。yield (log, state_md)"""
    if not pid:
        yield "### ⚠️ 请先生成内容（需要项目 ID）", "无项目"
        return

    from core.pipeline_state import resume_pipeline, describe_state
    log_lines = []
    try:
        gen = resume_pipeline(int(pid), max_retries=2)
        while True:
            try:
                msg, pct = next(gen)
                log_lines.append(msg)
                progress(pct)
                yield "### 🔄 续跑中...\n" + "\n".join(f"- {l}" for l in log_lines[-20:]), gr.update()
            except StopIteration as e:
                result = e.value
                break
    except Exception as e:
        import traceback
        yield (f"### ❌ 续跑出错\n```\n{e}\n{traceback.format_exc()[-2000:]}\n```",
               gr.update())
        return

    state_md = describe_state(int(pid))
    done_stages = result.get("stages", {}) if result else {}
    summary = "  ".join(
        f"{s}: {v.get('done', 0)}" for s, v in done_stages.items()
    )
    yield f"### ✅ 续跑完成\n{summary}\n\n" + "\n".join(f"- {l}" for l in log_lines[-30:]), state_md


def get_pipeline_state(pid):
    """返回当前管线状态 Markdown。"""
    if not pid:
        return "无项目"
    from core.pipeline_state import describe_state
    try:
        return describe_state(int(pid))
    except Exception as e:
        return f"❌ 获取状态失败: {e}"


# ─── 管线选择器 ─────────────────────────────────────────

def _pipeline_choices():
    """Build (display_label, pipeline_name) choices for dropdown."""
    from pipelines.render_pipeline import (
        load_pipeline_config, get_dispatcher, PipelineStatus,
    )
    cfg = load_pipeline_config()
    entries = cfg.get("pipelines", [])
    try:
        matrix = get_dispatcher().capability_matrix()
    except Exception:
        matrix = {}
    choices = []
    active_name = cfg.get("active_pipeline", "")
    for entry in sorted(entries, key=lambda e: e.get("priority", 99)):
        name = entry["name"]
        desc = entry.get("description", "无描述")[:45]
        prod = entry.get("production_ready", True)
        s = matrix.get(name, {})
        avail = s.get("available", False)
        if avail and prod:
            icon = "🟢"
        elif avail and not prod:
            icon = "🟡"
        elif not avail and prod:
            icon = "🔴"
        else:
            icon = "⚪"
        choices.append((f"{icon} {name} · {desc}", name))
    return choices, active_name


def _pipeline_status_card(pipeline_name):
    """Generate a Markdown status card for a pipeline."""
    from pipelines.render_pipeline import (
        load_pipeline_config, get_dispatcher, classify_pipeline_missing,
    )
    cfg = load_pipeline_config()
    entries = cfg.get("pipelines", [])
    entry = next((e for e in entries if e["name"] == pipeline_name), None)
    if not entry:
        return f"### ❌ 管线 `{pipeline_name}` 未找到"

    desc = entry.get("description", "无描述")
    prod = entry.get("production_ready", False)
    try:
        matrix = get_dispatcher().probe(force=True)
    except Exception as e:
        matrix = {}
    status = matrix.get(pipeline_name)
    avail = status.available if status else False
    missing = status.missing if status else []
    last_err = status.last_error if status else ""

    ecfg = entry.get("config", {})
    w = ecfg.get("width", "?")
    h = ecfg.get("height", "?")
    fps = ecfg.get("fps", "?")
    frames = ecfg.get("frames", "?")

    lines = []
    if avail and prod:
        lines.append(f"##### 🟢 **{pipeline_name}** · 可生产 · {desc}")
    elif avail and not prod:
        lines.append(f"##### 🟡 **{pipeline_name}** · 验证中 · {desc}")
    else:
        lines.append(f"##### 🔴 **{pipeline_name}** · 不可用 · {desc}")

    if w != "?":
        res_str = f"*{w}×{h}"
        if frames != "?":
            res_str += f" · {frames}帧"
        if fps != "?":
            res_str += f" · {fps}fps"
        lines.append(res_str + "*")

    if missing:
        sk, st = classify_pipeline_missing(missing)
        lines.append(f"\n**状态:** {st}  |  缺失 ({len(missing)} 项):")
        for m in missing:
            lines.append(f"- `{m}`")
    else:
        lines.append("\n✅ 所有组件就绪")

    if last_err:
        lines.append(f"\n⚠️ 上次错误: `{last_err[:100]}`")

    return "\n".join(lines)


def _on_pipeline_select(pipeline_name):
    """Dropdown change: set active pipeline + show status card."""
    if not pipeline_name:
        return "", "### 请选择一条渲染管线"
    from pipelines.render_pipeline import set_active_pipeline_name
    try:
        set_active_pipeline_name(pipeline_name)
        status_md = _pipeline_status_card(pipeline_name)
        return pipeline_name, status_md
    except ValueError as e:
        return pipeline_name, f"### ⚠️ 无法切换管线\n```\n{e}\n```"


def _detect_missing_models(_pid=None):
    """Scan active pipeline config for missing files on disk."""
    import os
    from pipelines.render_pipeline import load_pipeline_config
    cfg = load_pipeline_config()
    active = cfg.get("active_pipeline", "")
    entry = next((e for e in cfg.get("pipelines", []) if e["name"] == active), None)
    if not entry:
        return "### ⚠️ 无活跃管线"

    config = entry.get("config", {})
    # ComfyUI 模型基路径 — 所有相对路径相对于此目录下的子文件夹
    COMFY_MODELS_BASE = os.path.expanduser("~/Documents/ComfyUI/models")
    # 字段名 → 子文件夹映射（相对路径字段所属子目录）
    SUBDIR_MAP = {
        "checkpoint": "checkpoints",
        "vae": "vae",
        "flux_vae": "vae",
    }
    file_fields = [
        "checkpoint", "motion_model", "gguf_path", "text_encoder", "vae",
        "flux_checkpoint", "flux_text_encoder", "flux_vae",
    ]
    missing = []
    found = []
    # 优先检查全路径字段（如 flux_vae_path），再查短字段名
    full_path_aliases = {"flux_vae": "flux_vae_path"}
    for key in file_fields:
        # 如果存在对应的全路径字段，用它替代
        alias = full_path_aliases.get(key)
        alias_val = config.get(alias) if alias else None
        path_str = alias_val or config.get(key)
        if not path_str:
            continue
        full_path = os.path.expanduser(path_str)
        # 如果是相对路径，尝试拼接 ComfyUI 模型目录
        if not os.path.isfile(full_path) and not full_path.startswith("/"):
            subdir = SUBDIR_MAP.get(key, "")
            if subdir and full_path.startswith(subdir + "/"):
                # 已经是子目录格式，直接拼接
                guessed = os.path.join(COMFY_MODELS_BASE, full_path)
            else:
                guessed = os.path.join(COMFY_MODELS_BASE, subdir, full_path) if subdir else full_path
            if os.path.isfile(guessed):
                full_path = guessed
        if os.path.isfile(full_path):
            st = os.path.getsize(full_path)
            sz_str = f"{st/1024**3:.1f}GB" if st > 1024**3 else f"{st/1024**2:.0f}MB"
            found.append((key, sz_str))
        else:
            missing.append((key, path_str))

    lines = [f"### 🔍 管线 `{active}` 模型文件检测"]
    md_lines = []
    if found:
        md_lines.append(f"\n✅ 已就绪 ({len(found)} 项):")
        for k, sz in found:
            md_lines.append(f"- `{k}` ({sz})")
    if missing:
        md_lines.append(f"\n❌ 缺失 ({len(missing)} 项):")
        for k, v in missing:
            md_lines.append(f"- `{k}` → `{v}`")
    if not found and not missing:
        md_lines.append("\n没有可检测的模型路径")

    return "\n".join(lines + md_lines)


def _auto_download_missing(_pid=None):
    """Try to download missing model files for the active pipeline."""
    import os, subprocess, json
    from pipelines.render_pipeline import load_pipeline_config

    cfg = load_pipeline_config()
    active = cfg.get("active_pipeline", "")
    entry = next((e for e in cfg.get("pipelines", []) if e["name"] == active), None)
    if not entry:
        return "### ⚠️ 无活跃管线"

    config = entry.get("config", {})
    file_fields = [
        "checkpoint", "motion_model", "gguf_path", "text_encoder", "vae",
        "flux_checkpoint", "flux_text_encoder", "flux_vae",
    ]

    # ── 已知 ModelScope 下载源 ──
    KNOWN_SOURCES = {
        "Wan2.2_VAE.safetensors":
            ("modelscope", "AI-ModelScope/Wan2.1-ComfyUI", "Wan2.2_VAE.safetensors"),
        "umt5_xxl_fp8_e4m3fn_scaled.safetensors":
            ("modelscope", "Kijai/umt5-xxl-fp8-e4m3fn-scaled", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
        "Wan2.2-TI2V-5B-Q4_K_M.gguf":
            ("modelscope", "Kijai/Wan2.2-TI2V-5B-gguf", "Wan2.2-TI2V-5B-Q4_K_M.gguf"),
        "hsxl_temporal_layers.f16.safetensors":
            ("hf", "Kijai/hsxl_temporal_layers_fp16", "hsxl_temporal_layers.f16.safetensors"),
    }

    missing_files = []
    for key in file_fields:
        path_str = config.get(key)
        if not path_str:
            continue
        full_path = os.path.expanduser(path_str)
        if not os.path.isfile(full_path):
            missing_files.append((key, path_str))

    if not missing_files:
        return "### ✅ 管线 `{}` 所有模型文件已就绪".format(active)

    lines = ["### ⬇️ 正在尝试下载缺失模型..."]
    results = []

    for key, path_str in missing_files:
        basename = os.path.basename(path_str)
        target_dir = os.path.dirname(os.path.expanduser(path_str))
        os.makedirs(target_dir, exist_ok=True)

        if basename in KNOWN_SOURCES:
            source_type, repo, filename = KNOWN_SOURCES[basename]
            lines.append(f"\n📥 **{basename}**")
            lines.append(f"   源: {source_type}/{repo}")

            if source_type == "modelscope":
                cmd = [
                    "python", "-m", "modelscope", "download",
                    "--local_dir", target_dir,
                    repo, filename,
                ]
            else:
                # HF
                cmd = [
                    "hf", "download", repo, filename,
                    "--local-dir", target_dir,
                ]

            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if os.path.isfile(os.path.join(target_dir, filename)):
                    results.append(f"✅ {basename} — 下载成功")
                else:
                    results.append(f"❌ {basename} — 下载后未找到文件")
            except subprocess.TimeoutExpired:
                results.append(f"⏱️ {basename} — 下载超时")
            except FileNotFoundError:
                results.append(f"⚠️ {basename} — CLI 工具不可用 (`{cmd[0]}`)")
            except Exception as e:
                results.append(f"❌ {basename} — {str(e)[:80]}")
        else:
            results.append(f"⚠️ {basename} — 未知下载源，请手动下载到 `{target_dir}`")

    lines.append("\n---\n### 下载结果")
    for r in results:
        lines.append(r)

    # Refresh status card by re-probing
    from pipelines.render_pipeline import reset_dispatcher
    reset_dispatcher()

    lines.append("\n\n> 💡 重新探测状态后可查看最新管线就绪情况")
    return "\n".join(lines)


# ─── 各步骤独立运行 ────────────────────────────────────────

def run_music_step_flow(pid, progress=gr.Progress()):
    """仅生成配乐，yield (log_md, audio_path)。"""
    if not pid:
        yield "❌ 无项目", None; return
    proj = get_project(int(pid))
    if not proj:
        yield "❌ 项目不存在", None; return
    progress(0.1, desc="初始化配乐生成…")
    yield "### ⏳ 配乐生成中…", None
    try:
        from pathlib import Path as _P
        from pipelines.audio_pipeline import generate_project_music
        out_dir = _P("output/projects") / proj.name / "audio"
        progress(0.3, desc="生成中…")
        results = generate_project_music(int(pid), out_dir)
        done = [r for r in results if r.get("success") or r.get("skipped")]
        files = [r["file"] for r in done if r.get("file") and _P(r["file"]).exists()]
        progress(1.0)
        log = f"### ✅ 配乐生成完成\n共 {len(results)} 首，成功 {len(done)} 首"
        for r in results:
            st = "♻️ 复用" if r.get("skipped") else ("✅" if r.get("success") else "❌")
            log += f"\n- {st} **{r.get('name','?')}**"
        yield log, (files[0] if files else None)
    except Exception as e:
        import traceback
        yield f"### ❌ 配乐生成失败\n```\n{e}\n{traceback.format_exc()[-2000:]}\n```", None


def run_tts_step_flow(pid, shot_id_str: str = "", progress=gr.Progress()):
    """仅生成 TTS。shot_id_str 为空时处理全部 shot。yield (log_md, audio_path)。"""
    if not pid:
        yield "❌ 无项目", None; return
    proj = get_project(int(pid))
    if not proj:
        yield "❌ 项目不存在", None; return
    try:
        from pathlib import Path as _P
        from pipelines.audio_pipeline import generate_shot_tts
        out_dir = _P("output/projects") / proj.name / "audio"
        shots = list_shots(project_id=int(pid))
        if shot_id_str and shot_id_str.strip():
            try:
                sid = int(shot_id_str)
                shots = [s for s in shots if s.id == sid]
            except ValueError:
                pass
        total = len(shots)
        if not total:
            yield "❌ 没有分镜", None; return
        progress(0.0)
        all_files = []
        for i, shot in enumerate(shots):
            progress((i + 1) / total, desc=f"TTS shot {shot.id}…")
            yield f"### ⏳ TTS shot {shot.id} ({i+1}/{total})…", None
            results = generate_shot_tts(int(pid), shot.id, out_dir)
            all_files += [r["file"] for r in results if r.get("file")]
        progress(1.0)
        yield f"### ✅ TTS 生成完成，共 {len(all_files)} 条音频", (all_files[0] if all_files else None)
    except Exception as e:
        import traceback
        yield f"### ❌ TTS 失败\n```\n{e}\n{traceback.format_exc()[-2000:]}\n```", None


def run_sfx_step_flow(pid, progress=gr.Progress()):
    """仅生成音效，yield (log_md, audio_path)。"""
    if not pid:
        yield "❌ 无项目", None; return
    proj = get_project(int(pid))
    if not proj:
        yield "❌ 项目不存在", None; return
    try:
        from pathlib import Path as _P
        from pipelines.audio_pipeline import generate_project_sfx
        out_dir = _P("output/projects") / proj.name / "audio"
        progress(0.2)
        yield "### ⏳ 音效生成中…", None
        results = generate_project_sfx(int(pid), out_dir)
        done = [r for r in results if r.get("success") or r.get("skipped")]
        files = [r["file"] for r in done if r.get("file")]
        progress(1.0)
        log = f"### ✅ 音效完成，{len(done)}/{len(results)}"
        for r in results:
            st = "♻️" if r.get("skipped") else ("✅" if r.get("success") else "❌")
            log += f"\n- {st} {r.get('name','?')}"
        yield log, (files[0] if files else None)
    except Exception as e:
        import traceback
        yield f"### ❌ 音效失败\n```\n{e}\n{traceback.format_exc()[-2000:]}\n```", None


def run_render_step_flow(pid, shot_id_str: str = "", progress=gr.Progress()):
    """仅渲染视频帧。shot_id_str 为空处理全部，可逗号分隔多个 ID。yield (log_md, video_path)。"""
    if not pid:
        yield "❌ 无项目", None; return
    proj = get_project(int(pid))
    if not proj:
        yield "❌ 项目不存在", None; return
    try:
        from pipelines.batch_renderer import BatchRenderer
        renderer = BatchRenderer(proj.name, project_id=int(pid))
        shots = list_shots(project_id=int(pid))
        if shot_id_str and shot_id_str.strip():
            ids = {int(x.strip()) for x in shot_id_str.split(",") if x.strip().isdigit()}
            shots = [s for s in shots if s.id in ids]
        if not shots:
            yield "❌ 没有可渲染的分镜", None; return

        from core.database import get_shot
        scene_payloads = []
        for shot in shots:
            s = get_shot(shot.id)
            if not s: continue
            import json as _j
            payload = _j.loads(s.render_payload) if s.render_payload else {}
            payload["shot_id"] = s.id
            scene_payloads.append(payload)

        progress(0.1)
        yield f"### ⏳ 渲染 {len(scene_payloads)} 个分镜…", None
        videos = renderer.render_multi_scene(scene_payloads, max_workers=1)
        progress(1.0)
        log = f"### ✅ 渲染完成 {len(videos)}/{len(scene_payloads)}"
        yield log, (videos[0] if videos else None)
    except Exception as e:
        import traceback
        yield f"### ❌ 渲染失败\n```\n{e}\n{traceback.format_exc()[-2000:]}\n```", None


def run_composite_step_flow(pid, progress=gr.Progress()):
    """仅运行合成步骤，yield (log_md, video_path)。"""
    if not pid:
        yield "❌ 无项目", None; return
    proj = get_project(int(pid))
    if not proj:
        yield "❌ 项目不存在", None; return
    try:
        progress(0.2)
        yield "### ⏳ 合成中…", None
        from pipelines.compositor import run_compositor_pipeline
        result = run_compositor_pipeline(
            project_id=int(pid), episode=1, burn_subs=True, crossfade=0.5,
        )
        progress(1.0)
        if result:
            yield f"### ✅ 合成完成\n`{result}`", result
        else:
            yield "### ❌ 合成失败（可能缺少视频或音频素材）", None
    except Exception as e:
        import traceback
        yield f"### ❌ 合成失败\n```\n{e}\n{traceback.format_exc()[-2000:]}\n```", None


def ai_enhance_step(pid, step: str, content: str, instruction: str, mdl: str = "") -> tuple[str, str]:
    """用 AI 优化某一步骤的 prompt/content。
    step: 'music' | 'sfx' | 'script' | 'chars' | 'scenes'
    返回 (enhanced_content, status_msg)。
    """
    if not pid or not content:
        return content, "❌ 无内容可优化"
    try:
        from core.ollama_client import call_ollama, resolve_model_profile
        mdl = mdl or resolve_model_profile("art_music") or "qwen2.5:14b"
        step_hints = {
            "music": "优化配乐描述，使其更适合作为 AI 音乐生成 prompt。保留 JSON 结构，只修改 prompt_for_gen / description / mood / instruments / tempo 字段。",
            "sfx": "优化音效描述，保留 JSON 结构，只修改 description / tags / category 字段，让其更精确。",
            "script": "优化剧本内容，增强戏剧性和情感深度，保留 JSON 结构。",
            "chars": "深化角色设定，丰富 appearance / personality / background / voice_profile，保留 JSON 结构。",
            "scenes": "优化场景描述，增强视觉感和氛围细节，保留 JSON 结构。",
        }
        hint = step_hints.get(step, "优化内容，保留原有 JSON 结构。")
        extra = f"\n用户额外要求：{instruction}" if instruction and instruction.strip() else ""
        prompt = f"""你是专业的故事创作助手。{hint}{extra}

直接返回修改后的完整 JSON，不要加任何说明文字。

原始内容：
{content[:3000]}"""
        result = call_ollama(prompt, model=mdl, max_tokens=2048)
        if not result:
            return content, "❌ AI 响应为空"
        # 从结果中提取 JSON
        import re
        m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", result)
        json_str = m.group(1) if m else result.strip()
        # 验证 JSON
        json.loads(json_str)
        return json_str, f"✅ AI 优化完成（{step}）"
    except json.JSONDecodeError:
        return result, "⚠️ AI 返回了内容（但 JSON 格式可能有问题，请检查后保存）"
    except Exception as e:
        return content, f"❌ AI 优化失败: {e}"


def load_music_status(pid) -> str:
    """返回配乐状态 Markdown。"""
    if not pid:
        return ""
    try:
        from pathlib import Path as _P
        tracks = list_music(int(pid))
        if not tracks:
            return "⚪ 暂无配乐（请先运行 Phase 1 生成内容）"
        lines = []
        for t in tracks:
            has_file = bool(t.file_path and _P(t.file_path).exists())
            icon = "✅" if has_file else "⚪"
            lines.append(f"{icon} **{t.name}** — {t.mood or '?'} / {t.tempo or '?'}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


def load_tts_status(pid) -> str:
    """返回 TTS 状态 Markdown（按 shot 汇总）。"""
    if not pid:
        return ""
    try:
        from core.asset_registry import is_shot_tts_complete
        shots = list_shots(project_id=int(pid))
        if not shots:
            return "⚪ 暂无分镜"
        done = sum(1 for s in shots if is_shot_tts_complete(int(pid), s.id))
        return f"**TTS 进度**: {done}/{len(shots)} 个 shot 已完成"
    except Exception as e:
        return f"❌ {e}"


def load_render_status(pid) -> str:
    """返回渲染状态 Markdown（按 shot 汇总）。"""
    if not pid:
        return ""
    try:
        from core.asset_registry import is_shot_rendered
        shots = list_shots(project_id=int(pid))
        if not shots:
            return "⚪ 暂无分镜"
        done = sum(1 for s in shots if is_shot_rendered(s.id))
        return f"**渲染进度**: {done}/{len(shots)} 个 shot 已渲染"
    except Exception as e:
        return f"❌ {e}"


def load_shot_tts_detail(pid, shot_id_str: str) -> tuple[str, str]:
    """加载某 shot 的对白和 TTS 状态。返回 (dialogue_json, status_md)。"""
    if not pid or not shot_id_str:
        return "", ""
    try:
        sid = int(shot_id_str)
        from core.database import get_shot as _get_shot
        shot = _get_shot(sid)
        if not shot:
            return "", f"❌ Shot {sid} 不存在"
        dialogue = shot.dialogue or "[]"
        from core.asset_registry import is_shot_tts_complete, get_shot_tts
        tts_done = is_shot_tts_complete(int(pid), sid)
        tts_files = get_shot_tts(int(pid), sid)
        status = f"{'✅ TTS 已完成' if tts_done else '⚪ TTS 未生成'} — {len(tts_files)} 条音频"
        return dialogue, status
    except Exception as e:
        return "", f"❌ {e}"


# ─── 系统状态 + 音频配置 ──────────────────────────────────

def get_system_status() -> str:
    """返回整个渲染/音频管线的后端状态 Markdown。"""
    from pathlib import Path as _Path
    lines = ["### 🖥️ 系统状态"]

    # ComfyUI
    online = comfyui_online()
    lines.append(f"**ComfyUI**: {'🟢 在线' if online else '🔴 离线'}")

    lines.append("**视频生成**: Wan2.2 TI2V 5B ✅")

    # TTS
    try:
        from pipelines.audio_pipeline import _pick_tts_backend, BARK_PYTHON, CHATTTS_PYTHON, _check_edge_tts
        backend = _pick_tts_backend()
        chattts_ok = CHATTTS_PYTHON.exists()
        bark_ok = BARK_PYTHON.exists()
        edge_ok = _check_edge_tts()
        tts_str = {
            "chattts": "✅ ChatTTS（本地，原生中文，推荐）",
            "bark": "✅ Bark（本地，多语言）",
            "edge_tts": "✅ Edge-TTS（在线免费）",
            "kokoro": "✅ Kokoro（本地）",
            "pyttsx3": "⚠️ pyttsx3（系统回退）",
        }.get(backend, backend)
        lines.append(f"**TTS 后端**: {tts_str}")
        avail = []
        if chattts_ok: avail.append("ChatTTS")
        if bark_ok: avail.append("Bark")
        if edge_ok: avail.append("Edge-TTS")
        if avail: lines.append(f"  *可用*: {', '.join(avail)}")
    except Exception as e:
        lines.append(f"**TTS**: ❓ {e}")

    # BGM
    try:
        from pipelines.audio_pipeline import _check_acestep_music
        if _check_acestep_music():
            lines.append("**BGM 生成**: ✅ Ace-Step 1.5（ComfyUI）→ ffmpeg 合成兜底")
        else:
            lines.append("**BGM 生成**: ⚠️ ffmpeg 合成（Ace-Step 未就绪或缺模型）")
    except Exception:
        lines.append("**BGM 生成**: ffmpeg 合成（内置保底）")

    return "\n\n".join(lines)


def test_tts_preview(text: str, voice_type: str) -> tuple[str, str]:
    """生成一段 TTS 试听，返回 (audio_path, log)。"""
    import tempfile
    from pipelines.audio_pipeline import (
        generate_tts, _pick_tts_backend, _BARK_VOICE_MAP, _VOICE_MAP,
        _CHATTTS_VOICE_SEEDS,
    )
    backend = _pick_tts_backend()
    out = tempfile.mktemp(suffix=".mp3")
    try:
        if backend == "chattts":
            seed = _CHATTTS_VOICE_SEEDS.get(voice_type, _CHATTTS_VOICE_SEEDS["default"])
            ok = generate_tts(text, out, backend="chattts", voice_seed=seed)
        elif backend == "bark":
            preset = _BARK_VOICE_MAP.get(voice_type, _BARK_VOICE_MAP["default"])
            ok = generate_tts(text, out, backend="bark", voice_preset=preset)
        else:
            voice = _VOICE_MAP.get(voice_type, _VOICE_MAP["default"])
            ok = generate_tts(text, out, voice=voice, backend=backend)
        if ok:
            return out, f"✅ 生成成功（{backend}）"
        return None, f"❌ 生成失败（{backend}）"
    except Exception as e:
        return None, f"❌ {e}"


def test_bgm_preview(mood: str, duration: int = 10) -> tuple[str, str]:
    """生成一段 BGM 试听，返回 (audio_path, log)。"""
    import tempfile
    from pipelines.audio_pipeline import generate_music
    out = tempfile.mktemp(suffix=".mp3")
    try:
        ok = generate_music("preview", out, duration=duration, mood=mood)
        if ok:
            return out, f"✅ BGM 生成成功（mood={mood}）"
        return None, "❌ BGM 生成失败"
    except Exception as e:
        return None, f"❌ {e}"


# ─── ComfyUI 模型管理 ────────────────────────────────

MODEL_TYPE_LABELS = {
    "checkpoint":  "Checkpoint (大模型)",
    "lora":        "LoRA (风格/角色)",
    "vae":         "VAE",
    "controlnet":  "ControlNet",
    "upscale":     "Upscale 模型",
}


def cm_refresh_list(model_type: str, query: str = "") -> tuple[list, str]:
    """刷新指定类型模型列表，返回 (choices, status_md)。"""
    if not comfyui_online():
        return [], "⚠️ ComfyUI 离线 — 无法查询已安装模型"
    models = cm_search(query, model_type, force_refresh=True)
    status = f"✅ ComfyUI 在线 · **{MODEL_TYPE_LABELS.get(model_type, model_type)}** — 找到 {len(models)} 个"
    return models, status


def cm_load_all_types() -> tuple[list, list, list, list, str]:
    """一次性加载所有类型，返回 (checkpoints, loras, vaes, controlnets, status)。"""
    if not comfyui_online():
        return [], [], [], [], "⚠️ ComfyUI 离线"
    installed = all_installed()
    ckpts = installed.get("checkpoint", [])
    loras = installed.get("lora", [])
    vaes  = installed.get("vae", [])
    cns   = installed.get("controlnet", [])
    msg = (f"✅ 已加载: Checkpoint×{len(ckpts)} · LoRA×{len(loras)} "
           f"· VAE×{len(vaes)} · ControlNet×{len(cns)}")
    return ckpts, loras, vaes, cns, msg


def cm_do_download(source: str, model_type: str, filename: str, progress=gr.Progress()):
    """下载模型，流式输出进度。"""
    if not source.strip():
        yield "❌ 请输入来源 URL 或 HuggingFace 路径"; return
    if not model_type:
        yield "❌ 请选择模型类型"; return

    dest_dir = get_model_dir(model_type)
    yield f"⏳ 目标目录: `{dest_dir}`\n开始下载..."

    log = []

    def _prog(msg, pct=0.0):
        log.append(msg)
        progress(pct, desc=msg)

    success, final_msg = download_model(
        source=source, model_type=model_type,
        filename=filename.strip() or "",
        progress_fn=_prog,
    )
    full_log = "\n".join(log[-20:])
    yield f"{final_msg}\n\n```\n{full_log}\n```"


def cm_check_file(filename: str, model_type: str) -> str:
    """检查文件是否已存在于 ComfyUI 目录。"""
    if not filename.strip():
        return ""
    exists = is_installed(filename.strip(), model_type)
    d = get_model_dir(model_type)
    if exists:
        return f"✅ 已存在: `{d / filename.strip()}`"
    return f"❌ 未找到: `{d / filename.strip()}`"


# ─── 已有项目加载 ─────────────────────────────────────

def get_project_choices() -> list[str]:
    """返回所有项目的下拉选项，最新在前。"""
    try:
        projects = list_projects()
        result = []
        for p in sorted(projects, key=lambda x: x.id, reverse=True):
            shots = list_shots(project_id=p.id)
            rendered = sum(1 for s in shots if s.status == "rendered")
            label = f"#{p.id}  {p.name}  ({rendered}/{len(shots)} 已渲染)"
            result.append(label)
        return result
    except Exception:
        return []


def load_existing_project(proj_choice: str):
    """加载已有项目到 UI（非流式，queue=False）。"""
    empty = ("", None, "请先选择项目", "", "", "", "", "", "运行管线后自动展示生产指标。", [], 0,
             "⚪ 未生成", "⚪ 未生成", "⚪ 未渲染", "", "", "")
    if not proj_choice or not str(proj_choice).startswith("#"):
        return empty
    try:
        pid = int(str(proj_choice).split()[0].lstrip("#"))
    except Exception:
        return empty
    proj = get_project(pid)
    if not proj:
        return ("❌ 项目不存在", None, "", "", "", "", "", "", "", [], 0,
                "⚪ 未生成", "⚪ 未生成", "⚪ 未渲染", "", "", "")

    view_md = format_content_markdown(pid)
    edit_data = load_edit_data(pid)
    overview = format_production_overview(pid)
    shot_rows = build_shot_table(pid)
    return (
        f"### 📂 已加载项目 #{pid}: {proj.name}",
        {"project_id": pid, "name": proj.name},
        view_md,
        edit_data.get("script", ""),
        edit_data.get("characters", ""),
        edit_data.get("scenes", ""),
        edit_data.get("music", ""),
        edit_data.get("sfx", ""),
        overview,
        shot_rows,
        pid,
        load_music_status(pid),
        load_tts_status(pid),
        load_render_status(pid),
        build_shot_edit_json(pid),
        "",
        "",
    )


MODEL_AUDIT_SPECS = [
    {
        "group": "Wan 2.2 视频主线",
        "name": "Wan2.2-TI2V-5B GGUF",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/unet/Wan2.2-TI2V-5B-Q4_K_M.gguf",
        "min_size_mb": 1200,
        "critical": True,
    },
    {
        "group": "Wan 2.2 视频主线",
        "name": "Wan2.2-T2V 14B/T2V A14B",
        "kind": "any",
        "paths": [
            "~/myworkspace/ComfyUI_models/unet/Wan2.2-T2V-14B-Q4_K_M.gguf",
            "~/myworkspace/ComfyUI_models/unet/Wan2.2-T2V-A14B-Q4_K_M.gguf",
            "~/myworkspace/ComfyUI_models/wan_t2v/Wan2.2-T2V-A14B",
        ],
        "patterns": ["*.gguf", "*.safetensors", "*.bin", "*.pt"],
        "critical": False,
    },
    {
        "group": "Wan 2.2 视频主线",
        "name": "Wan2.2-I2V A14B",
        "kind": "dir",
        "path": "~/myworkspace/ComfyUI_models/wan_i2v/Wan2.2-I2V-A14B",
        "patterns": ["*.gguf", "*.safetensors", "*.bin", "*.pt"],
        "critical": False,
    },
    {
        "group": "Wan 编辑 / 动画",
        "name": "Wan2.1-VACE-1.3B",
        "kind": "dir",
        "path": "~/myworkspace/ComfyUI_models/wan_vace/Wan2.1-VACE-1.3B",
        "patterns": ["*.safetensors", "*.bin", "*.pt", "*.gguf"],
        "critical": False,
    },
    {
        "group": "Wan 编辑 / 动画",
        "name": "Wan2.2-Animate-14B",
        "kind": "dir",
        "path": "~/myworkspace/ComfyUI_models/wan_animate/Wan2.2-Animate-14B",
        "patterns": ["*.safetensors", "*.bin", "*.pt", "*.gguf"],
        "critical": False,
    },
    {
        "group": "Wan 编码器 / VAE",
        "name": "Wan UMT5 FP8",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "min_size_mb": 1000,
        "critical": True,
    },
    {
        "group": "Wan 编码器 / VAE",
        "name": "Wan UMT5 BF16",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/text_encoders/wan2.2_umt5/models_t5_umt5-xxl-enc-bf16.pth",
        "min_size_mb": 1000,
        "critical": True,
    },
    {
        "group": "Wan 编码器 / VAE",
        "name": "Wan2.2 VAE",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/vae/Wan2.2_VAE.safetensors",
        "min_size_mb": 500,
        "critical": True,
    },
    {
        "group": "FLUX",
        "name": "FLUX.2-klein-4B",
        "kind": "any",
        "paths": [
            "~/myworkspace/ComfyUI_models/checkpoints/flux-2-klein-4b.safetensors",
            "~/myworkspace/ComfyUI_models/diffusion_models/flux-2-klein-4b.safetensors",
            "~/myworkspace/ComfyUI_models/checkpoints/flux_2_klein_4B",
        ],
        "patterns": ["*flux*4b*.safetensors"],
        "critical": False,
    },
    {
        "group": "FLUX",
        "name": "FLUX VAE ae.safetensors",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/vae/ae.safetensors",
        "min_size_mb": 100,
        "critical": False,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step XL SFT",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/diffusion_models/acestep_v1.5_xl_sft_bf16.safetensors",
        "min_size_mb": 3000,
        "critical": True,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step XL Turbo",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/diffusion_models/acestep_v1.5_xl_turbo_bf16.safetensors",
        "min_size_mb": 3000,
        "critical": True,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step Turbo AIO",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/diffusion_models/acestep_v1.5_turbo.safetensors",
        "min_size_mb": 1000,
        "critical": False,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step Qwen 0.6B",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/text_encoders/qwen_0.6b_ace15.safetensors",
        "min_size_mb": 500,
        "critical": True,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step Qwen 4B",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/text_encoders/qwen_4b_ace15.safetensors",
        "min_size_mb": 1000,
        "critical": True,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step VAE",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/vae/ace_1.5_vae.safetensors",
        "min_size_mb": 100,
        "critical": True,
    },
    {
        "group": "角色一致性",
        "name": "InstantID ControlNet",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/controlnet/InstantID-ControlNet.safetensors",
        "min_size_mb": 500,
        "critical": True,
    },
    {
        "group": "角色一致性",
        "name": "InstantID IP-Adapter",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/instantid/ip-adapter.bin",
        "min_size_mb": 500,
        "critical": True,
    },
    {
        "group": "角色一致性",
        "name": "CLIP Vision H14",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
        "min_size_mb": 1000,
        "critical": True,
    },
    {
        "group": "角色一致性",
        "name": "InsightFace antelopev2",
        "kind": "dir",
        "path": "~/myworkspace/ComfyUI_models/insightface/models/antelopev2",
        "patterns": ["*.onnx"],
        "critical": True,
    },
]


def _human_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.1f} GiB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / 1024 ** 2:.1f} MiB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KiB"
    return f"{num_bytes} B"


def _is_metadata_only_file(path: Path) -> bool:
    name = path.name
    meta_suffixes = (".metadata", ".lock", ".aria2", ".idmdownload", ".part")
    meta_names = {".gitattributes", ".gitignore", "README.md", "CACHEDIR.TAG", ".DS_Store", ".msc", ".mv"}
    return (
        name in meta_names
        or name.endswith(meta_suffixes)
        or ".cache/huggingface" in str(path)
    )


def _find_real_payload_files(root: Path, patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        files.extend([p for p in root.rglob(pattern) if p.is_file() and not _is_metadata_only_file(p)])
    uniq = []
    seen = set()
    for item in files:
        if item in seen:
            continue
        seen.add(item)
        uniq.append(item)
    return uniq


def _find_downloading_artifacts(root: Path) -> list[Path]:
    artifacts = []
    for pattern in ("*.idmdownload", "*.aria2", "*.part", "*.lock"):
        artifacts.extend([p for p in root.rglob(pattern) if p.is_file()])
    return artifacts


def _evaluate_model_spec(spec: dict) -> dict:
    min_size_bytes = int(spec.get("min_size_mb", 0) * 1024 * 1024)
    paths = [Path(os.path.expanduser(p)) for p in spec.get("paths", [])] or [Path(os.path.expanduser(spec["path"]))]
    patterns = spec.get("patterns", ["*.safetensors", "*.bin", "*.pt", "*.gguf", "*.onnx"])
    best = {
        "name": spec["name"],
        "group": spec["group"],
        "critical": bool(spec.get("critical", False)),
        "status": "missing",
        "path": str(paths[0]),
        "detail": "未发现可用文件",
        "size_bytes": 0,
    }

    for path in paths:
        kind = spec["kind"]
        if kind in {"file", "any"} and path.exists() and path.is_file():
            size = path.resolve().stat().st_size if path.is_symlink() else path.stat().st_size
            if size >= min_size_bytes:
                return {
                    **best,
                    "status": "ready",
                    "path": str(path),
                    "detail": f"文件可用 · {_human_size(size)}",
                    "size_bytes": size,
                }
        if kind == "file":
            downloads = _find_downloading_artifacts(path.parent) if path.parent.exists() else []
            related = [p for p in downloads if path.stem in p.name or path.name in p.name]
            if related:
                size = sum(p.stat().st_size for p in related if p.exists())
                best = {
                    **best,
                    "status": "downloading",
                    "path": str(path.parent),
                    "detail": f"下载中 {len(related)} 项 · {_human_size(size)}",
                    "size_bytes": size,
                }
        else:
            if path.exists() and path.is_dir():
                payloads = _find_real_payload_files(path, patterns)
                if payloads:
                    size = sum(p.stat().st_size for p in payloads if p.exists())
                    return {
                        **best,
                        "status": "ready",
                        "path": str(path),
                        "detail": f"目录就绪 · {len(payloads)} 个主文件 · {_human_size(size)}",
                        "size_bytes": size,
                    }
                downloads = _find_downloading_artifacts(path)
                if downloads:
                    size = sum(p.stat().st_size for p in downloads if p.exists())
                    best = {
                        **best,
                        "status": "downloading",
                        "path": str(path),
                        "detail": f"目录下载中 · {len(downloads)} 个分片 · {_human_size(size)}",
                        "size_bytes": size,
                    }
                elif any(path.rglob("*")):
                    best = {
                        **best,
                        "status": "metadata",
                        "path": str(path),
                        "detail": "仅有 README / cache / metadata，暂无主权重",
                        "size_bytes": 0,
                    }
        if spec["kind"] == "any" and best["status"] == "ready":
            return best
    return best


def collect_model_audit() -> list[dict]:
    return [_evaluate_model_spec(spec) for spec in MODEL_AUDIT_SPECS]


def format_model_audit_markdown() -> str:
    entries = collect_model_audit()
    icon = {
        "ready": "✅",
        "downloading": "⏳",
        "metadata": "⚠️",
        "missing": "❌",
    }
    summary = {
        "ready": sum(1 for e in entries if e["status"] == "ready"),
        "downloading": sum(1 for e in entries if e["status"] == "downloading"),
        "metadata": sum(1 for e in entries if e["status"] == "metadata"),
        "missing": sum(1 for e in entries if e["status"] == "missing"),
    }
    lines = [
        "### 🧱 模型资产审计",
        f"- 已可用: {summary['ready']}",
        f"- 下载中: {summary['downloading']}",
        f"- 空壳目录: {summary['metadata']}",
        f"- 真缺失: {summary['missing']}",
        "",
    ]
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        groups.setdefault(entry["group"], []).append(entry)
    for group, rows in groups.items():
        lines.append(f"#### {group}")
        for row in rows:
            critical = " [关键]" if row["critical"] else ""
            path_text = row["path"].replace(str(Path.home()), "~")
            lines.append(f"- {icon[row['status']]} **{row['name']}**{critical}")
            lines.append(f"  路径: `{path_text}`")
            lines.append(f"  状态: {row['detail']}")
    return "\n".join(lines)


def format_industrial_sop_markdown() -> str:
    return "\n".join([
        "### 🏭 工业化 SOP",
        "1. 模型资产审计：先保证 `Wan2.2-TI2V`、Wan 编码器、ACE-Step、InstantID 四组关键资产可用。",
        "2. 内容生成：优先用 `🔥 一键全流程生成` 生成剧本、角色、场景、音乐、音效和分镜。",
        "3. 分镜审校：在 `🎞️ 分镜` Tab 按 shot 审核，通过后自动锁定，退回的 shot 直接重跑。",
        "4. 批量渲染：优先使用 `🚀 全量渲染+导出`，中途中断时改用 `♻️ 断点续跑`。",
        "5. 音频与合成：让统一音频管线自动跑 TTS/BGM/SFX，再由统一合成管线输出成片。",
        "6. 交付留痕：导出后保留导出清单、字幕修订和 shot 审核历史，避免返工时丢上下文。",
    ])


def format_industrial_console(pid: int) -> tuple[str, str, str]:
    entries = collect_model_audit()
    critical_missing = [e for e in entries if e["critical"] and e["status"] != "ready"]
    critical_ready = [e for e in entries if e["critical"] and e["status"] == "ready"]

    try:
        from pipelines.render_pipeline import get_dispatcher, load_pipeline_config
        cfg = load_pipeline_config()
        active_pipeline = cfg.get("active_pipeline", "")
        matrix = get_dispatcher().capability_matrix()
        pipeline_state = matrix.get(active_pipeline, {})
        pipeline_text = (
            f"- 活跃管线: `{active_pipeline}`\n"
            f"- 管线可用: {'是' if pipeline_state.get('available') else '否'}\n"
        )
    except Exception as e:
        active_pipeline = ""
        pipeline_text = f"- 管线状态读取失败: `{str(e)[:120]}`\n"

    if not pid:
        next_action = "先创建或加载项目，然后执行 `🔥 一键全流程生成`。"
        project_text = "- 当前未加载项目"
    else:
        proj = get_project(int(pid))
        stage = _stage_status(int(pid))
        shots = list_shots(project_id=int(pid))
        ready = sum(1 for s in shots if s.status == "ready")
        rendered = sum(1 for s in shots if s.status == "rendered")
        approved = sum(1 for s in shots if s.status == "approved")
        qc_failed = sum(1 for s in shots if s.status == "qc_failed")
        if not stage.get("story"):
            next_action = "先补齐内容阶段，优先执行 `🔥 一键全流程生成`。"
        elif not stage.get("shots"):
            next_action = "内容已生成但分镜未规划，运行 `步骤5: 分镜` 或重新全流程生成。"
        elif approved < len(shots) and len(shots) > 0:
            next_action = "进入 `🎞️ 分镜` Tab 批量审核并锁定关键 shot，再启动全量渲染。"
        elif rendered < len(shots):
            next_action = "使用 `🚀 全量渲染+导出`；若中断，改用 `♻️ 断点续跑`。"
        elif qc_failed > 0:
            next_action = "先处理 `qc_failed` 的 shot，再重新批量渲染。"
        else:
            next_action = "素材基本就绪，可直接做合成导出或抽查成片质量。"
        project_text = "\n".join([
            f"- 当前项目: `{proj.name if proj else pid}`",
            f"- 分镜数: {len(shots)}",
            f"- 待渲染: {ready}",
            f"- 已渲染: {rendered}",
            f"- 已通过审核: {approved}",
            f"- 质检失败: {qc_failed}",
        ])

    ops_md = "\n".join([
        "### 🎛️ 工业化总控",
        project_text,
        pipeline_text.rstrip(),
        f"- 关键模型就绪: {len(critical_ready)}/{len(critical_ready) + len(critical_missing)}",
        f"- 当前建议: {next_action}",
        "",
        "#### 快捷入口",
        "- `🔥 一键全流程生成`：从故事到分镜一口气完成。",
        "- `🚀 全量渲染+导出`：统一跑渲染、音频、合成、导出。",
        "- `♻️ 断点续跑`：项目中断后只补未完成的 shot 和阶段。",
        "- `🎞️ 分镜` Tab：审核、锁定、退回、重渲染都在这里闭环。",
    ])

    missing_lines = ["### 🚨 当前瓶颈"]
    if critical_missing:
        for item in critical_missing:
            missing_lines.append(f"- `{item['name']}`: {item['detail']}")
    else:
        missing_lines.append("- 关键生产模型已就绪，当前可按工业化流程推进。")
    return ops_md, "\n".join(missing_lines), format_industrial_sop_markdown()


def load_industrial_dashboard(pid: int) -> tuple[str, str, str, str]:
    ops_md, bottleneck_md, sop_md = format_industrial_console(int(pid or 0))
    audit_md = format_model_audit_markdown()
    return ops_md, bottleneck_md, audit_md, sop_md


# ─── ComfyUI 启动 ─────────────────────────────────────

_comfyui_proc = None


def _comfyui_status_text() -> str:
    online = comfyui_online()
    return "🟢 ComfyUI 在线" if online else "🔴 ComfyUI 离线"


def launch_comfyui() -> str:
    global _comfyui_proc
    if comfyui_online():
        return "✅ ComfyUI 已在运行"
    main_py = comfyui_main_py()
    if not main_py.exists():
        return f"❌ 未找到 {main_py}"
    python_exe = resolve_comfyui_python()
    if not python_exe.exists():
        return f"❌ 未找到 ComfyUI 专用 Python: {python_exe}"
    try:
        import subprocess
        _comfyui_proc = subprocess.Popen(
            [str(python_exe), str(main_py), "--listen", "127.0.0.1", "--port", "8188"],
            cwd=str(COMFYUI_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time
        for _ in range(12):
            time.sleep(2.5)
            if comfyui_online():
                return "✅ ComfyUI 启动成功（PID: %d）" % _comfyui_proc.pid
        return "⏳ ComfyUI 正在启动，请稍后刷新状态..."
    except Exception as e:
        return f"❌ 启动失败: {e}"


# ─── 构建 UI ─────────────────────────────────────────

def build_ui():
    init_db()
    models = get_ollama_models()
    default_model = models[0] if models else "qwen2.5:7b"

    with gr.Blocks(title="🎬 漫剧故事工坊") as app:
        app.queue(default_concurrency_limit=5)

        gr.Markdown("# 🎬 漫剧故事工坊")
        gr.Markdown("**两步走**: ① 生成全部内容（可编辑） → ② 渲染导出成片")

        # ══════ 已有项目选择 ═══════════════════════
        with gr.Row():
            proj_dropdown = gr.Dropdown(
                label="📂 加载已有项目",
                choices=get_project_choices(),
                value=None,
                allow_custom_value=False,
                scale=4,
            )
            proj_refresh_btn = gr.Button("🔄", scale=1, size="sm", min_width=60)
            proj_load_btn = gr.Button("📂 加载", variant="primary", scale=1, min_width=80)
            proj_delete_btn = gr.Button("🗑️ 删除项目", variant="stop", scale=1, min_width=100)
        proj_action_status = gr.Markdown("")

        # ══════ Phase 1: 内容生成 ═══════════════════
        gr.Markdown("## 📝 Phase 1: 内容生成")

        premise = gr.Textbox(label="创作构想", lines=6, placeholder="输入故事创意...")

        with gr.Row():
            project_name = gr.Textbox(label="项目名称", placeholder="留空自动生成", scale=1)
            genre = gr.Dropdown(label="类型",
                choices=["玄幻","仙侠","都市","科幻","奇幻","武侠","历史","悬疑","恐怖","言情","校园","末日"],
                value="玄幻", scale=1)
            tone = gr.Dropdown(label="基调",
                choices=["热血","温馨","黑暗","搞笑","治愈","悬疑","史诗","浪漫","轻松","沉重"],
                value="热血", scale=1)
            acts = gr.Slider(label="幕数", minimum=1, maximum=5, value=3, step=1, scale=1)

        # 全局默认模型 + 各阶段独立模型
        with gr.Row():
            model = gr.Dropdown(label="全局默认模型", choices=models or ["qwen2.5:7b"],
                value=default_model, allow_custom_value=True, scale=2)
        model_profile_md = gr.Markdown(format_model_profile(default_model))
        model.change(fn=format_model_profile, inputs=[model], outputs=[model_profile_md])

        with gr.Accordion("🎭 各阶段独立模型 & 分步运行", open=False):
            gr.Markdown("各阶段留空则使用全局模型。可对已有项目单独运行某一步骤（填入项目 ID）。")
            with gr.Row():
                story_model = gr.Dropdown(label="📝 步骤1 剧本", choices=models or [default_model],
                    value="", allow_custom_value=True, scale=1)
                char_model = gr.Dropdown(label="👤 步骤2 角色", choices=models or [default_model],
                    value="", allow_custom_value=True, scale=1)
                scene_model = gr.Dropdown(label="🏞️ 步骤3 场景", choices=models or [default_model],
                    value="", allow_custom_value=True, scale=1)
                art_model = gr.Dropdown(label="🎨 步骤4 美术/音乐", choices=models or [default_model],
                    value="", allow_custom_value=True, scale=1)

            with gr.Row():
                step1_btn = gr.Button("📝 步骤1: 剧本", scale=1)
                step2_btn = gr.Button("👤 步骤2: 角色", scale=1)
                step3_btn = gr.Button("🏞️ 步骤3: 场景", scale=1)
                step4_btn = gr.Button("🎨 步骤4: 美术/音乐/音效", scale=1)
                step5_btn = gr.Button("🎞️ 步骤5: 分镜", scale=1)

            with gr.Row():
                stage_status_btn = gr.Button("🔍 查看阶段状态", size="sm", scale=1)
            stage_status_md = gr.Markdown("", label="阶段状态")

        with gr.Row():
            gen_btn = gr.Button("🔥 一键全流程生成", variant="primary", size="lg", scale=2)
            clear_btn = gr.Button("🗑️ 清空", size="lg", scale=1)

        gen_log = gr.Markdown("### 📋 管线日志\n等待启动...")
        gen_results = gr.JSON(value=None, label="生成结果摘要")

        # ══════ Phase 2: 渲染导出 ═══════════════════
        gr.Markdown("---")
        gr.Markdown("## 🎬 Phase 2: 渲染 + 导出")
        gr.Markdown("用数据库中最新内容渲染视频。可先编辑内容再执行。")

        # ── ComfyUI 状态行 ──
        with gr.Row():
            comfyui_status_md = gr.Markdown(_comfyui_status_text())
            comfyui_launch_btn = gr.Button("🚀 启动 ComfyUI", scale=1, size="sm")
            comfyui_refresh_btn = gr.Button("🔄 刷新状态", scale=1, size="sm")
        comfyui_launch_log = gr.Markdown("", visible=False)

        # ── 管线选择 & 状态 ──
        pipeline_choices, active_pipeline_name = _pipeline_choices()
        with gr.Accordion("🔧 渲染管线控制", open=True):
            with gr.Row():
                pipeline_selector_dd = gr.Dropdown(
                    choices=pipeline_choices, value=active_pipeline_name,
                    label="活跃管线", scale=3, interactive=True,
                )
                pipeline_detect_btn = gr.Button("🔍 检测缺失", size="sm", scale=1, min_width=80)
                pipeline_download_btn = gr.Button("⬇️ 一键下载", size="sm", scale=1, min_width=80,
                                                  variant="primary")
            pipeline_status_card_md = gr.Markdown(
                _pipeline_status_card(active_pipeline_name)
            )
            pipeline_detect_log = gr.Markdown("", visible=False)

        # ── 渲染操作按钮 ──
        with gr.Row():
            render_btn = gr.Button("🚀 全量渲染+导出", variant="secondary", size="lg",
                                   elem_classes="gr-button-secondary", scale=2)
            resume_btn = gr.Button("♻️ 断点续跑", variant="primary", size="lg", scale=2)
            pipeline_state_btn = gr.Button("📊 查看状态", size="lg", scale=1)

        gr.Markdown("`全量渲染+导出`：从头检查并执行完整 Phase 2。`断点续跑`：跳过已完成 shot，仅补未完成阶段。`渲染工作站`：只跑指定 shot。")
        render_log = gr.Markdown("点击「全量渲染+导出」开始，或点击「断点续跑」从断点续跑...")
        render_results = gr.JSON(value=None, label="渲染结果")
        pipeline_state_md = gr.Markdown("", label="管线状态")

        # ══════ 状态变量 ════════════════════════════
        project_id_state = gr.State(0)
        render_config_state = gr.State({})   # {checkpoint, loras, width, height, steps, cfg}

        # ══════ 查看 + 编辑区（各步骤工作站）════════════
        gr.Markdown("---")
        gr.Markdown("## ✏️ 各步骤工作站  *每个 Tab 可独立编辑 & 单步执行*")
        production_overview = gr.Markdown("运行管线后自动展示生产指标。")
        industrial_ops_default = "### 🎛️ 工业化总控\n先加载项目或生成内容。"
        industrial_bottleneck_default = "### 🚨 当前瓶颈\n等待审计。"
        industrial_model_audit_default = format_model_audit_markdown()
        industrial_sop_default = format_industrial_sop_markdown()

        with gr.Tabs():
            with gr.TabItem("🏭 工业化控制台"):
                gr.Markdown("### 面向批量生产的总控面板\n把模型资产、阶段状态、推荐动作和快捷入口放到一个界面。")
                with gr.Row():
                    industrial_refresh_btn = gr.Button("🔄 刷新控制台", variant="primary", scale=1)
                    industrial_refresh_models_btn = gr.Button("🧱 只刷新模型审计", scale=1)
                with gr.Row():
                    with gr.Column(scale=1):
                        industrial_ops_card = gr.Markdown(value=industrial_ops_default)
                        industrial_bottleneck_card = gr.Markdown(value=industrial_bottleneck_default)
                    with gr.Column(scale=1):
                        industrial_model_audit_card = gr.Markdown(value=industrial_model_audit_default)
                        industrial_sop_card = gr.Markdown(value=industrial_sop_default)

            # ─── 概览 ──────────────────────────────
            with gr.TabItem("📺 概览"):
                view_md = gr.Markdown(value="运行管线后自动展示可读内容。")

            # ─── 分镜 ──────────────────────────────
            with gr.TabItem("🎞️ 分镜"):
                shot_table = gr.Dataframe(
                    headers=["ID", "Act", "Scene", "Shot", "场景", "镜头", "情绪", "角色", "状态", "管线", "回退", "音频", "锁定"],
                    value=[], interactive=False, label="分镜列表",
                )
                with gr.Row():
                    shot_action_id = gr.Textbox(label="Shot ID", placeholder="例如 12", scale=1)
                    shot_review_note = gr.Textbox(label="审核备注", placeholder="例如：镜头节奏通过 / 角色口型不对", scale=3)
                with gr.Row():
                    shot_load_btn = gr.Button("📥 载入 Shot", scale=1)
                    shot_approve_btn = gr.Button("✅ 通过", scale=1)
                    shot_reject_btn = gr.Button("↩️ 退回", scale=1)
                    shot_lock_btn = gr.Button("🔒 锁定", scale=1)
                    shot_unlock_btn = gr.Button("🔓 解锁", scale=1)
                shot_auto_lock_on_approve = gr.Checkbox(label="审核通过后自动锁定", value=True)
                gr.Markdown("##### 结构化分镜工位")
                shot_form_status_md = gr.Markdown("输入 Shot ID 后点击“载入 Shot”进行结构化编辑。")
                shot_form_id = gr.Number(label="Shot 内部 ID", value=0, precision=0, visible=False)
                with gr.Row():
                    shot_form_act = gr.Number(label="Act", value=1, precision=0, scale=1)
                    shot_form_scene = gr.Number(label="Scene", value=1, precision=0, scale=1)
                    shot_form_number = gr.Number(label="Shot", value=1, precision=0, scale=1)
                    shot_form_status = gr.Dropdown(
                        label="状态",
                        choices=["ready", "rendered", "approved", "rejected"],
                        value="ready",
                        scale=1,
                    )
                    shot_form_locked = gr.Checkbox(label="锁定", value=False, scale=1)
                with gr.Row():
                    shot_form_location = gr.Textbox(label="场景地点", scale=2)
                    shot_form_type = gr.Dropdown(
                        label="镜头类型",
                        choices=["特写", "近景", "中景", "全景", "远景", "俯拍", "仰拍", "跟拍"],
                        value="中景",
                        scale=1,
                    )
                    shot_form_mood = gr.Textbox(label="情绪", scale=1)
                with gr.Row():
                    shot_form_time = gr.Dropdown(
                        label="时间",
                        choices=["清晨", "白天", "黄昏", "夜晚"],
                        value="白天",
                        scale=1,
                    )
                    shot_form_weather = gr.Dropdown(
                        label="天气",
                        choices=["晴", "阴", "雨", "雪", "雾"],
                        value="晴",
                        scale=1,
                    )
                shot_form_characters = gr.Textbox(
                    label="角色列表 JSON",
                    lines=2,
                    placeholder='["主角", "反派"]',
                )
                shot_form_narration = gr.Textbox(label="旁白 / 镜头描述", lines=3)
                shot_form_camera_notes = gr.Textbox(label="机位 / 运镜备注", lines=2)
                shot_form_payload = gr.Textbox(
                    label="Render Payload JSON",
                    lines=8,
                    placeholder="高级模式：需要时再编辑底层 render payload",
                )
                with gr.Row():
                    shot_form_save_btn = gr.Button("💾 保存结构化分镜", elem_classes="save-btn", scale=1)
                    shot_rerender_btn = gr.Button("🎬 重渲染当前 Shot", variant="primary", scale=1)
                    shot_rework_btn = gr.Button("🔁 退回并重跑", scale=1)
                shot_edit = gr.Textbox(
                    label="分镜 JSON（高级模式）",
                    lines=14,
                )
                with gr.Row():
                    shot_reload_btn = gr.Button("🔄 载入分镜 JSON", scale=1)
                    save_shot_btn = gr.Button("💾 保存分镜", elem_classes="save-btn", scale=1)
                    shot_status = gr.Markdown("")
                shot_render_log = gr.Markdown("")
                shot_render_preview = gr.Video(
                    label="当前 Shot 渲染预览", interactive=False,
                )
                shot_review_history_md = gr.Markdown("输入 Shot ID 查看审核历史。")

            # ─── 剧本工作站 ───────────────────────────
            with gr.TabItem("📖 剧本"):
                with gr.Row():
                    gr.Markdown("##### 状态")
                    script_step_refresh_btn = gr.Button("🔄", size="sm", scale=0, min_width=40)
                script_step_status = gr.Markdown("加载项目后显示状态")
                script_edit = gr.Textbox(label="剧本 JSON（可直接编辑）", lines=15)
                with gr.Accordion("🤖 AI 辅助", open=False):
                    with gr.Row():
                        script_ai_instr = gr.Textbox(
                            label="优化指令（留空用默认）",
                            placeholder="例如：加强第二幕的冲突感，让主角更有深度",
                            lines=2, scale=3,
                        )
                        script_ai_model = gr.Dropdown(
                            label="AI 模型", choices=models or [default_model],
                            value="", allow_custom_value=True, scale=1,
                        )
                    script_ai_btn = gr.Button("🤖 AI 优化剧本", variant="secondary")
                    script_ai_status = gr.Markdown("")
                with gr.Row():
                    save_script_btn = gr.Button("💾 保存剧本", elem_classes="save-btn", scale=1)
                    step1_run_btn = gr.Button("▶️ 重新生成剧本", scale=1)
                    script_status = gr.Markdown("")

            # ─── 角色工作站 ───────────────────────────
            with gr.TabItem("👤 角色"):
                char_edit = gr.Textbox(label="角色列表 JSON（可直接编辑）", lines=12)
                with gr.Accordion("🤖 AI 辅助", open=False):
                    with gr.Row():
                        char_ai_instr = gr.Textbox(
                            label="优化指令",
                            placeholder="例如：让反派角色更有魅力，丰富支线角色背景",
                            lines=2, scale=3,
                        )
                        char_ai_model = gr.Dropdown(
                            label="AI 模型", choices=models or [default_model],
                            value="", allow_custom_value=True, scale=1,
                        )
                    char_ai_btn = gr.Button("🤖 AI 优化角色", variant="secondary")
                    char_ai_status = gr.Markdown("")
                with gr.Row():
                    save_char_btn = gr.Button("💾 保存角色", elem_classes="save-btn", scale=1)
                    step2_run_btn = gr.Button("▶️ 重新生成角色", scale=1)
                    char_status = gr.Markdown("")

            # ─── 场景工作站 ───────────────────────────
            with gr.TabItem("🏞️ 场景"):
                scene_edit = gr.Textbox(label="场景列表 JSON（可直接编辑）", lines=12)
                with gr.Accordion("🤖 AI 辅助", open=False):
                    with gr.Row():
                        scene_ai_instr = gr.Textbox(
                            label="优化指令",
                            placeholder="例如：增强视觉冲击力，让场景描述更适合动漫风格渲染",
                            lines=2, scale=3,
                        )
                        scene_ai_model = gr.Dropdown(
                            label="AI 模型", choices=models or [default_model],
                            value="", allow_custom_value=True, scale=1,
                        )
                    scene_ai_btn = gr.Button("🤖 AI 优化场景", variant="secondary")
                    scene_ai_status = gr.Markdown("")
                with gr.Row():
                    save_scene_btn = gr.Button("💾 保存场景", elem_classes="save-btn", scale=1)
                    step3_run_btn = gr.Button("▶️ 重新生成场景", scale=1)
                    scene_status = gr.Markdown("")

            # ─── 配乐工作站 ───────────────────────────
            with gr.TabItem("🎵 配乐"):
                with gr.Row():
                    music_step_status = gr.Markdown(load_music_status(0))
                    music_status_refresh_btn = gr.Button("🔄 刷新", size="sm", scale=0, min_width=50)
                gr.Markdown("##### 编辑配乐数据（JSON）")
                gr.Markdown(
                    "每条记录包含 `name`、`mood`、`tempo`、`instruments`、`description`、`prompt_for_gen`。\n"
                    "`prompt_for_gen` 是实际传给 MusicGen 的英文描述，对音乐质量影响最大。",
                    elem_classes="gr-text-small",
                )
                music_edit = gr.Textbox(
                    label="配乐 JSON（可直接编辑 prompt_for_gen 字段）",
                    lines=12,
                )
                with gr.Accordion("🤖 AI 辅助优化配乐描述", open=False):
                    with gr.Row():
                        music_ai_instr = gr.Textbox(
                            label="优化指令（留空则自动优化 prompt_for_gen）",
                            placeholder="例如：让配乐更有史诗感，加入东方乐器元素",
                            lines=2, scale=3,
                        )
                        music_ai_model = gr.Dropdown(
                            label="AI 模型", choices=models or [default_model],
                            value="", allow_custom_value=True, scale=1,
                        )
                    music_ai_btn = gr.Button("🤖 AI 优化配乐描述", variant="secondary")
                    music_ai_status = gr.Markdown("")
                with gr.Row():
                    save_music_btn = gr.Button("💾 保存配乐数据", elem_classes="save-btn", scale=1)
                    music_run_btn = gr.Button("▶️ 单步生成配乐", variant="primary", scale=1)
                    music_status = gr.Markdown("")
                music_run_log = gr.Markdown("")
                music_preview_out = gr.Audio(
                    label="配乐预览（生成后自动显示首曲）",
                    type="filepath", interactive=False,
                )

            # ─── 音效工作站 ───────────────────────────
            with gr.TabItem("🔊 音效"):
                sfx_edit = gr.Textbox(label="音效数据 JSON（可直接编辑）", lines=10)
                with gr.Accordion("🤖 AI 辅助优化音效描述", open=False):
                    with gr.Row():
                        sfx_ai_instr = gr.Textbox(
                            label="优化指令",
                            placeholder="例如：让音效描述更具体，区分环境音和动作音",
                            lines=2, scale=3,
                        )
                        sfx_ai_model = gr.Dropdown(
                            label="AI 模型", choices=models or [default_model],
                            value="", allow_custom_value=True, scale=1,
                        )
                    sfx_ai_btn = gr.Button("🤖 AI 优化音效描述", variant="secondary")
                    sfx_ai_status = gr.Markdown("")
                with gr.Row():
                    save_sfx_btn = gr.Button("💾 保存音效数据", elem_classes="save-btn", scale=1)
                    sfx_run_btn = gr.Button("▶️ 单步生成音效", variant="primary", scale=1)
                    sfx_status = gr.Markdown("")
                sfx_run_log = gr.Markdown("")
                sfx_preview_out = gr.Audio(
                    label="音效预览（生成后自动显示）",
                    type="filepath", interactive=False,
                )

            # ─── TTS 工作站 ──────────────────────────
            with gr.TabItem("🎤 TTS 配音"):
                with gr.Row():
                    tts_step_status_md = gr.Markdown(load_tts_status(0))
                    tts_status_refresh_btn = gr.Button("🔄 刷新", size="sm", scale=0, min_width=50)
                gr.Markdown(
                    "可选择单个 Shot 查看/编辑对白，或直接批量生成全部 Shot 的 TTS。",
                    elem_classes="gr-text-small",
                )
                with gr.Row():
                    tts_shot_id_input = gr.Textbox(
                        label="Shot ID（留空 = 全部）",
                        placeholder="例如: 3  或  1,2,5",
                        scale=2,
                    )
                    tts_load_shot_btn = gr.Button("📖 查看此 Shot 对白", scale=1)
                tts_shot_status_md = gr.Markdown("")
                tts_dialogue_edit = gr.Textbox(
                    label="对白 JSON（可编辑 character / line / voice_preset 字段）",
                    lines=10, placeholder="点击「查看此 Shot 对白」加载...",
                )
                gr.Markdown(
                    "TTS 使用 **ChatTTS**（中文优先）→ Bark → Edge-TTS 自动降级。\n"
                    "在模型管理 → TTS 试听 可以预先试听各音色。",
                    elem_classes="gr-text-small",
                )
                with gr.Row():
                    tts_run_shot_btn = gr.Button("▶️ 生成此 Shot TTS", scale=1)
                    tts_run_all_btn = gr.Button("▶️ 生成全部 TTS", variant="primary", scale=1)
                tts_run_log = gr.Markdown("")
                tts_preview_out = gr.Audio(
                    label="TTS 预览（生成后自动显示首条）",
                    type="filepath", interactive=False,
                )

            # ─── 渲染工作站 ──────────────────────────
            with gr.TabItem("🎬 渲染"):
                with gr.Row():
                    render_step_status_md = gr.Markdown(load_render_status(0))
                    render_status_refresh_btn = gr.Button("🔄 刷新", size="sm", scale=0, min_width=50)
                gr.Markdown(
                    "渲染使用 ComfyUI（Wan2.2 TI2V）。已渲染的 Shot 自动跳过。",
                    elem_classes="gr-text-small",
                )
                with gr.Row():
                    render_shot_id_input = gr.Textbox(
                        label="Shot ID（留空 = 全部未渲染）",
                        placeholder="例如: 3  或  1,2,5",
                        scale=2,
                    )
                    render_run_btn = gr.Button("▶️ 渲染指定/全部", variant="primary", scale=1)
                render_run_log = gr.Markdown("")
                render_video_preview = gr.Video(
                    label="渲染预览（完成后自动显示）", interactive=False,
                )

            # ─── 合成工作站 ──────────────────────────
            with gr.TabItem("🎞️ 合成导出"):
                gr.Markdown(
                    "将所有已渲染视频 + TTS + BGM 合成为最终集数视频。\n"
                    "需要完成渲染和音频生成后再执行。",
                    elem_classes="gr-text-small",
                )
                with gr.Row():
                    composite_run_btn = gr.Button("▶️ 执行合成", variant="primary", scale=1)
                    episode_video_path = gr.Textbox(
                        label="输出视频路径", interactive=False, scale=2,
                    )
                composite_run_log = gr.Markdown("")
                export_manifest_md = gr.Markdown("")
                composite_video_preview = gr.Video(
                    label="最终视频预览", interactive=False,
                )

            # ─── 字幕工作站 ──────────────────────────
            with gr.TabItem("💬 字幕"):
                gr.Markdown("输入 Shot ID 生成/读取字幕，可直接编辑 `.srt` 文本后保存。")
                with gr.Row():
                    subtitle_shot_id = gr.Textbox(label="Shot ID", placeholder="例如 12", scale=1)
                    subtitle_load_btn = gr.Button("📖 加载字幕", scale=1)
                    subtitle_save_btn = gr.Button("💾 保存字幕", elem_classes="save-btn", scale=1)
                subtitle_path_md = gr.Markdown("")
                subtitle_text = gr.Textbox(label="字幕 SRT 文本", lines=16)
                subtitle_status = gr.Markdown("")

            # ─── 视频预览 ─────────────────────────────
            with gr.TabItem("▶️ 视频预览"):
                gr.Markdown("### Shot 视频预览")
                with gr.Row():
                    shot_preview_id = gr.Number(label="Shot ID", value=0, precision=0, scale=1)
                    load_video_btn = gr.Button("▶️ 加载视频", scale=1)
                shot_video_player = gr.Video(label="Shot 视频", interactive=False)
                shot_video_status = gr.Markdown("")

            # ─── AI 联动编辑 ──────────────────────────
            with gr.TabItem("🤖 AI 编辑"):
                gr.Markdown("### AI 联动编辑\n输入自然语言指令，AI 扫描所有受影响字段并预览变更。")
                with gr.Row():
                    ai_edit_instruction = gr.Textbox(
                        label="编辑指令",
                        placeholder="例如: 把张三改名为李四，性格改为冷漠",
                        lines=2, scale=3,
                    )
                    ai_scan_btn = gr.Button("🔍 AI 扫描预览", variant="primary", scale=1)
                ai_edit_preview_md = gr.Markdown("输入指令后点击「AI 扫描预览」")
                ai_manifest_json = gr.Textbox(
                    label="变更清单 JSON（可手动调整后执行）",
                    lines=8, visible=False,
                )
                with gr.Row():
                    ai_exec_btn = gr.Button("✅ 确认执行变更", variant="secondary", scale=1)
                    ai_rollback_btn = gr.Button("↩️ 回滚最近编辑", scale=1)
                    show_manifest_btn = gr.Button("📋 显示/隐藏 JSON", scale=1)
                ai_exec_status = gr.Markdown("")
                gr.Markdown("#### 编辑历史")
                edit_history_table = gr.Dataframe(
                    headers=["ID", "时间", "指令", "表", "字段", "旧值", "新值", "置信度"],
                    value=[], interactive=False, label="近 30 条编辑记录",
                )
                refresh_history_btn = gr.Button("🔄 刷新历史", size="sm")

            with gr.TabItem("🗂️ 模型管理"):
                gr.Markdown("### ComfyUI 模型管理\n查看已安装模型 / 搜索 / 下载缺失模型。")

                # ── 系统状态总览 ─────────────────────────
                with gr.Accordion("🖥️ 系统状态总览", open=True):
                    sys_status_md = gr.Markdown(get_system_status())
                    sys_refresh_btn = gr.Button("🔄 刷新状态", size="sm")

                # ── 渲染参数配置 ─────────────────────────
                with gr.Accordion("⚙️ 渲染参数（Steps / CFG / 尺寸）", open=False):
                    gr.Markdown("调整后点「应用」将参数合并到渲染配置。")
                    with gr.Row():
                        rp_steps = gr.Slider(label="Steps", minimum=10, maximum=50, value=20, step=1, scale=2)
                        rp_cfg   = gr.Slider(label="CFG Scale", minimum=3.0, maximum=15.0, value=7.0, step=0.5, scale=2)
                    with gr.Row():
                        rp_width  = gr.Dropdown(label="宽度", choices=[512, 768, 832, 896, 1024, 1152], value=896, scale=1)
                        rp_height = gr.Dropdown(label="高度", choices=[512, 768, 832, 896, 1024, 1152], value=1152, scale=1)
                        rp_apply_btn = gr.Button("✅ 应用参数", variant="secondary", scale=1)
                    rp_status_md = gr.Markdown("")

                # ── TTS / 音频配置 ───────────────────────
                with gr.Accordion("🎤 TTS 配置 & 试听", open=False):
                    gr.Markdown(
                        "TTS 后端优先级：**ChatTTS**（本地，中文主力）"
                        " → Edge-TTS → Bark → Kokoro → pyttsx3。\n\n"
                        "ChatTTS 使用本机独立 venv 与本地模型目录；失败时才会自动回退。"
                    )
                    with gr.Row():
                        tts_test_text  = gr.Textbox(
                            label="试听文本",
                            value="仙剑问情，一梦千年，何处是归途？",
                            scale=3,
                        )
                        tts_voice_type = gr.Dropdown(
                            label="音色类型",
                            choices=["男", "女", "男孩", "女孩", "旁白"],
                            value="旁白", scale=1,
                        )
                        tts_preview_btn = gr.Button("▶️ 试听", variant="primary", scale=1)
                    tts_audio_out = gr.Audio(label="TTS 试听", type="filepath", interactive=False)
                    tts_preview_log = gr.Markdown("")

                # ── BGM 配置 & 试听 ──────────────────────
                with gr.Accordion("🎵 BGM 配置 & 试听", open=False):
                    gr.Markdown(
                        "BGM 后端优先级：**Ace-Step 1.5**（ComfyUI，模型就绪时）→ ffmpeg 合成兜底。\n\n"
                        "如果本机未安装 Ace-Step 音频模型，系统会自动改用本地 ffmpeg 合成，不会把整条音频链跑死。"
                    )
                    with gr.Row():
                        bgm_mood_sel = gr.Dropdown(
                            label="情绪",
                            choices=["热血", "史诗", "神秘", "温馨", "黑暗", "浪漫", "悬疑", "epic", "warm", "dark"],
                            value="热血", scale=2,
                        )
                        bgm_dur_sel = gr.Slider(label="时长(秒)", minimum=5, maximum=60, value=15, step=5, scale=2)
                        bgm_preview_btn = gr.Button("▶️ 试听 BGM", variant="primary", scale=1)
                    bgm_audio_out = gr.Audio(label="BGM 试听", type="filepath", interactive=False)
                    bgm_preview_log = gr.Markdown("")

                # ── 已安装模型浏览 ──────────────────────
                with gr.Accordion("🔍 已安装模型浏览", open=False):
                    cm_status_md = gr.Markdown("点击「加载」查询 ComfyUI 已安装模型。")
                    with gr.Row():
                        cm_load_btn = gr.Button("🔄 加载全部模型", variant="primary", scale=2)
                        cm_type_filter = gr.Dropdown(
                            label="类型筛选",
                            choices=list(MODEL_TYPE_LABELS.keys()),
                            value="checkpoint", scale=1,
                        )
                        cm_search_input = gr.Textbox(
                            label="搜索关键词", placeholder="输入关键词过滤...",
                            scale=2,
                        )
                        cm_search_btn = gr.Button("🔍 搜索", scale=1)

                    with gr.Row():
                        cm_ckpt_list  = gr.Dropdown(label="Checkpoint", choices=[], allow_custom_value=True, scale=1)
                        cm_lora_list  = gr.Dropdown(label="LoRA",        choices=[], allow_custom_value=True, scale=1)
                        cm_vae_list   = gr.Dropdown(label="VAE",         choices=[], allow_custom_value=True, scale=1)
                        cm_cn_list    = gr.Dropdown(label="ControlNet",  choices=[], allow_custom_value=True, scale=1)

                    cm_search_result = gr.Dropdown(
                        label="搜索结果（选择后可应用）", choices=[], interactive=True,
                    )

                    # LoRA 强度 + 应用配置
                    with gr.Row():
                        cm_lora_strength = gr.Slider(label="LoRA 强度", minimum=0.0, maximum=1.5,
                                                      value=0.7, step=0.05, scale=2)
                        cm_apply_btn = gr.Button("✅ 应用到渲染配置", variant="secondary", scale=1)
                    cm_active_config_md = gr.Markdown("当前渲染配置：使用默认值")

                # ── 下载缺失模型 ────────────────────────
                with gr.Accordion("📥 下载缺失模型", open=False):
                    gr.Markdown(
                        "支持以下格式：\n"
                        "- **直链 URL**: `https://huggingface.co/.../resolve/main/xxx.safetensors`\n"
                        "- **HuggingFace 路径**: `username/repo-name/path/to/file.safetensors`\n"
                        "- **HF 简写**: `hf:username/repo@filename.safetensors`\n\n"
                        "下载完成后需要**重启 ComfyUI** 才能在工作流中使用。"
                    )
                    with gr.Row():
                        dl_source = gr.Textbox(
                            label="来源 URL / HuggingFace 路径",
                            placeholder="如: stabilityai/stable-diffusion-xl-base-1.0/sd_xl_base_1.0.safetensors",
                            scale=3,
                        )
                        dl_type = gr.Dropdown(
                            label="模型类型",
                            choices=list(MODEL_TYPE_LABELS.keys()),
                            value="lora", scale=1,
                        )
                    with gr.Row():
                        dl_filename = gr.Textbox(
                            label="保存文件名（留空从 URL 自动提取）",
                            placeholder="my_lora.safetensors", scale=2,
                        )
                        dl_check_btn = gr.Button("🔎 检查是否已存在", scale=1)
                        dl_btn = gr.Button("⬇️ 开始下载", variant="primary", scale=1)
                    dl_check_status = gr.Markdown("")
                    dl_log = gr.Textbox(
                        label="下载日志", lines=6, interactive=False,
                    )

        # ══════ 事件绑定 ═════════════════════════════

        # 项目加载
        _load_proj_outputs = [
            gen_log, gen_results,
            view_md,
            script_edit, char_edit, scene_edit, music_edit, sfx_edit,
            production_overview, shot_table,
            project_id_state,
            music_step_status, tts_step_status_md, render_step_status_md,
            shot_edit, subtitle_text, subtitle_path_md,
        ]
        proj_load_btn.click(
            fn=load_existing_project,
            inputs=[proj_dropdown],
            outputs=_load_proj_outputs,
            queue=False,
        ).then(
            fn=load_industrial_dashboard,
            inputs=[project_id_state],
            outputs=[industrial_ops_card, industrial_bottleneck_card, industrial_model_audit_card, industrial_sop_card],
            queue=False,
        )
        proj_refresh_btn.click(
            fn=lambda: gr.update(choices=get_project_choices()),
            inputs=[],
            outputs=[proj_dropdown],
            queue=False,
        )
        proj_delete_btn.click(
            fn=delete_project_with_outputs,
            inputs=[proj_dropdown],
            outputs=[proj_dropdown, proj_action_status],
            queue=False,
        )

        # ComfyUI 启动
        comfyui_launch_btn.click(
            fn=lambda: (gr.update(visible=True, value="⏳ 正在启动..."),),
            inputs=[],
            outputs=[comfyui_launch_log],
            queue=False,
        ).then(
            fn=lambda: (gr.update(value=launch_comfyui()), _comfyui_status_text()),
            inputs=[],
            outputs=[comfyui_launch_log, comfyui_status_md],
        )
        comfyui_refresh_btn.click(
            fn=_comfyui_status_text,
            inputs=[],
            outputs=[comfyui_status_md],
            queue=False,
        )

        # Phase 1: 全流程生成
        gen_outputs = [
            gen_log, gen_results,
            view_md,
            script_edit, char_edit, scene_edit, music_edit, sfx_edit,
            production_overview, shot_table,
            shot_edit, subtitle_text, subtitle_path_md,
            project_id_state,
        ]
        gen_btn.click(
            fn=full_pipeline_flow,
            inputs=[premise, project_name, genre, tone, acts, model,
                    story_model, char_model, scene_model, art_model],
            outputs=gen_outputs,
            concurrency_limit=2,
        ).then(
            fn=load_industrial_dashboard,
            inputs=[project_id_state],
            outputs=[industrial_ops_card, industrial_bottleneck_card, industrial_model_audit_card, industrial_sop_card],
            queue=False,
        )

        # Phase 1: 分步运行
        _step_outputs = [gen_log, gen_results, project_id_state]

        step1_btn.click(
            fn=story_stage_flow,
            inputs=[project_id_state, premise, project_name, genre, tone, acts,
                    story_model, model],
            outputs=_step_outputs,
            concurrency_limit=2,
        )
        step2_btn.click(
            fn=chars_stage_flow,
            inputs=[project_id_state, char_model, model],
            outputs=_step_outputs,
            concurrency_limit=2,
        )
        step3_btn.click(
            fn=scenes_stage_flow,
            inputs=[project_id_state, scene_model, model],
            outputs=_step_outputs,
            concurrency_limit=2,
        )
        step4_btn.click(
            fn=art_music_stage_flow,
            inputs=[project_id_state, art_model, model],
            outputs=_step_outputs,
            concurrency_limit=2,
        )
        step5_btn.click(
            fn=shots_stage_flow,
            inputs=[project_id_state],
            outputs=_step_outputs,
            concurrency_limit=2,
        )

        # 阶段状态查询
        stage_status_btn.click(
            fn=get_stage_status,
            inputs=[project_id_state],
            outputs=[stage_status_md],
            queue=False,
        )

        # ── 工作站：各步骤 AI 辅助 + 单步运行 ─────────────────

        # 剧本 AI 辅助
        script_ai_btn.click(
            fn=lambda pid, content, instr, mdl: ai_enhance_step(pid, "script", content, instr, mdl),
            inputs=[project_id_state, script_edit, script_ai_instr, script_ai_model],
            outputs=[script_edit, script_ai_status],
            concurrency_limit=2,
        )
        script_step_refresh_btn.click(
            fn=lambda pid: f"**剧本**：{'已有剧本' if pid and list_scripts(int(pid)) else '⚪ 未生成'}",
            inputs=[project_id_state], outputs=[script_step_status], queue=False,
        )
        step1_run_btn.click(
            fn=story_stage_flow,
            inputs=[project_id_state, premise, project_name, genre, tone, acts, story_model, model],
            outputs=[gen_log, gen_results, project_id_state],
            concurrency_limit=2,
        )

        # 角色 AI 辅助
        char_ai_btn.click(
            fn=lambda pid, content, instr, mdl: ai_enhance_step(pid, "chars", content, instr, mdl),
            inputs=[project_id_state, char_edit, char_ai_instr, char_ai_model],
            outputs=[char_edit, char_ai_status],
            concurrency_limit=2,
        )
        step2_run_btn.click(
            fn=chars_stage_flow,
            inputs=[project_id_state, char_model, model],
            outputs=[gen_log, gen_results, project_id_state],
            concurrency_limit=2,
        )

        # 场景 AI 辅助
        scene_ai_btn.click(
            fn=lambda pid, content, instr, mdl: ai_enhance_step(pid, "scenes", content, instr, mdl),
            inputs=[project_id_state, scene_edit, scene_ai_instr, scene_ai_model],
            outputs=[scene_edit, scene_ai_status],
            concurrency_limit=2,
        )
        step3_run_btn.click(
            fn=scenes_stage_flow,
            inputs=[project_id_state, scene_model, model],
            outputs=[gen_log, gen_results, project_id_state],
            concurrency_limit=2,
        )

        # 配乐：AI 辅助 + 单步生成
        music_ai_btn.click(
            fn=lambda pid, content, instr, mdl: ai_enhance_step(pid, "music", content, instr, mdl),
            inputs=[project_id_state, music_edit, music_ai_instr, music_ai_model],
            outputs=[music_edit, music_ai_status],
            concurrency_limit=2,
        )
        music_status_refresh_btn.click(
            fn=lambda pid: load_music_status(int(pid) if pid else 0),
            inputs=[project_id_state], outputs=[music_step_status], queue=False,
        )
        music_run_btn.click(
            fn=run_music_step_flow,
            inputs=[project_id_state],
            outputs=[music_run_log, music_preview_out],
            concurrency_limit=2,
        ).then(
            fn=lambda pid: load_music_status(int(pid) if pid else 0),
            inputs=[project_id_state], outputs=[music_step_status], queue=False,
        )

        # 音效：AI 辅助 + 单步生成
        sfx_ai_btn.click(
            fn=lambda pid, content, instr, mdl: ai_enhance_step(pid, "sfx", content, instr, mdl),
            inputs=[project_id_state, sfx_edit, sfx_ai_instr, sfx_ai_model],
            outputs=[sfx_edit, sfx_ai_status],
            concurrency_limit=2,
        )
        sfx_run_btn.click(
            fn=run_sfx_step_flow,
            inputs=[project_id_state],
            outputs=[sfx_run_log, sfx_preview_out],
            concurrency_limit=2,
        )

        # TTS：查看 shot + 单步/全部生成
        tts_load_shot_btn.click(
            fn=lambda pid, sid: load_shot_tts_detail(int(pid) if pid else 0, sid),
            inputs=[project_id_state, tts_shot_id_input],
            outputs=[tts_dialogue_edit, tts_shot_status_md],
            queue=False,
        )
        tts_status_refresh_btn.click(
            fn=lambda pid: load_tts_status(int(pid) if pid else 0),
            inputs=[project_id_state], outputs=[tts_step_status_md], queue=False,
        )
        tts_run_shot_btn.click(
            fn=run_tts_step_flow,
            inputs=[project_id_state, tts_shot_id_input],
            outputs=[tts_run_log, tts_preview_out],
            concurrency_limit=2,
        ).then(
            fn=lambda pid: load_tts_status(int(pid) if pid else 0),
            inputs=[project_id_state], outputs=[tts_step_status_md], queue=False,
        )
        tts_run_all_btn.click(
            fn=lambda pid: run_tts_step_flow(pid, ""),
            inputs=[project_id_state],
            outputs=[tts_run_log, tts_preview_out],
            concurrency_limit=2,
        ).then(
            fn=lambda pid: load_tts_status(int(pid) if pid else 0),
            inputs=[project_id_state], outputs=[tts_step_status_md], queue=False,
        )

        # 渲染：单步/指定 shot
        render_status_refresh_btn.click(
            fn=lambda pid: load_render_status(int(pid) if pid else 0),
            inputs=[project_id_state], outputs=[render_step_status_md], queue=False,
        )
        render_run_btn.click(
            fn=run_render_step_flow,
            inputs=[project_id_state, render_shot_id_input],
            outputs=[render_run_log, render_video_preview],
            concurrency_limit=2,
        ).then(
            fn=lambda pid: load_render_status(int(pid) if pid else 0),
            inputs=[project_id_state], outputs=[render_step_status_md], queue=False,
        )

        # 合成导出
        composite_run_btn.click(
            fn=run_composite_step_flow,
            inputs=[project_id_state],
            outputs=[composite_run_log, composite_video_preview],
            concurrency_limit=2,
        ).then(
            fn=lambda log, vid: gr.update(value=vid or ""),
            inputs=[composite_run_log, composite_video_preview],
            outputs=[episode_video_path],
            queue=False,
        ).then(
            fn=lambda pid, path: record_export_manifest_for_project(pid, path or ""),
            inputs=[project_id_state, episode_video_path],
            outputs=[export_manifest_md],
            queue=False,
        )

        # 清空（绕过 queue，防止被生成器堵住）
        clear_btn.click(
            fn=lambda: (
                "### 📋 管线日志\n等待启动...", None,
                "运行管线后自动展示可读内容。",
                "", "", "", "", "", "运行管线后自动展示生产指标。", [], "", "", "", 0,
            ),
            inputs=[],
            outputs=gen_outputs,
            queue=False,
        )

        # Phase 2: 渲染导出（生成器，需要 queue 流式输出）
        render_btn.click(
            fn=render_export_flow,
            inputs=[project_id_state, project_name, render_config_state],
            outputs=[render_log, render_results, project_id_state],
            concurrency_limit=2,
        ).then(
            fn=load_industrial_dashboard,
            inputs=[project_id_state],
            outputs=[industrial_ops_card, industrial_bottleneck_card, industrial_model_audit_card, industrial_sop_card],
            queue=False,
        )

        # Phase 2: 续跑
        resume_btn.click(
            fn=resume_pipeline_flow,
            inputs=[project_id_state],
            outputs=[render_log, pipeline_state_md],
        ).then(
            fn=load_industrial_dashboard,
            inputs=[project_id_state],
            outputs=[industrial_ops_card, industrial_bottleneck_card, industrial_model_audit_card, industrial_sop_card],
            queue=False,
        )

        industrial_refresh_btn.click(
            fn=load_industrial_dashboard,
            inputs=[project_id_state],
            outputs=[industrial_ops_card, industrial_bottleneck_card, industrial_model_audit_card, industrial_sop_card],
            queue=False,
        )
        industrial_refresh_models_btn.click(
            fn=format_model_audit_markdown,
            inputs=[],
            outputs=[industrial_model_audit_card],
            queue=False,
        )

        # Phase 2: 管线选择器切换
        pipeline_selector_dd.change(
            fn=_on_pipeline_select,
            inputs=[pipeline_selector_dd],
            outputs=[pipeline_selector_dd, pipeline_status_card_md],
            queue=False,
        )

        # Phase 2: 检测缺失模型
        pipeline_detect_btn.click(
            fn=_detect_missing_models,
            inputs=[project_id_state],
            outputs=[pipeline_detect_log],
            queue=False,
        ).then(
            fn=lambda: gr.update(visible=True),
            inputs=None,
            outputs=[pipeline_detect_log],
            queue=False,
        )

        # Phase 2: 一键下载缺失模型
        pipeline_download_btn.click(
            fn=_auto_download_missing,
            inputs=[project_id_state],
            outputs=[pipeline_detect_log],
            queue=False,
        ).then(
            fn=lambda: gr.update(visible=True),
            inputs=None,
            outputs=[pipeline_detect_log],
            queue=False,
        )

        # Phase 2: 查看管线状态（旧按钮保留）
        pipeline_state_btn.click(
            fn=get_pipeline_state,
            inputs=[project_id_state],
            outputs=[pipeline_state_md],
            queue=False,
        )

        # 保存（绕过 queue）
        save_script_btn.click(
            fn=save_script_text,
            inputs=[project_id_state, script_edit],
            outputs=[script_status],
            queue=False,
        )
        save_char_btn.click(
            fn=save_chars_text,
            inputs=[project_id_state, char_edit],
            outputs=[char_status],
            queue=False,
        )
        save_scene_btn.click(
            fn=save_scenes_text,
            inputs=[project_id_state, scene_edit],
            outputs=[scene_status],
            queue=False,
        )
        save_music_btn.click(
            fn=save_music_text,
            inputs=[project_id_state, music_edit],
            outputs=[music_status],
            queue=False,
        )
        save_sfx_btn.click(
            fn=save_sfx_text,
            inputs=[project_id_state, sfx_edit],
            outputs=[sfx_status],
            queue=False,
        )
        shot_reload_btn.click(
            fn=build_shot_edit_json,
            inputs=[project_id_state],
            outputs=[shot_edit],
            queue=False,
        )
        save_shot_btn.click(
            fn=save_shot_edit_text,
            inputs=[project_id_state, shot_edit],
            outputs=[shot_status],
            queue=False,
        )
        shot_load_btn.click(
            fn=load_shot_form,
            inputs=[project_id_state, shot_action_id],
            outputs=[
                shot_form_status_md,
                shot_form_id,
                shot_form_act,
                shot_form_scene,
                shot_form_number,
                shot_form_location,
                shot_form_type,
                shot_form_mood,
                shot_form_time,
                shot_form_weather,
                shot_form_narration,
                shot_form_camera_notes,
                shot_form_status,
                shot_form_locked,
                shot_form_characters,
                shot_form_payload,
            ],
            queue=False,
        )
        shot_form_save_btn.click(
            fn=save_shot_form,
            inputs=[
                project_id_state,
                shot_form_id,
                shot_form_act,
                shot_form_scene,
                shot_form_number,
                shot_form_location,
                shot_form_type,
                shot_form_mood,
                shot_form_time,
                shot_form_weather,
                shot_form_narration,
                shot_form_camera_notes,
                shot_form_status,
                shot_form_locked,
                shot_form_characters,
                shot_form_payload,
            ],
            outputs=[shot_form_status_md, shot_edit, shot_table, production_overview],
            queue=False,
        ).then(
            fn=get_shot_review_summary,
            inputs=[project_id_state, shot_action_id],
            outputs=[shot_review_history_md],
            queue=False,
        )
        shot_action_id.change(
            fn=get_shot_review_summary,
            inputs=[project_id_state, shot_action_id],
            outputs=[shot_review_history_md],
            queue=False,
        )
        shot_approve_btn.click(
            fn=approve_shot_action,
            inputs=[project_id_state, shot_action_id, shot_review_note, shot_auto_lock_on_approve],
            outputs=[shot_status, shot_edit],
            queue=False,
        ).then(
            fn=build_shot_table,
            inputs=[project_id_state],
            outputs=[shot_table],
            queue=False,
        ).then(
            fn=format_production_overview,
            inputs=[project_id_state],
            outputs=[production_overview],
            queue=False,
        ).then(
            fn=get_shot_review_summary,
            inputs=[project_id_state, shot_action_id],
            outputs=[shot_review_history_md],
            queue=False,
        ).then(
            fn=load_render_status,
            inputs=[project_id_state],
            outputs=[render_step_status_md],
            queue=False,
        )
        shot_reject_btn.click(
            fn=lambda pid, sid, note: review_shot_action(pid, sid, "reject", note),
            inputs=[project_id_state, shot_action_id, shot_review_note],
            outputs=[shot_status, shot_edit],
            queue=False,
        ).then(
            fn=build_shot_table,
            inputs=[project_id_state],
            outputs=[shot_table],
            queue=False,
        ).then(
            fn=format_production_overview,
            inputs=[project_id_state],
            outputs=[production_overview],
            queue=False,
        ).then(
            fn=get_shot_review_summary,
            inputs=[project_id_state, shot_action_id],
            outputs=[shot_review_history_md],
            queue=False,
        ).then(
            fn=load_render_status,
            inputs=[project_id_state],
            outputs=[render_step_status_md],
            queue=False,
        )
        shot_lock_btn.click(
            fn=lambda pid, sid, note: review_shot_action(pid, sid, "lock", note),
            inputs=[project_id_state, shot_action_id, shot_review_note],
            outputs=[shot_status, shot_edit],
            queue=False,
        ).then(
            fn=build_shot_table,
            inputs=[project_id_state],
            outputs=[shot_table],
            queue=False,
        ).then(
            fn=format_production_overview,
            inputs=[project_id_state],
            outputs=[production_overview],
            queue=False,
        ).then(
            fn=get_shot_review_summary,
            inputs=[project_id_state, shot_action_id],
            outputs=[shot_review_history_md],
            queue=False,
        ).then(
            fn=load_render_status,
            inputs=[project_id_state],
            outputs=[render_step_status_md],
            queue=False,
        )
        shot_unlock_btn.click(
            fn=lambda pid, sid, note: review_shot_action(pid, sid, "unlock", note),
            inputs=[project_id_state, shot_action_id, shot_review_note],
            outputs=[shot_status, shot_edit],
            queue=False,
        ).then(
            fn=build_shot_table,
            inputs=[project_id_state],
            outputs=[shot_table],
            queue=False,
        ).then(
            fn=format_production_overview,
            inputs=[project_id_state],
            outputs=[production_overview],
            queue=False,
        ).then(
            fn=get_shot_review_summary,
            inputs=[project_id_state, shot_action_id],
            outputs=[shot_review_history_md],
            queue=False,
        ).then(
            fn=load_render_status,
            inputs=[project_id_state],
            outputs=[render_step_status_md],
            queue=False,
        )
        shot_rerender_btn.click(
            fn=lambda pid, sid, note, auto_lock: run_shot_rerender_flow(pid, sid, note, mode="rerender"),
            inputs=[project_id_state, shot_action_id, shot_review_note, shot_auto_lock_on_approve],
            outputs=[shot_render_log, shot_render_preview],
            concurrency_limit=2,
        ).then(
            fn=build_shot_edit_json,
            inputs=[project_id_state],
            outputs=[shot_edit],
            queue=False,
        ).then(
            fn=build_shot_table,
            inputs=[project_id_state],
            outputs=[shot_table],
            queue=False,
        ).then(
            fn=format_production_overview,
            inputs=[project_id_state],
            outputs=[production_overview],
            queue=False,
        ).then(
            fn=get_shot_review_summary,
            inputs=[project_id_state, shot_action_id],
            outputs=[shot_review_history_md],
            queue=False,
        ).then(
            fn=load_render_status,
            inputs=[project_id_state],
            outputs=[render_step_status_md],
            queue=False,
        )
        shot_rework_btn.click(
            fn=lambda pid, sid, note, auto_lock: run_shot_rerender_flow(pid, sid, note, mode="rework"),
            inputs=[project_id_state, shot_action_id, shot_review_note, shot_auto_lock_on_approve],
            outputs=[shot_render_log, shot_render_preview],
            concurrency_limit=2,
        ).then(
            fn=build_shot_edit_json,
            inputs=[project_id_state],
            outputs=[shot_edit],
            queue=False,
        ).then(
            fn=build_shot_table,
            inputs=[project_id_state],
            outputs=[shot_table],
            queue=False,
        ).then(
            fn=format_production_overview,
            inputs=[project_id_state],
            outputs=[production_overview],
            queue=False,
        ).then(
            fn=get_shot_review_summary,
            inputs=[project_id_state, shot_action_id],
            outputs=[shot_review_history_md],
            queue=False,
        ).then(
            fn=load_render_status,
            inputs=[project_id_state],
            outputs=[render_step_status_md],
            queue=False,
        )
        subtitle_load_btn.click(
            fn=load_subtitle_workspace,
            inputs=[project_id_state, subtitle_shot_id],
            outputs=[subtitle_text, subtitle_path_md, subtitle_status],
            queue=False,
        )
        subtitle_save_btn.click(
            fn=save_subtitle_text,
            inputs=[project_id_state, subtitle_shot_id, subtitle_text],
            outputs=[subtitle_status],
            queue=False,
        )

        # ── AI 编辑 ────────────────────────────────────
        ai_scan_btn.click(
            fn=lambda pid, instr, mdl: ai_edit_preview(int(pid) if pid else 0, instr, mdl),
            inputs=[project_id_state, ai_edit_instruction, model],
            outputs=[ai_edit_preview_md, ai_manifest_json],
            concurrency_limit=2,
        )
        ai_exec_btn.click(
            fn=lambda pid, mjson: ai_edit_execute(int(pid) if pid else 0, mjson),
            inputs=[project_id_state, ai_manifest_json],
            outputs=[ai_exec_status],
            queue=False,
        )
        ai_rollback_btn.click(
            fn=lambda pid: ai_edit_rollback(int(pid) if pid else 0, n=1),
            inputs=[project_id_state],
            outputs=[ai_exec_status],
            queue=False,
        )
        show_manifest_btn.click(
            fn=lambda v: gr.update(visible=not v),
            inputs=[ai_manifest_json],
            outputs=[ai_manifest_json],
            queue=False,
        )
        refresh_history_btn.click(
            fn=lambda pid: get_edit_history(int(pid) if pid else 0),
            inputs=[project_id_state],
            outputs=[edit_history_table],
            queue=False,
        )

        # ── 视频预览 ────────────────────────────────────
        def _load_shot_video(project_id, shot_id):
            from pathlib import Path as _Path
            if not project_id or not shot_id:
                return None, "请输入 Shot ID"
            shot_id = int(shot_id)
            proj = get_project(int(project_id))
            if not proj:
                return None, "项目不存在"
            jobs = list_render_jobs(project_id=int(project_id), shot_id=shot_id)
            for job in jobs:
                vp = job.get("output_path", "")
                if job.get("status") == "completed" and vp and _Path(vp).exists():
                    return vp, f"✅ shot {shot_id}: {_Path(vp).name}"
            try:
                from pipelines.output_manager import get_shot_video_path
                vp = get_shot_video_path(proj.name, shot_id)
                if vp:
                    return vp, f"✅ 找到视频: {_Path(vp).name}"
            except Exception:
                pass
            return None, f"❌ Shot {shot_id} 暂无视频"

        load_video_btn.click(
            fn=_load_shot_video,
            inputs=[project_id_state, shot_preview_id],
            outputs=[shot_video_player, shot_video_status],
            queue=False,
        )

        # ── 系统状态 ──────────────────────────────────────
        sys_refresh_btn.click(
            fn=get_system_status,
            inputs=[],
            outputs=[sys_status_md],
            queue=False,
        )

        # ── 渲染参数 ──────────────────────────────────────
        def _apply_render_params(steps, cfg, width, height, cur_cfg: dict):
            cfg_new = dict(cur_cfg or {})
            cfg_new["steps"]  = int(steps)
            cfg_new["cfg"]    = float(cfg)
            cfg_new["width"]  = int(width)
            cfg_new["height"] = int(height)
            parts = [f"Steps={steps}", f"CFG={cfg}", f"{width}×{height}"]
            if cfg_new.get("checkpoint"):
                parts.insert(0, f"Checkpoint: {cfg_new['checkpoint']}")
            return cfg_new, "✅ 参数已应用: " + " · ".join(parts)

        rp_apply_btn.click(
            fn=_apply_render_params,
            inputs=[rp_steps, rp_cfg, rp_width, rp_height, render_config_state],
            outputs=[render_config_state, rp_status_md],
            queue=False,
        )

        # ── TTS 试听 ──────────────────────────────────────
        tts_preview_btn.click(
            fn=test_tts_preview,
            inputs=[tts_test_text, tts_voice_type],
            outputs=[tts_audio_out, tts_preview_log],
        )

        # ── BGM 试听 ──────────────────────────────────────
        bgm_preview_btn.click(
            fn=test_bgm_preview,
            inputs=[bgm_mood_sel, bgm_dur_sel],
            outputs=[bgm_audio_out, bgm_preview_log],
        )

        # ── 模型管理 ─────────────────────────────────────

        def _load_all_models():
            ckpts, loras, vaes, cns, msg = cm_load_all_types()
            return (
                gr.update(choices=ckpts, value=ckpts[0] if ckpts else ""),
                gr.update(choices=loras, value=loras[0] if loras else ""),
                gr.update(choices=vaes,  value=vaes[0]  if vaes  else ""),
                gr.update(choices=cns,   value=cns[0]   if cns   else ""),
                msg,
            )

        cm_load_btn.click(
            fn=_load_all_models,
            inputs=[],
            outputs=[cm_ckpt_list, cm_lora_list, cm_vae_list, cm_cn_list, cm_status_md],
            queue=False,
        )

        def _cm_search(query, model_type):
            models, status = cm_refresh_list(model_type, query)
            return gr.update(choices=models, value=None), status

        cm_search_btn.click(
            fn=_cm_search,
            inputs=[cm_search_input, cm_type_filter],
            outputs=[cm_search_result, cm_status_md],
            queue=False,
        )
        cm_search_input.submit(
            fn=_cm_search,
            inputs=[cm_search_input, cm_type_filter],
            outputs=[cm_search_result, cm_status_md],
            queue=False,
        )

        dl_check_btn.click(
            fn=cm_check_file,
            inputs=[dl_filename, dl_type],
            outputs=[dl_check_status],
            queue=False,
        )

        dl_btn.click(
            fn=cm_do_download,
            inputs=[dl_source, dl_type, dl_filename],
            outputs=[dl_log],
        )

        def _apply_render_config(ckpt, lora, lora_str, search_sel, cur_cfg: dict):
            cfg = dict(cur_cfg or {})
            active_ckpt = ckpt or cfg.get("checkpoint", "")
            # search_result 优先（当搜索后选了一个）
            active_lora = lora or cfg.get("loras", [{}])[0].get("name", "") if cfg.get("loras") else lora
            if active_ckpt:
                cfg["checkpoint"] = active_ckpt
            if active_lora:
                cfg["loras"] = [{"name": active_lora, "strength": float(lora_str or 0.7)}]
            parts = []
            if cfg.get("checkpoint"):
                parts.append(f"📌 Checkpoint: `{cfg['checkpoint']}`")
            if cfg.get("loras"):
                lora_info = ", ".join(f"{l['name']} ({l['strength']})" for l in cfg["loras"])
                parts.append(f"🎨 LoRA: {lora_info}")
            display = "当前渲染配置：\n" + "\n".join(parts) if parts else "当前渲染配置：使用默认值"
            return cfg, display

        cm_apply_btn.click(
            fn=_apply_render_config,
            inputs=[cm_ckpt_list, cm_lora_list, cm_lora_strength,
                    cm_search_result, render_config_state],
            outputs=[render_config_state, cm_active_config_md],
            queue=False,
        )

    return app


if __name__ == "__main__":
    app = build_ui()
    app.launch(server_name="127.0.0.1", server_port=7860, share=False, show_error=True, css=CUSTOM_CSS)
