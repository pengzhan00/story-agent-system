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
from core.ollama_client import list_models, refresh_models, resolve_model_profile, STAGE_MODEL_DEFAULTS
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
/* ══════════════════════════════════════════
   漫剧故事工坊 — Clean Studio Theme
   ══════════════════════════════════════════ */
:root {
  --bg-base:    #f5f6fa;
  --bg-card:    #ffffff;
  --bg-raised:  #f0f2f8;
  --bg-input:   #ffffff;
  --bg-hover:   #eef0f8;
  --bg-sidebar: #f8f9fc;

  --text-primary:   #1a1d2e;
  --text-secondary: #4a5068;
  --text-muted:     #8a90a8;
  --text-label:     #3a3f58;

  --accent:       #6366f1;
  --accent-dim:   #4f52cc;
  --accent-light: #eef0ff;
  --accent-glow:  rgba(99,102,241,.15);

  --amber:        #f59e0b;
  --amber-light:  #fef3c7;
  --amber-dim:    #d97706;

  --success:      #10b981;
  --success-light:#d1fae5;
  --warning:      #f59e0b;
  --danger:       #ef4444;
  --danger-light: #fee2e2;

  --border:       #e2e5f0;
  --border-dark:  #c8cce0;

  --radius:    10px;
  --radius-sm: 6px;
  --shadow-sm: 0 1px 4px rgba(30,35,80,.08);
  --shadow-md: 0 4px 16px rgba(30,35,80,.10);
  --shadow-lg: 0 8px 32px rgba(30,35,80,.12);
}

/* ── Base ── */
body, .gradio-container {
  background: var(--bg-base) !important;
  color: var(--text-primary) !important;
  font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", system-ui, sans-serif !important;
}

/* ── Text contrast safety net ── */
.gradio-container,
.gradio-container p,
.gradio-container span,
.gradio-container div,
.gradio-container label,
.gradio-container h1,
.gradio-container h2,
.gradio-container h3,
.gradio-container h4,
.gradio-container h5,
.gradio-container h6,
.gradio-container li,
.gradio-container td,
.gradio-container th,
.gradio-container .prose,
.gradio-container .prose p,
.gradio-container .prose li,
.gradio-container .prose strong,
.gradio-container .prose em,
.gradio-container .prose code {
  color: var(--text-primary);
}

.gradio-container .prose a,
.gradio-container a {
  color: var(--accent) !important;
}

/* ── Cards / panels ── */
.block, .gr-box, .gr-panel, .gr-form, .gr-group,
.gradio-container > .main > .wrap,
.gap.compact, .gap {
  background: var(--bg-card) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  box-shadow: var(--shadow-sm) !important;
}

/* ── Inputs ── */
textarea, input[type="text"], input[type="number"], input[type="search"],
.gr-text-input input, .gr-text-area textarea, .gr-number input {
  background: var(--bg-input) !important;
  color: var(--text-primary) !important;
  border: 1.5px solid var(--border-dark) !important;
  border-radius: var(--radius-sm) !important;
  caret-color: var(--accent) !important;
  transition: border-color .2s, box-shadow .2s;
}
textarea:focus, input:focus {
  border-color: var(--accent) !important;
  box-shadow: 0 0 0 3px var(--accent-glow) !important;
  outline: none !important;
}

/* ── Select / Dropdown ── */
select, .wrap .wrap-inner select, .gr-dropdown select {
  background: var(--bg-input) !important;
  color: var(--text-primary) !important;
  border: 1.5px solid var(--border-dark) !important;
  border-radius: var(--radius-sm) !important;
}

/* ── Labels ── */
label, .block > label {
  color: var(--text-label) !important;
  font-size: 0.82rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.02em !important;
}

/* ── Buttons — Primary (indigo) ── */
button.primary, .gr-button.primary,
button[class*="primary"], .btn-primary {
  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 600 !important;
  border-radius: var(--radius-sm) !important;
  letter-spacing: 0.01em !important;
  box-shadow: 0 2px 8px rgba(99,102,241,.30) !important;
  transition: transform .15s, box-shadow .15s !important;
}
button.primary:hover, .gr-button.primary:hover,
button[class*="primary"]:hover {
  transform: translateY(-1px) !important;
  box-shadow: 0 4px 16px rgba(99,102,241,.40) !important;
}

/* ── Buttons — Secondary (amber) ── */
button.secondary, .gr-button.secondary,
button[class*="secondary"], .btn-secondary {
  background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 600 !important;
  border-radius: var(--radius-sm) !important;
  box-shadow: 0 2px 8px rgba(245,158,11,.25) !important;
  transition: transform .15s, box-shadow .15s !important;
}
button.secondary:hover, .gr-button.secondary:hover,
button[class*="secondary"]:hover {
  transform: translateY(-1px) !important;
  box-shadow: 0 4px 14px rgba(245,158,11,.38) !important;
}

/* ── Buttons — Default (neutral) ── */
button.lg, button.sm, .gr-button:not([class*="primary"]):not([class*="secondary"]) {
  background: var(--bg-raised) !important;
  color: var(--text-secondary) !important;
  border: 1.5px solid var(--border-dark) !important;
  border-radius: var(--radius-sm) !important;
  transition: background .15s, border-color .15s, color .15s !important;
}
button.lg:hover, button.sm:hover {
  background: var(--accent-light) !important;
  border-color: var(--accent) !important;
  color: var(--accent) !important;
}

/* ── Save button override ── */
.save-btn {
  background: linear-gradient(135deg, #10b981 0%, #059669 100%) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 600 !important;
}
.save-btn:hover {
  box-shadow: 0 4px 14px rgba(16,185,129,.35) !important;
  transform: translateY(-1px) !important;
}

/* ── Tabs ── */
.tab-nav, .tabs > .tab-nav {
  background: var(--bg-sidebar) !important;
  border-bottom: 1.5px solid var(--border) !important;
  gap: 2px !important;
  padding: 0 8px !important;
}
.tab-nav button {
  color: var(--text-secondary) !important;
  background: transparent !important;
  border: none !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 0 !important;
  padding: 10px 18px !important;
  font-size: 0.88rem !important;
  font-weight: 500 !important;
  transition: color .15s, border-color .15s !important;
}
.tab-nav button:hover {
  color: var(--accent) !important;
  background: var(--accent-light) !important;
}
.tab-nav button.selected {
  color: var(--accent) !important;
  background: transparent !important;
  border-bottom: 2.5px solid var(--accent) !important;
  font-weight: 700 !important;
}

/* ── Accordion ── */
.accordion {
  background: var(--bg-card) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  overflow: hidden !important;
}
.accordion > .label-wrap {
  background: var(--bg-raised) !important;
  padding: 10px 16px !important;
  border-bottom: 1px solid var(--border) !important;
}
.accordion > .label-wrap span {
  color: var(--text-label) !important;
  font-weight: 600 !important;
}

/* ── Section cards ── */
.section-shell {
  background: linear-gradient(180deg, #ffffff 0%, #fbfbfe 100%) !important;
  border: 1px solid var(--border) !important;
  border-radius: 14px !important;
  box-shadow: var(--shadow-md) !important;
  padding: 16px 18px !important;
  margin-bottom: 14px !important;
}
.section-title {
  margin: 0 0 6px 0 !important;
  font-size: 1.15rem !important;
  font-weight: 700 !important;
  color: var(--text-primary) !important;
}
.section-kicker {
  color: var(--text-muted) !important;
  font-size: 0.82rem !important;
  letter-spacing: .08em !important;
  text-transform: uppercase !important;
  margin-bottom: 6px !important;
}
.choice-card {
  background: #fbfcff !important;
  border: 1px solid var(--border) !important;
  border-radius: 12px !important;
  padding: 12px !important;
}
.workspace-shell {
  background: linear-gradient(180deg, #f9faff 0%, #f3f6ff 100%) !important;
  border: 1px solid #d7ddf2 !important;
  border-radius: 18px !important;
  box-shadow: 0 10px 28px rgba(54, 76, 128, 0.08) !important;
  padding: 18px !important;
  margin: 18px 0 !important;
}
.workspace-tabs {
  background: #ffffff !important;
  border: 1px solid var(--border) !important;
  border-radius: 16px !important;
  padding: 10px !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.8), var(--shadow-md) !important;
}
.workspace-intro {
  margin-bottom: 10px !important;
  color: var(--text-secondary) !important;
}
.tab-shell {
  background: linear-gradient(180deg, #ffffff 0%, #fdfdff 100%) !important;
  border: 1px solid var(--border) !important;
  border-radius: 14px !important;
  padding: 14px 16px !important;
  margin-bottom: 14px !important;
}
.tab-shell h3,
.tab-shell h4,
.tab-shell .md h3,
.tab-shell .md h4 {
  border-bottom: none !important;
  padding-bottom: 0 !important;
  margin-bottom: 6px !important;
}
.dashboard-grid {
  display: grid !important;
  grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  gap: 14px !important;
}
.dashboard-card {
  background: linear-gradient(180deg, #ffffff 0%, #f9fbff 100%) !important;
  border: 1px solid var(--border) !important;
  border-radius: 14px !important;
  padding: 14px !important;
  box-shadow: var(--shadow-md) !important;
}
.dashboard-card .md h3,
.dashboard-card .md h4,
.dashboard-card h3,
.dashboard-card h4 {
  border-bottom: none !important;
  padding-bottom: 0 !important;
}
.workbench-note {
  color: var(--text-secondary) !important;
  font-size: 0.92rem !important;
  margin-bottom: 10px !important;
}
@media (max-width: 980px) {
  .dashboard-grid {
    grid-template-columns: 1fr !important;
  }
}

/* ── Markdown ── */
.prose, .md, .markdown {
  color: var(--text-primary) !important;
}
.prose h1, .prose h2, .prose h3,
.md h1, .md h2, .md h3 {
  color: var(--text-primary) !important;
  border-bottom: 1.5px solid var(--border) !important;
  padding-bottom: 4px;
}
.prose code, .md code {
  background: var(--accent-light) !important;
  color: var(--accent-dim) !important;
  border-radius: 4px !important;
  padding: 1px 5px !important;
  font-size: 0.85em !important;
}
.prose pre, .md pre {
  background: #f8f9fc !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius-sm) !important;
}

/* ── Dataframe / Table ── */
.gr-samples-table, table {
  background: var(--bg-card) !important;
  border-color: var(--border) !important;
}
thead tr th {
  background: var(--bg-raised) !important;
  color: var(--text-secondary) !important;
  border-color: var(--border) !important;
  font-size: 0.78rem !important;
  letter-spacing: 0.05em !important;
  text-transform: uppercase !important;
  font-weight: 700 !important;
}
tbody tr td {
  background: var(--bg-card) !important;
  color: var(--text-primary) !important;
  border-color: var(--border) !important;
  font-size: 0.88rem !important;
}
tbody tr:hover td {
  background: var(--bg-raised) !important;
}

/* ── JSON output ── */
.json-holder {
  background: #f8f9fc !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius-sm) !important;
  color: var(--text-primary) !important;
}

/* ── Progress bar ── */
.gr-progress {
  height: 5px !important;
  border-radius: 3px !important;
  background: var(--bg-raised) !important;
}
.gr-progress > div {
  background: linear-gradient(90deg, #6366f1, #f59e0b) !important;
  border-radius: 3px !important;
}

/* ── Custom progress cards ── */
#overall-progress-bar {
  background: var(--accent-light) !important;
  border: 1.5px solid var(--accent) !important;
  border-radius: var(--radius-sm) !important;
  padding: 10px 14px !important;
  font-family: "JetBrains Mono", "Fira Code", monospace !important;
  font-size: 0.88rem !important;
  color: var(--text-primary) !important;
}
#shot-progress-bar {
  background: var(--amber-light) !important;
  border: 1.5px solid var(--amber) !important;
  border-radius: var(--radius-sm) !important;
  padding: 10px 14px !important;
  font-size: 0.88rem !important;
  color: var(--text-primary) !important;
}

/* ── Slider ── */
input[type="range"] {
  accent-color: var(--accent) !important;
}

/* ── Checkbox & Radio ── */
input[type="checkbox"], input[type="radio"] {
  accent-color: var(--accent) !important;
}

/* ── Video / Audio preview ── */
video, audio {
  border-radius: var(--radius-sm) !important;
  border: 1px solid var(--border) !important;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg-raised); }
::-webkit-scrollbar-thumb { background: var(--border-dark); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

/* ── Dropdown popup ── */
.choices__list--dropdown, ul[role="listbox"] {
  background: var(--bg-card) !important;
  border: 1px solid var(--border-dark) !important;
  border-radius: var(--radius-sm) !important;
  box-shadow: var(--shadow-lg) !important;
}
.choices__item, li[role="option"] {
  color: var(--text-primary) !important;
  padding: 8px 12px !important;
}
.choices__item--highlighted, li[role="option"]:hover,
li[role="option"][aria-selected="true"] {
  background: var(--accent-light) !important;
  color: var(--accent) !important;
}

/* ── Gradio title h1 ── */
.gradio-container h1 {
  background: linear-gradient(90deg, #6366f1, #a855f7);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  font-size: 1.7rem !important;
  font-weight: 800 !important;
  letter-spacing: -0.02em !important;
}
"""

# Module-level theme (shared between build_ui and launch)
_STUDIO_THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.indigo,
    secondary_hue=gr.themes.colors.yellow,
    neutral_hue=gr.themes.colors.slate,
).set(
    body_background_fill="#f5f6fa",
    body_text_color="#1a1d2e",
    block_background_fill="#ffffff",
    block_border_color="#e2e5f0",
    block_shadow="0 1px 4px rgba(30,35,80,.08)",
    button_primary_background_fill="linear-gradient(135deg, #6366f1, #4f46e5)",
    button_primary_text_color="#ffffff",
    button_secondary_background_fill="linear-gradient(135deg, #f59e0b, #d97706)",
    button_secondary_text_color="#ffffff",
    input_background_fill="#ffffff",
    input_border_color="#c8cce0",
    checkbox_background_color="#ffffff",
    slider_color="#6366f1",
    table_even_background_fill="#f5f6fa",
    table_odd_background_fill="#ffffff",
)


def get_ollama_models():
    try:
        refresh_models()
        models = [m for m in list_models() if "embed" not in m.lower()]
        preferred = [
            "qwen3.6:35b",
            "qwen3.5:27b",
            "qwen3:8b",
            "qwen2.5-coder:7b",
            "deepseek-r1:70b",
            "deepseek-r1:8b",
            "qwen2.5:7b",
        ]
        ordered = []
        for name in preferred:
            if name in models and name not in ordered:
                ordered.append(name)
        for name in models:
            if name not in ordered:
                ordered.append(name)
        return ordered
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


def _default_stage_model_profile() -> dict[str, str]:
    return dict(STAGE_MODEL_DEFAULTS)


def format_model_profile(model_selection: str = "") -> str:
    profile = _default_stage_model_profile()
    lines = [
        "### 🤖 阶段模型分配",
        "- 后台默认按固定分工运行；只有你手动指定某个阶段模型时，才会覆盖这一默认配置。",
    ]
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


def _resolve_model(stage_model: str, stage_default: str, global_model_var) -> str:
    """返回阶段模型；默认走固定阶段分工，仅在手动指定时覆盖。"""
    return (stage_model or "").strip() or (stage_default or "").strip() or "qwen3:8b"


def story_stage_flow(pid, premise, pname, genre, tone, acts, stage_m, global_m, progress=gr.Progress()):
    """步骤1: 剧本生成。"""
    if not premise or not premise.strip():
        yield "### ⚠️ 请输入创作构想", None, int(pid or 0); return
    model = _resolve_model(stage_m, _default_stage_model_profile()["director"], None)
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
    model = _resolve_model(stage_m, _default_stage_model_profile()["character"], None)
    for pct, log_md, partial in run_stage_characters(int(pid), model=model):
        progress(pct)
        yield log_md, partial, int(pid)


def scenes_stage_flow(pid, stage_m, global_m, progress=gr.Progress()):
    """步骤3: 场景设计。"""
    if not pid:
        yield "### ⚠️ 请先运行步骤1生成剧本", None, 0; return
    model = _resolve_model(stage_m, _default_stage_model_profile()["scene"], None)
    for pct, log_md, partial in run_stage_scenes(int(pid), model=model):
        progress(pct)
        yield log_md, partial, int(pid)


def art_music_stage_flow(pid, stage_m, global_m, progress=gr.Progress()):
    """步骤4: 美术/音乐/音效。"""
    if not pid:
        yield "### ⚠️ 请先运行步骤1生成剧本", None, 0; return
    model = _resolve_model(stage_m, _default_stage_model_profile()["art"], None)
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
                       genre_tags=None, tone_tags=None, emotion_arc="",
                       episode_count=80, project_format="short_drama",
                       progress=gr.Progress()):
    """yield (gen_log, gen_result, view_md, edit_data..., pid)"""
    if not premise or not premise.strip():
        yield ("### ⚠️ 请先输入创作构想", None, "", "", "", "", "", "", "", [], "", "", "", 0)
        return

    # 构建 per-stage 模型配置：默认固定阶段分工，仅在手动指定阶段模型时覆盖
    base = model or "qwen3.6:35b"
    project_format = _normalize_project_format(project_format)
    stage_models = _default_stage_model_profile()
    if story_model:
        stage_models["director"] = story_model
        stage_models["writer"] = story_model
    if char_model:
        stage_models["character"] = char_model
    if scene_model:
        stage_models["scene"] = scene_model
    if art_model:
        stage_models["art"] = art_model
        stage_models["music"] = art_model
        stage_models["sound"] = art_model

    result = None
    pid = 0
    try:
        for pct, log_md, partial in run_pipeline_generator(
            premise=premise.strip(),
            project_name=_sanitize_project_name(project_name) if project_name else "",
            genre=genre or "玄幻", tone=tone or "热血",
            acts=int(acts) if acts else 4,
            model=base,
            model_profile=stage_models,
            enable_render=False,
            genre_tags=genre_tags or [],
            tone_tags=tone_tags or [],
            emotion_arc=emotion_arc or "",
            episode_count=int(episode_count) if episode_count else 80,
            act_count=int(acts) if acts else 4,
            project_format=project_format or "short_drama",
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


# ─── 概念发现 ─────────────────────────────────────

def concept_generate_flow(keywords, requirements, n, use_web, model_name, progress=gr.Progress()):
    """生成 N 个故事梗概卡片，更新 DataFrame + CheckboxGroup + State."""
    if not keywords or not keywords.strip():
        return (
            [],                          # concept_data_state
            [],                          # dataframe rows
            gr.update(choices=[], value=[]),  # concept_select
            "⚠️ 请先输入关键词",
        )
    progress(0.1, desc="AI 生成梗概中...")
    try:
        from agents.concept_finder.core import generate_concepts
        concepts = generate_concepts(
            keywords=keywords.strip(),
            requirements=(requirements or "").strip(),
            n=int(n or 6),
            use_web=bool(use_web),
            model=model_name or "qwen3.6:35b",
        )
    except Exception as e:
        return ([], [], gr.update(choices=[], value=[]), f"❌ 生成失败: {e}")

    progress(0.9, desc="整理结果...")
    rows = [
        [c["id"], c["title"], c["genre"], c["tone"], c["synopsis"], c["hook"]]
        for c in concepts
    ]
    choices = [f"{c['id']} | {c['title']} ({c['genre']} / {c['tone']})" for c in concepts]
    return (
        concepts,                                    # concept_data_state
        rows,                                        # dataframe
        gr.update(choices=choices, value=[]),        # concept_select
        f"✅ 生成 {len(concepts)} 个梗概，请勾选后「加入队列」",
    )


def concept_add_to_queue(selected_labels, concept_data, queue_data):
    """把勾选的概念加入队列，返回新队列 state + 队列展示 MD."""
    if not selected_labels:
        return queue_data, _concept_queue_md(queue_data), gr.update(choices=_concept_queue_choices(queue_data), value=[]), "⚠️ 请先勾选梗概"
    # 从 label 中提取 id (格式 "id | title ...")
    selected_ids = {lbl.split(" | ")[0].strip() for lbl in selected_labels}
    new_items = [c for c in concept_data if c["id"] in selected_ids
                 and c["id"] not in {q["id"] for q in queue_data}]
    new_queue = queue_data + new_items
    return (
        new_queue,
        _concept_queue_md(new_queue),
        gr.update(choices=_concept_queue_choices(new_queue), value=[]),
        f"✅ 已加入 {len(new_items)} 个，队列共 {len(new_queue)} 个",
    )


def concept_clear_queue(_queue):
    _QUEUE_CONTROL["stop"] = False
    return [], "**队列为空**", gr.update(choices=[], value=[]), ""


_QUEUE_CONTROL = {"stop": False}
FORMAT_CHOICE_MAP = {
    "竖屏 9:16（短剧默认）": "short_drama",
    "横屏 16:9（短剧 / 电影都可用）": "movie",
    "short_drama": "short_drama",
    "movie": "movie",
}


def _normalize_project_format(value: str) -> str:
    return FORMAT_CHOICE_MAP.get(value or "", "short_drama")


def _concept_queue_md(queue: list) -> str:
    if not queue:
        return "**队列为空**"
    rows = ["| # | 剧名 | 类型 | 基调 | 爽点 |", "|---|---|---|---|---|"]
    for i, c in enumerate(queue, 1):
        rows.append(f"| {i} | {c['title']} | {c['genre']} | {c['tone']} | {c['hook']} |")
    return "\n".join(rows)


def _concept_queue_choices(queue: list) -> list[str]:
    return [f"{c['id']} | {c['title']} ({c['genre']} / {c['tone']})" for c in queue]


def concept_remove_from_queue(selected_labels, queue_data):
    if not selected_labels:
        return queue_data, _concept_queue_md(queue_data), gr.update(choices=_concept_queue_choices(queue_data), value=[]), "⚠️ 请先勾选队列项"
    selected_ids = {lbl.split(" | ")[0].strip() for lbl in selected_labels}
    new_queue = [q for q in queue_data if q["id"] not in selected_ids]
    removed = len(queue_data) - len(new_queue)
    return (
        new_queue,
        _concept_queue_md(new_queue),
        gr.update(choices=_concept_queue_choices(new_queue), value=[]),
        f"✅ 已移除 {removed} 个待执行项，剩余 {len(new_queue)} 个",
    )


def concept_stop_remaining_queue():
    _QUEUE_CONTROL["stop"] = True
    return "🛑 已请求停止剩余队列。当前正在处理的项目会在下一个安全点结束后停止继续。"


def concept_fill_premise(selected_labels, concept_data):
    """把第一个选中的概念填入 premise 文本框."""
    if not selected_labels or not concept_data:
        return gr.update()
    first_id = selected_labels[0].split(" | ")[0].strip()
    concept = next((c for c in concept_data if c["id"] == first_id), None)
    if not concept:
        return gr.update()
    text = f"{concept['synopsis']}\n\n【类型】{concept['genre']}  【基调】{concept['tone']}\n【爽点】{concept['hook']}"
    return gr.update(value=text)


def concept_run_queue_flow(
    queue_data, project_name_base, genre_val, tone_val, acts_val,
    model_val, story_model_val, char_model_val, scene_model_val, art_model_val,
    genre_tags_val, tone_tags_val, emotion_arc_val, episode_count_val, project_format_val,
    progress=gr.Progress(),
):
    """依次对队列中每个概念跑完整管线，yield 日志."""
    if not queue_data:
        yield "⚠️ 队列为空，请先加入梗概", {}
        return
    _QUEUE_CONTROL["stop"] = False
    project_format_internal = _normalize_project_format(project_format_val)

    all_logs = []
    for i, concept in enumerate(queue_data, 1):
        if _QUEUE_CONTROL.get("stop"):
            all_logs.append(f"\n\n🛑 队列已停止，剩余 {len(queue_data) - i + 1} 个项目未继续执行")
            yield "\n".join(all_logs), {}
            return
        header = f"\n\n---\n### 🎬 队列 [{i}/{len(queue_data)}]: {concept['title']}\n"
        all_logs.append(header)
        yield "\n".join(all_logs), {}

        premise_text = (
            f"{concept['synopsis']}\n"
            f"类型：{concept['genre']}  基调：{concept['tone']}\n"
            f"爽点：{concept['hook']}"
        )
        pname = f"{project_name_base or concept['title']}_{i}" if len(queue_data) > 1 else (project_name_base or "")

        try:
            from core.orchestrator import run_pipeline_generator
            base = model_val or "qwen3.6:35b"
            stage_models = _default_stage_model_profile()
            if story_model_val: stage_models["director"] = story_model_val; stage_models["writer"] = story_model_val
            if char_model_val:  stage_models["character"] = char_model_val
            if scene_model_val: stage_models["scene"] = scene_model_val
            if art_model_val:   stage_models["art"] = art_model_val; stage_models["music"] = art_model_val; stage_models["sound"] = art_model_val
            for pct, log_md, partial in run_pipeline_generator(
                premise=premise_text,
                project_name=_sanitize_project_name(pname) if pname else "",
                genre=concept.get("genre", genre_val or "玄幻"),
                tone=concept.get("tone", tone_val or "热血"),
                acts=int(acts_val or 4),
                model=base,
                model_profile=stage_models,
                enable_render=False,
                genre_tags=genre_tags_val or [],
                tone_tags=tone_tags_val or [],
                emotion_arc=emotion_arc_val or "",
                episode_count=int(episode_count_val or 80),
                act_count=int(acts_val or 4),
                project_format=project_format_internal,
            ):
                progress(((i - 1) / len(queue_data)) + pct / len(queue_data))
                combined = "\n".join(all_logs) + "\n" + log_md
                yield combined, partial or {}
                if _QUEUE_CONTROL.get("stop"):
                    all_logs.append("\n🛑 当前项目已到安全点，停止继续执行后续队列项")
                    yield "\n".join(all_logs), partial or {}
                    return
                last_partial = partial
        except Exception as e:
            import traceback
            all_logs.append(f"❌ 第{i}个失败: {e}\n{traceback.format_exc()[:500]}")
            yield "\n".join(all_logs), {}

    all_logs.append(f"\n\n✅ 队列全部完成，共 {len(queue_data)} 个项目")
    yield "\n".join(all_logs), {}


# ─── Phase 2: 渲染导出 ─────────────────────────────

def render_export_flow(pid, project_name, render_cfg, progress=gr.Progress()):
    """yield (render_log, render_result, render_pid)"""
    if not pid:
        yield ("### ⚠️ 请先生成内容", None, 0)
        return

    preflight_ok, preflight_md = _render_preflight_markdown(auto_launch=True)
    if not preflight_ok:
        yield (preflight_md + "\n\n### ⛔ 渲染已阻止\n请先修复上述阻塞项后再启动渲染。", None, pid)
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


def _quick_video_template_defaults(template: str) -> tuple[str, str, str]:
    template = (template or "").strip()
    if template == "全自动":
        return (
            "快速试片（更快）",
            "ACE-Step 优先，失败回退（推荐）",
            "### ⚙️ 当前模板：全自动\n直接偏向更快出样片，默认做小样验证，适合先看方向。",
        )
    if template == "严格分阶段":
        return (
            "质量优先（严格）",
            "只用 ACE-Step",
            "### ⚙️ 当前模板：严格分阶段\n优先质量与稳定性，不做静默回退。",
        )
    return (
        "稳妥模式（推荐）",
        "ACE-Step 优先，失败回退（推荐）",
        "### ⚙️ 当前模板：轻审一次\n默认走最稳的生产路径，先做阶段预检，再继续生成。",
    )


def _quick_video_product_defaults(target: str) -> tuple[float, str, str]:
    target = (target or "").strip()
    if target == "做电影":
        return (
            60,
            "横屏 16:9",
            "### 🎬 当前目标：做电影\n默认按横屏长段落规划，优先让系统先出分镜图，再做镜头运镜与音频合成。",
        )
    if target == "做短剧":
        return (
            30,
            "竖屏 9:16",
            "### 📺 当前目标：做短剧\n默认按竖屏短剧节奏走，适合连续做 15~60 秒段落并逐段拼成长片。",
        )
    return (
        15,
        "竖屏 9:16",
        "### 🎞️ 当前目标：做短视频\n默认先做短段样片，确认人物、画风、音乐方向后再拉长。",
    )


def stop_rendering_now(project_id: int = 0) -> tuple[str, str]:
    from core.orchestrator import request_render_stop
    from core.database import cancel_running_render_jobs
    import requests as _req
    from core.service_ports import comfyui_api_base

    request_render_stop()
    msgs = ["### 🛑 停止渲染请求已发送"]
    try:
        api = comfyui_api_base()
        try:
            r = _req.post(f"{api}/interrupt", timeout=5)
            msgs.append(f"- {'✅' if r.status_code == 200 else '⚠️'} 已请求中断当前 ComfyUI 任务")
        except Exception as e:
            msgs.append(f"- ⚠️ 中断当前任务失败: {e}")
        try:
            _req.post(f"{api}/queue", json={"clear": True}, timeout=5)
            msgs.append("- ✅ 已清空 ComfyUI 等待队列")
        except Exception as e:
            msgs.append(f"- ⚠️ 清空 ComfyUI 队列失败: {e}")
    except Exception as e:
        msgs.append(f"- ⚠️ ComfyUI 地址不可用: {e}")

    cancelled = cancel_running_render_jobs(int(project_id or 0))
    msgs.append(f"- ✅ 已标记 {cancelled} 个运行中渲染作业为取消")
    if project_id:
        msgs.append(f"- 项目 `{project_id}` 的后续镜头会在安全点停止继续执行")
    status_md = "\n".join(msgs)
    return status_md, status_md


def force_kill_comfyui() -> str:
    """强制 SIGKILL ComfyUI 进程（UN 状态软中断无效时使用），然后自动重启。"""
    import subprocess, signal, time
    import requests as _req

    msgs = ["### ☢️ 强制终止 ComfyUI"]

    # 1. 软中断尝试
    try:
        from core.service_ports import comfyui_api_base
        api = comfyui_api_base()
        _req.post(f"{api}/interrupt", timeout=3)
        _req.post(f"{api}/queue", json={"clear": True}, timeout=3)
        msgs.append("- ✅ 软中断信号已发送")
    except Exception:
        msgs.append("- ⚠️ 软中断跳过（ComfyUI 未响应）")

    # 2. 找 ComfyUI 进程 PID（匹配 port 8188）
    try:
        result = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True
        )
        pids = []
        for line in result.stdout.splitlines():
            if "port 8188" in line and "grep" not in line:
                pid = int(line.split()[1])
                pids.append(pid)

        if not pids:
            msgs.append("- ℹ️ 未找到 ComfyUI 进程（可能已停止）")
        else:
            for pid in pids:
                try:
                    import os
                    os.kill(pid, signal.SIGKILL)
                    msgs.append(f"- ✅ SIGKILL → PID {pid}")
                except ProcessLookupError:
                    msgs.append(f"- ℹ️ PID {pid} 已不存在")
                except Exception as e:
                    msgs.append(f"- ⚠️ kill PID {pid} 失败: {e}")
    except Exception as e:
        msgs.append(f"- ⚠️ 查找进程失败: {e}")

    time.sleep(2)

    # 3. 重启 ComfyUI
    try:
        comfyui_dir = Path.home() / "Documents" / "ComfyUI"
        python_bin = comfyui_dir / ".venv" / "bin" / "python3"
        log_file = open("/tmp/comfyui_auto.log", "w")
        subprocess.Popen(
            [str(python_bin), "main.py", "--listen", "127.0.0.1", "--port", "8188"],
            cwd=str(comfyui_dir),
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
        msgs.append("- 🔄 ComfyUI 已重启（后台启动，约 30 秒后可用）")
    except Exception as e:
        msgs.append(f"- ⚠️ ComfyUI 重启失败: {e}")

    # 4. 标记编排器停止
    try:
        from core.orchestrator import request_render_stop
        request_render_stop()
        msgs.append("- ✅ 编排器渲染任务已取消")
    except Exception:
        pass

    return "\n".join(msgs)


def quick_video_flow(prompt, bgm_prompt, duration_sec, aspect_ratio, product_target, execution_route,
                     cloud_api_key, cloud_api_base, cloud_model,
                     pipeline_choice, reference_image_path, crossfade,
                     workflow_template, render_strategy, audio_strategy,
                     quality: str = "标准",
                     progress=gr.Progress()):
    """指定时长短视频一键生成。"""
    prompt = (prompt or "").strip()
    if not prompt:
        yield "请输入视频主题描述。", gr.update(value=None, visible=False), "### ⚠️ 请先输入视频主题"
        return

    execution_route = (execution_route or "").strip()
    cloud_api_key = (cloud_api_key or "").strip()
    cloud_api_base = (cloud_api_base or "").strip()
    cloud_model = (cloud_model or "").strip()
    if cloud_api_key:
        os.environ["QWEN_VIDEO_API_KEY"] = cloud_api_key
        os.environ["DASHSCOPE_API_KEY"] = cloud_api_key
    if cloud_api_base:
        os.environ["QWEN_VIDEO_API_BASE"] = cloud_api_base
    if cloud_model:
        os.environ["QWEN_VIDEO_MODEL"] = cloud_model
    if execution_route == "Qwen 云端重视频（API）":
        readiness = collect_backend_readiness(auto_launch_comfyui=False, auto_launch_acestep=False)
        preflight_ok = bool(readiness["ollama"]["ready"])
        preflight_md = format_backend_readiness_markdown(readiness)
    else:
        preflight_ok, preflight_md = _quick_video_preflight_markdown(auto_launch=True)
    if not preflight_ok:
        yield (
            preflight_md,
            gr.update(value=None, visible=False),
            "### ⛔ 快速视频已阻止\n请先修复上述阻塞项后再试。",
        )
        return

    pipeline_map = {
        "auto（自动）": "",
        "本机友好成片（AI 分镜图 → 运镜合成）": "local_storyboard_reel",
        "Qwen 云端重视频（API）": "qwen_wan_cloud",
        "LTX 本机直出视频": "ltx_t2v",
    }
    # 直接传中文别名（get_preset 能识别所有选项）
    aspect_map = {
        "竖屏 9:16": "竖屏 9:16",
        "横屏 16:9": "横屏 16:9",
        "横屏 4:3":  "横屏 4:3",
        "竖屏 3:4":  "竖屏 3:4",
        "方形 1:1":  "方形 1:1",
        "portrait":  "竖屏 9:16",
        "landscape": "横屏 16:9",
        "square":    "方形 1:1",
    }

    chosen_pipeline = pipeline_map.get(pipeline_choice or "", "")
    reference_image_path = (reference_image_path or "").strip()
    has_reference = bool(reference_image_path and Path(reference_image_path).exists())
    allow_fallback = True
    audio_mode = "auto"
    strict_production = False

    render_strategy = (render_strategy or "").strip()
    if render_strategy == "快速试片（更快）":
        if not chosen_pipeline:
            if execution_route == "Qwen 云端重视频（API）":
                chosen_pipeline = "qwen_wan_cloud"
            elif execution_route == "LTX 本机直出视频":
                chosen_pipeline = "ltx_t2v"
            else:
                chosen_pipeline = "local_storyboard_reel"
        allow_fallback = True
    elif render_strategy == "质量优先（严格）":
        if not chosen_pipeline:
            if execution_route == "Qwen 云端重视频（API）":
                chosen_pipeline = "qwen_wan_cloud"
            elif execution_route == "LTX 本机直出视频":
                chosen_pipeline = "ltx_t2v"
            else:
                chosen_pipeline = "local_storyboard_reel"
        allow_fallback = False
        strict_production = execution_route != "Qwen 云端重视频（API）"
    else:
        if not chosen_pipeline:
            if execution_route == "Qwen 云端重视频（API）":
                chosen_pipeline = "qwen_wan_cloud"
            elif execution_route == "LTX 本机直出视频":
                chosen_pipeline = "ltx_t2v"
            else:
                chosen_pipeline = "local_storyboard_reel"
        allow_fallback = True
        strict_production = execution_route != "Qwen 云端重视频（API）"

    audio_strategy = (audio_strategy or "").strip()
    if audio_strategy == "只用 ACE-Step":
        audio_mode = "acestep_only"
    elif audio_strategy == "只用基础 BGM":
        audio_mode = "ffmpeg_only"

    log_lines: list[str] = []
    if strict_production and not chosen_pipeline:
        yield (
            "当前没有参考图，且你选择的是生产/严格路线。",
            gr.update(value=None, visible=False),
            "### ⛔ 快速视频已阻止\n当前系统的稳态生产路径需要参考图。若要继续无参考图纯文本生成，请手动切到实验链。",
        )
        return

    template_note = (
        f"🎯 目标：{product_target or '做短视频'} | 🧭 模板：{workflow_template or '轻审一次'}"
        f" | 🚦 路线：{execution_route or '本机友好成片（AI 分镜图 → 运镜合成）'}"
        f" | 🔊 音频：{audio_strategy or 'ACE-Step 优先，失败回退（推荐）'}"
    )
    log_lines.append(template_note)
    if execution_route == "Qwen 云端重视频（API）":
        api_base = cloud_api_base or os.getenv("QWEN_VIDEO_API_BASE") or "https://dashscope.aliyuncs.com/api/v1"
        model_name = cloud_model or os.getenv("QWEN_VIDEO_MODEL") or "qwen-vl-max"
        key_hint = "已填写" if cloud_api_key else ("已读取环境变量" if (os.getenv("QWEN_VIDEO_API_KEY") or os.getenv("DASHSCOPE_API_KEY")) else "未提供")
        log_lines.append(f"☁️ 云端配置：base=`{api_base}` · model=`{model_name}` · key=`{key_hint}`")
    if chosen_pipeline == "local_storyboard_reel":
        log_lines.append("🏠 当前走本机友好主路：AI 分镜图 → 运镜成片 → 音频合成。")
    elif chosen_pipeline == "qwen_wan_cloud":
        log_lines.append("☁️ 当前走 Qwen 云端重视频：适合偶尔做重视频，不占本机视频引擎。")
    elif has_reference:
        log_lines.append("🖼️ 已检测到参考图：本机路线会把它当角色/画面锚点优先利用。")
    else:
        log_lines.append("🧪 当前走实验链：仅用于摸底验证，不作为本机默认生产。")

    from pipelines.quick_video import QuickVideoGenerator

    generator = QuickVideoGenerator()
    final_path = None
    preview_mode = render_strategy == "快速试片（更快）"

    for update in generator.generate(
        prompt=prompt,
        duration_sec=float(duration_sec or 15),
        pipeline_name=chosen_pipeline,
        aspect_ratio=aspect_map.get(aspect_ratio or "", "竖屏 9:16"),
        reference_image_path=reference_image_path,
        bgm_prompt=(bgm_prompt or "").strip(),
        crossfade=float(crossfade or 0.0),
        allow_fallback=allow_fallback,
        audio_mode=audio_mode,
        preview_mode=preview_mode,
        quality=quality or "标准",
    ):
        pct = float(update.get("progress", 0.0))
        progress(pct)
        msg = update.get("msg", "")
        if msg:
            log_lines.append(msg)
        final_path = update.get("output_path") or final_path
        if update.get("error"):
            yield (
                "\n".join(log_lines[-20:]),
                gr.update(value=None, visible=False),
                f"### ❌ 快速视频失败\n`{update['error']}`",
            )
            return

        video_update = gr.update(value=final_path, visible=bool(final_path and Path(final_path).exists()))
        status_md = (
            f"### ⏳ {update.get('step', 'running')}\n{msg}"
            if not final_path
            else f"### ✅ 快速视频完成\n{msg}"
        )
        yield ("\n".join(log_lines[-20:]), video_update, status_md)

def resume_pipeline_flow(pid, progress=gr.Progress()):
    """续跑管线：yield (log, overall_progress_md)"""
    if not pid:
        yield "### ⚠️ 请先生成内容（需要项目 ID）", "**整体进度** — 无项目"
        return

    import core.database as _db
    from core.pipeline_state import resume_pipeline, describe_state

    # 计算总 shot 数
    all_shots = _db.list_shots(project_id=int(pid))
    total = len(all_shots)

    def _overall_md(done: int, total: int, stage: str = "") -> str:
        pct = done / max(total, 1) * 100
        bar_filled = int(pct / 5)   # 20 格
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        eta = ""
        return f"**整体进度** `[{bar}]` {done}/{total} shots ({pct:.1f}%) {stage}"

    log_lines = []
    done_shots = 0
    try:
        gen = resume_pipeline(int(pid), max_retries=2)
        while True:
            try:
                msg, pct = next(gen)
                log_lines.append(msg)
                progress(pct)
                # 实时统计已完成 shots
                done_shots = sum(1 for s in _db.list_shots(project_id=int(pid))
                                 if s.status in ("rendered", "approved"))
                stage_hint = msg[:30] if msg else ""
                yield (
                    "### 🔄 续跑中...\n" + "\n".join(f"- {l}" for l in log_lines[-20:]),
                    _overall_md(done_shots, total, stage_hint),
                )
            except StopIteration as e:
                result = e.value
                break
    except Exception as e:
        import traceback
        yield (
            f"### ❌ 续跑出错\n```\n{e}\n{traceback.format_exc()[-2000:]}\n```",
            _overall_md(done_shots, total, "❌ 出错"),
        )
        return

    done_shots = sum(1 for s in _db.list_shots(project_id=int(pid))
                     if s.status in ("rendered", "approved"))
    done_stages = result.get("stages", {}) if result else {}
    summary = "  ".join(f"{s}: {v.get('done', 0)}" for s, v in done_stages.items())
    yield (
        f"### ✅ 续跑完成\n{summary}\n\n" + "\n".join(f"- {l}" for l in log_lines[-30:]),
        _overall_md(done_shots, total, "✅ 完成"),
    )


def get_pipeline_state(pid):
    """返回当前管线状态 Markdown。"""
    if not pid:
        return "无项目"
    from core.pipeline_state import describe_state
    try:
        return describe_state(int(pid))
    except Exception as e:
        return f"❌ 获取状态失败: {e}"


def _poll_current_shot_status() -> str:
    """
    轮询 ComfyUI 当前 shot 渲染状态（每 4 秒自动调用）。
    返回 Markdown 字符串，显示在「当前 Shot」进度栏。
    """
    try:
        import requests as _req
        from core.service_ports import comfyui_api_base
        api = comfyui_api_base()
        r = _req.get(f"{api}/queue", timeout=2)
        q = r.json()
        running = q.get("queue_running", [])
        pending = q.get("queue_pending", [])

        if not running and not pending:
            return "**当前 Shot** — ⏸ ComfyUI 空闲"

        parts = ["**当前 Shot**"]
        if running:
            job = running[0]
            nodes = job[2] if len(job) > 2 else {}
            model = next(
                (v["inputs"].get("unet_name", "?") for v in nodes.values()
                 if v.get("class_type") in ("UnetLoaderGGUF", "UNETLoader")),
                "?"
            )
            steps = next(
                (v["inputs"].get("steps", "?") for v in nodes.values()
                 if v.get("class_type") == "KSampler"),
                "?"
            )
            # 截短模型名
            model_short = model.split("/")[-1][:35] if model != "?" else "?"
            parts.append(f"🎬 渲染中 | `{model_short}` · {steps}步")
            if pending:
                parts.append(f"队列: +{len(pending)} 待渲染")
        elif pending:
            parts.append(f"⏳ 等待队列 ({len(pending)} 个)")

        return "  \n".join(parts)
    except Exception as e:
        return f"**当前 Shot** — ⚠ {e}"


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
        desc = entry.get("description", "无描述")
        prod = entry.get("production_ready", True)
        ecfg = entry.get("config", {})
        steps = ecfg.get("steps", "?")
        s = matrix.get(name, {})
        avail = s.get("available", False)
        if avail and prod:
            status_icon = "🟢"
        elif avail and not prod:
            status_icon = "🟡"
        elif not avail and prod:
            status_icon = "🔴"
        else:
            status_icon = "⚪"
        # 提取层级标签：生产主链 / 实验链 / 兜底
        tier_tag = ""
        if "【生产主力】" in desc:
            tier_tag = "🏭生产"
        elif "【实验链】" in desc:
            tier_tag = "🧪实验"
        elif "【兜底】" in desc:
            tier_tag = "🔧兜底"
        # 去掉描述中的中文标签，只保留核心说明
        core_desc = (
            desc.replace("【生产主力】", "")
            .replace("【实验链】", "")
            .replace("【兜底】", "")
            .strip()
        )
        label = f"{status_icon} [{tier_tag} {steps}步] {name} · {core_desc}"
        choices.append((label, name))
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
    vae_name = ecfg.get("vae", "未设置")
    model_name = ecfg.get("model_name") or ecfg.get("gguf_path", "")
    if model_name:
        model_name = Path(str(model_name)).name

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
    if model_name:
        lines.append(f"- 模型: `{model_name}`")
    lines.append(f"- VAE: `{vae_name}`")

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

    # ── 已知 ModelScope / HuggingFace 下载源 ──
    KNOWN_SOURCES = {
        "ltx-2.3-22b-distilled-1.1.safetensors":
            ("hf", "Lightricks/LTX-Video", "ltx-2.3-22b-distilled-1.1.safetensors"),
        "gemma_3_12B_it_fp4_mixed.safetensors":
            ("hf", "Lightricks/LTX-Video", "gemma_3_12B_it_fp4_mixed.safetensors"),
        "ltx-2.3-22b-distilled-lora-384.safetensors":
            ("hf", "Lightricks/LTX-Video", "ltx-2.3-22b-distilled-lora-384.safetensors"),
        "ltx-2.3-spatial-upscaler-x2-1.1.safetensors":
            ("hf", "Lightricks/LTX-Video", "ltx-2.3-spatial-upscaler-x2-1.1.safetensors"),
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
    readiness = collect_backend_readiness(auto_launch_comfyui=False, auto_launch_acestep=False)

    # ComfyUI
    online = readiness["comfyui"]["ready"]
    lines.append(f"**ComfyUI**: {'🟢 在线' if online else '🔴 离线'}")
    lines.append(f"  *详情*: {readiness['comfyui']['detail']}")

    lines.append("**视频生成**: LTX 2.3 distilled ✅")
    lines.append(f"**Active Pipeline**: {'✅' if readiness['pipeline']['ready'] else '❌'} {readiness['pipeline']['detail']}")
    lines.append(f"**Ollama**: {'✅' if readiness['ollama']['ready'] else '❌'} {readiness['ollama']['detail']}")

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
        from pipelines.audio_pipeline import get_acestep_music_status
        ace = get_acestep_music_status()
        if ace["reason"] == "ready":
            lines.append("**BGM 生成**: ✅ Ace-Step 1.5（ComfyUI + API + LLM）→ ffmpeg 合成兜底")
            model_bits = [bit for bit in [ace.get("loaded_model"), ace.get("loaded_lm_model")] if bit]
            if model_bits:
                lines.append(f"  *已加载*: {', '.join(model_bits)}")
        else:
            lines.append(f"**BGM 生成**: ⚠️ ffmpeg 合成兜底（Ace-Step 未就绪：{ace.get('reason') or 'unknown'}）")
    except Exception:
        lines.append("**BGM 生成**: ffmpeg 合成（内置保底）")

    return "\n\n".join(lines)


def collect_backend_readiness(auto_launch_comfyui: bool = False, auto_launch_acestep: bool = False) -> dict:
    """Collect backend readiness for production gating and UI display."""
    readiness = {
        "ollama": {"ready": False, "detail": "Ollama 未连接"},
        "comfyui": {"ready": False, "detail": "ComfyUI 未启动"},
        "pipeline": {"ready": False, "detail": "渲染管线未就绪"},
        "acestep": {"ready": False, "detail": "ACE-Step 未就绪"},
    }

    try:
        models = refresh_models()
        readiness["ollama"] = {
            "ready": bool(models),
            "detail": f"{len(models)} 个模型可用" if models else "未获取到 Ollama 模型列表",
        }
    except Exception as e:
        readiness["ollama"] = {"ready": False, "detail": str(e)}

    try:
        from core.service_ports import (
            get_comfyui_url, comfyui_status_dict, invalidate_cache,
            get_acestep_url, acestep_status_dict,
        )
        if auto_launch_comfyui:
            invalidate_cache()
            get_comfyui_url(auto_launch=True)
        comfy = comfyui_status_dict()
        readiness["comfyui"] = {
            "ready": bool(comfy.get("online")),
            "detail": comfy.get("api_base") or comfy.get("url") or "ComfyUI 未启动",
        }

        if auto_launch_acestep:
            get_acestep_url(auto_launch=True)
        ace = acestep_status_dict()
        readiness["acestep"] = {
            "ready": bool(ace.get("online")),
            "detail": ace.get("url") or "ACE-Step 未启动",
        }
    except Exception as e:
        readiness["comfyui"] = {"ready": False, "detail": str(e)}

    try:
        from pipelines.render_pipeline import get_dispatcher
        dispatcher = get_dispatcher()
        matrix = dispatcher.probe(force=True)
        active = dispatcher.active_pipeline or "未设置"
        active_status = matrix.get(active)
        if active_status and active_status.available:
            readiness["pipeline"] = {"ready": True, "detail": f"{active} 已就绪"}
        elif active_status:
            blocked = "; ".join(active_status.missing[:4]) or "未知阻塞项"
            readiness["pipeline"] = {"ready": False, "detail": f"{active} 未就绪: {blocked}"}
        else:
            available = [name for name, info in matrix.items() if info.available]
            readiness["pipeline"] = {
                "ready": bool(available),
                "detail": f"active={active}；可用={', '.join(available[:4]) or '无'}",
            }
    except Exception as e:
        readiness["pipeline"] = {"ready": False, "detail": str(e)}

    try:
        from pipelines.audio_pipeline import get_acestep_music_status
        ace_music = get_acestep_music_status() if auto_launch_acestep else None
        if ace_music:
            readiness["acestep"] = {
                "ready": ace_music.get("reason") == "ready",
                "detail": (
                    f"DiT={ace_music.get('loaded_model')} / LLM={ace_music.get('loaded_lm_model')}"
                    if ace_music.get("reason") == "ready"
                    else ace_music.get("reason") or "ACE-Step 未就绪"
                ),
            }
    except Exception:
        pass

    return readiness


def format_backend_readiness_markdown(readiness: dict) -> str:
    labels = {
        "ollama": "Ollama",
        "comfyui": "ComfyUI",
        "pipeline": "Active Pipeline",
        "acestep": "ACE-Step",
    }
    lines = ["### 🧪 生产前自检"]
    for key in ("ollama", "comfyui", "pipeline", "acestep"):
        item = readiness.get(key, {})
        icon = "✅" if item.get("ready") else "❌"
        lines.append(f"- {icon} `{labels[key]}`: {item.get('detail', '')}")
    return "\n".join(lines)


def _render_preflight_markdown(auto_launch: bool = True) -> tuple[bool, str]:
    readiness = collect_backend_readiness(auto_launch_comfyui=auto_launch, auto_launch_acestep=False)
    blocking = [
        readiness["comfyui"]["detail"] if not readiness["comfyui"]["ready"] else "",
        readiness["pipeline"]["detail"] if not readiness["pipeline"]["ready"] else "",
    ]
    blocking = [item for item in blocking if item]
    return (len(blocking) == 0, format_backend_readiness_markdown(readiness))


def _quick_video_preflight_markdown(auto_launch: bool = True) -> tuple[bool, str]:
    readiness = collect_backend_readiness(auto_launch_comfyui=auto_launch, auto_launch_acestep=False)
    blocking = []
    for key in ("ollama", "comfyui", "pipeline"):
        if not readiness[key]["ready"]:
            blocking.append(readiness[key]["detail"])
    return (len(blocking) == 0, format_backend_readiness_markdown(readiness))


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
    from pipelines.audio_pipeline import (
        generate_music_acestep,
        generate_music_ffmpeg,
        get_acestep_music_status,
    )
    out = tempfile.mktemp(suffix=".mp3")
    try:
        ace = get_acestep_music_status()
        if ace.get("reason") == "ready":
            if generate_music_acestep("preview", out, duration=duration, mood=mood):
                return out, (
                    f"✅ BGM 生成成功（ACE-Step，mood={mood}）\n\n"
                    f"- DiT: `{ace.get('loaded_model')}`\n"
                    f"- LLM: `{ace.get('loaded_lm_model')}`"
                )
        if generate_music_ffmpeg("preview", out, duration=duration, mood=mood):
            fallback_reason = ace.get("reason") if ace else "Ace-Step 状态未知"
            return out, (
                f"✅ BGM 生成成功（ffmpeg 兜底，mood={mood}）\n\n"
                f"- 未走 ACE-Step 的原因: `{fallback_reason}`"
            )
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
    # ── LTX 2.3 视频主线 ─────────────────────────────────────────────────────
    {
        "group": "LTX 2.3 视频主线",
        "name": "LTX 2.3-22B distilled 1.1 (bf16)  ← 生产主力",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/checkpoints/ltx-2.3-22b-distilled-1.1.safetensors",
        "min_size_mb": 10000,
        "critical": True,
    },
    {
        "group": "LTX 2.3 视频主线",
        "name": "LTX 2.3-22B distilled fp8（可选，省显存）",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/checkpoints/ltx-2.3-22b-distilled-fp8.safetensors",
        "min_size_mb": 10000,
        "critical": False,
    },
    {
        "group": "LTX 2.3 视频主线",
        "name": "Gemma 3 12B text encoder (fp4_mixed)",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors",
        "min_size_mb": 5000,
        "critical": True,
    },
    {
        "group": "LTX 2.3 视频主线",
        "name": "LTX distilled LoRA 384",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/loras/ltx-2.3-22b-distilled-lora-384.safetensors",
        "min_size_mb": 100,
        "critical": True,
    },
    {
        "group": "LTX 2.3 视频主线",
        "name": "LTX spatial upscaler x2 1.1",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/latent_upscale_models/ltx-2.3-spatial-upscaler-x2-1.1.safetensors",
        "min_size_mb": 100,
        "critical": True,
    },
    {
        "group": "LTX 2.3 视频主线",
        "name": "LTX 2.3-22B dev bf16（可选，最高画质）",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/checkpoints/ltx-2.3-22b-dev.safetensors",
        "min_size_mb": 10000,
        "critical": False,
    },
    {
        "group": "LTX 2.3 视频主线",
        "name": "LTX 2.3-22B dev fp8（可选，省显存高质量）",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/checkpoints/ltx-2.3-22b-dev-fp8.safetensors",
        "min_size_mb": 10000,
        "critical": False,
    },
    # ── FLUX 图像 ──────────────────────────────────────────────────────────────
    {
        "group": "FLUX",
        "name": "FLUX.2-klein-4B",
        "kind": "any",
        "paths": [
            "~/myworkspace/ComfyUI_models/checkpoints/flux-2-klein-4b.safetensors",
            "~/myworkspace/ComfyUI_models/diffusion_models/flux-2-klein-4b.safetensors",
        ],
        "patterns": ["*.safetensors"],
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
    # ── ACE-Step 音乐 ──────────────────────────────────────────────────────────
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step v1.5 XL SFT bf16（高质量）",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/diffusion_models/acestep_v1.5_xl_sft_bf16.safetensors",
        "min_size_mb": 3000,
        "critical": True,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step v1.5 XL Turbo bf16（快速）",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/diffusion_models/acestep_v1.5_xl_turbo_bf16.safetensors",
        "min_size_mb": 3000,
        "critical": True,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step Qwen 0.6B LM",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/text_encoders/qwen_0.6b_ace15.safetensors",
        "min_size_mb": 500,
        "critical": True,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step Qwen 4B LM",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/text_encoders/qwen_4b_ace15.safetensors",
        "min_size_mb": 1000,
        "critical": False,
    },
    {
        "group": "ACE-Step 音乐",
        "name": "ACE-Step VAE",
        "kind": "file",
        "path": "~/myworkspace/ComfyUI_models/vae/ace_1.5_vae.safetensors",
        "min_size_mb": 100,
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
        "- 说明: `[关键]` 表示当前主生产链强依赖；非关键项更多是扩展能力。",
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
        "1. 模型资产审计：先保证 `LTX`、`Gemma`、ACE-Step 和图像侧基础模型四组关键资产可用。",
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
    ace_status = None
    try:
        from pipelines.audio_pipeline import get_acestep_music_status
        ace_status = get_acestep_music_status()
    except Exception:
        ace_status = None

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
        (
            f"- ACE-Step 状态: {'已就绪' if ace_status and ace_status.get('reason') == 'ready' else '未就绪'}"
            + (
                f"（DiT: `{ace_status.get('loaded_model')}` / LLM: `{ace_status.get('loaded_lm_model')}`）"
                if ace_status and ace_status.get('reason') == 'ready'
                else f"（{ace_status.get('reason')}）" if ace_status and ace_status.get('reason')
                else ""
            )
        ),
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
    if ace_status and ace_status.get("reason") != "ready":
        missing_lines.append(f"- `ACE-Step 服务`: {ace_status.get('reason')}")
    else:
        if not critical_missing:
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
    try:
        from core.service_ports import get_comfyui_url, comfyui_api_base, invalidate_cache

        invalidate_cache()
        base = get_comfyui_url(auto_launch=True)
        api = comfyui_api_base()
        mode = "App /api" if api.endswith("/api") else "CLI"
        return f"✅ ComfyUI 已就绪：{base}（{mode}）"
    except Exception as e:
        return f"❌ 启动失败: {e}"


# ─── 构建 UI ─────────────────────────────────────────

def build_ui():
    init_db()
    models = get_ollama_models()
    preferred_default = next(
        (name for name in ["qwen3.6:35b", "qwen3.5:27b", "qwen3:8b", "qwen2.5-coder:7b", "deepseek-r1:70b"] if name in models),
        None,
    )
    default_model = preferred_default or (models[0] if models else "qwen3:8b")

    with gr.Blocks(title="🎬 漫剧故事工坊") as app:
        app.queue(default_concurrency_limit=5)

        # ─── 标题 ────────────────────────────────────────────────────────────
        gr.Markdown("# 🎬 漫剧故事工坊")
        gr.Markdown("**统一生产工作台**：写梗概 → 快速做视频 → 完整管线 → 工作站审计，全程不离开这个页面。")

        # ─── 项目选择（始终可见）──────────────────────────────────────────────
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

        # ─── 梗概输入（始终可见，所有 Tab 共用）─────────────────────────────
        premise = gr.Textbox(
            label="✏️ 梗概 / 热词 / 创作构想",
            lines=3,
            placeholder="例如：失忆白月光回国，豪门前夫追妻火葬场。也可以只写热词：霸总 失忆 追妻 火葬场。",
        )

        # ─── 状态变量 ─────────────────────────────────────────────────────────
        project_id_state = gr.State(0)
        render_config_state = gr.State({})

        # ─── 顶级 Tab 导航 ────────────────────────────────────────────────────
        with gr.Tabs(elem_classes=["main-tabs"]):

            # ════════════════════════════════════════════════════════════════════
            #  Tab 1 · 快速做视频
            # ════════════════════════════════════════════════════════════════════
            with gr.TabItem("🚀 快速做视频"):
                gr.Markdown("从上面「梗概框」输入创意，选好类型和路线，直接做视频。", elem_classes=["workbench-note"])

                with gr.Row():
                    home_content_type = gr.Radio(
                        choices=["📱 短剧（竖屏/红果风格）", "🎬 短视频（广告/Vlog）", "🎥 电影（横屏/长片）"],
                        value="📱 短剧（竖屏/红果风格）",
                        label="① 内容类型",
                        interactive=True,
                    )
                    home_execution_route = gr.Radio(
                        label="② 执行路线",
                        choices=["LTX 本机直出视频", "本机友好成片（AI 分镜 → 运镜）", "Qwen 云端（API）"],
                        value="LTX 本机直出视频",
                        interactive=True,
                    )

                home_type_note = gr.Markdown(
                    "**短剧**：竖屏 9:16，每集 3–5 分钟，节奏快、爽点密集，典型红果/抖音短剧风格。"
                )

                with gr.Row():
                    home_duration = gr.Slider(
                        label="③ 目标时长（秒）", minimum=6, maximum=300, value=15, step=3, scale=3,
                    )

                with gr.Row():
                    home_aspect = gr.Radio(
                        label="④ 画面比例",
                        choices=["竖屏 9:16", "横屏 16:9", "横屏 4:3", "竖屏 3:4", "方形 1:1"],
                        value="竖屏 9:16",
                        interactive=True,
                        scale=3,
                    )
                    home_quality = gr.Radio(
                        label="⑤ 画质档位",
                        choices=["标准（540p）", "高清（720p）", "全高清（1080p）"],
                        value="标准（540p）",
                        interactive=True,
                        scale=2,
                    )

                home_target_note = gr.Markdown(
                    "> **短剧模式**：竖屏 9:16，节奏快，适合红果/抖音风格。  \n"
                    "> ⚡ **标准 540p**：本机 Apple Silicon 推荐，每镜头约 5-15 分钟。  \n"
                    "> ⚠️ **720p/1080p**：需要 NVIDIA 24GB+ 显卡，Apple MPS 上可能耗时数小时。"
                )

                with gr.Row():
                    home_bgm_prompt = gr.Textbox(
                        label="BGM 描述（可选）",
                        placeholder="如：轻快电子+鼓点，适合追妻爽剧",
                        lines=1, scale=3,
                    )
                    home_crossfade = gr.Slider(
                        label="转场（秒）", minimum=0.0, maximum=1.0, value=0.3, step=0.1, scale=1,
                    )

                with gr.Accordion("☁️ Qwen 云端配置（仅选云端路线时填）", open=False):
                    home_cloud_api_key = gr.Textbox(
                        label="API Key", type="password",
                        placeholder="留空 = 读取环境变量 QWEN_VIDEO_API_KEY",
                    )
                    with gr.Row():
                        home_cloud_api_base = gr.Textbox(
                            label="API Base",
                            value=os.getenv("QWEN_VIDEO_API_BASE", "https://dashscope.aliyuncs.com/api/v1"),
                        )
                        home_cloud_model = gr.Textbox(
                            label="云端模型",
                            value=os.getenv("QWEN_VIDEO_MODEL", "qwen-vl-max"),
                        )

                with gr.Row():
                    home_qv_btn       = gr.Button("🎬 做视频", variant="primary", size="lg", scale=3)
                    home_stop_btn     = gr.Button("🛑 停止", variant="stop", size="lg", scale=1)
                    home_kill_btn     = gr.Button("☢️ 强杀", variant="stop", size="lg", scale=1)
                home_kill_note = gr.Markdown(
                    "_🛑 停止：软中断，当前镜头完成后停。 ☢️ 强杀：SIGKILL ComfyUI 进程并重启，卡死时使用。_",
                    visible=True,
                )
                home_qv_log   = gr.Textbox(label="生成日志", lines=6, interactive=False)
                home_qv_video = gr.Video(label="视频结果", interactive=False, visible=False)
                home_qv_status = gr.Markdown("")

            # ════════════════════════════════════════════════════════════════════
            #  Tab 2 · 完整管线
            # ════════════════════════════════════════════════════════════════════
            with gr.TabItem("🏭 完整管线"):

                # ── AI 批量生成梗概 ──────────────────────────────────────────────
                with gr.Accordion("💡 AI 批量生成梗概（可选）", open=False):
                    gr.Markdown("输入关键词让 AI 批量生成故事梗概 → 勾选 → 点「填入梗概框」→ 批量跑管线。")
                    with gr.Row():
                        concept_keywords = gr.Textbox(
                            label="关键词", placeholder="如：兵王 赘婿 豪门 / 重生 复仇 商战", scale=3,
                        )
                        concept_requirements = gr.Textbox(
                            label="定制需求（可选）", placeholder="如：女主要强势、结局大团圆、无脑爽", scale=3,
                        )
                    with gr.Row():
                        concept_n = gr.Slider(label="生成数量", minimum=3, maximum=10, value=6, step=1, scale=1)
                        concept_use_web = gr.Checkbox(label="🌐 联网搜索参考", value=False, scale=1)
                        concept_model = gr.Dropdown(
                            label="模型", choices=models or ["qwen3.6:35b"],
                            value=default_model, allow_custom_value=True, scale=2,
                        )
                        concept_gen_btn = gr.Button("🔍 生成梗概", variant="primary", scale=1)
                    concept_table = gr.Dataframe(
                        headers=["ID", "剧名", "类型", "基调", "梗概", "爽点"],
                        datatype=["str", "str", "str", "str", "str", "str"],
                        interactive=False, wrap=True,
                        label="生成的故事梗概", row_count=(6, "dynamic"),
                    )
                    concept_data_state = gr.State([])
                    with gr.Row():
                        concept_select = gr.CheckboxGroup(
                            label="勾选要加入队列的梗概（按编号选）", choices=[], value=[], scale=4,
                        )
                        with gr.Column(scale=1):
                            concept_add_btn = gr.Button("➕ 加入队列", variant="secondary")
                            concept_clear_queue_btn = gr.Button("🗑️ 清空队列")
                    concept_queue_state = gr.State([])
                    concept_queue_md = gr.Markdown("**队列为空**")
                    concept_queue_select = gr.CheckboxGroup(
                        label="待执行队列（可在开跑前移除某几项）", choices=[], value=[],
                    )
                    with gr.Row():
                        concept_run_queue_btn = gr.Button("🚀 启动队列生成", variant="primary", scale=2)
                        concept_fill_btn = gr.Button("✏️ 填入梗概框（单选）", variant="secondary", scale=1)
                        concept_remove_btn = gr.Button("➖ 移除选中项", scale=1)
                        concept_stop_btn = gr.Button("🛑 停止", variant="stop", scale=1)
                        concept_kill_btn = gr.Button("☢️ 强杀", variant="stop", scale=1)
                    concept_queue_log = gr.Markdown("")

                gr.Markdown("---")

                # ── 项目设置 ─────────────────────────────────────────────────────
                with gr.Row():
                    project_name = gr.Textbox(label="项目名称", placeholder="留空自动生成", scale=1)
                    genre = gr.Dropdown(
                        label="主类型",
                        choices=["玄幻","仙侠","都市","科幻","奇幻","武侠","历史","悬疑","恐怖","言情","校园","末日"],
                        value="玄幻", scale=1,
                    )
                    tone = gr.Dropdown(
                        label="主基调",
                        choices=["热血","温馨","黑暗","搞笑","治愈","悬疑","史诗","浪漫","轻松","沉重"],
                        value="热血", scale=1,
                    )
                    acts = gr.Slider(label="幕数", minimum=1, maximum=5, value=4, step=1, scale=1)

                with gr.Accordion("🎭 细化设置（可选）", open=False):
                    with gr.Row():
                        genre_tags = gr.CheckboxGroup(
                            label="复合类型",
                            choices=["都市言情","古装宫斗","穿越重生","豪门总裁","悬疑推理","灵异惊悚","热血战争","青春校园"],
                            value=[], scale=3,
                        )
                        tone_tags = gr.CheckboxGroup(
                            label="复合基调",
                            choices=["甜宠","虐恋","爽文复仇","权谋暗黑","轻喜搞笑","热血燃向","治愈温情"],
                            value=[], scale=3,
                        )
                        emotion_arc = gr.Dropdown(
                            label="情绪弧度",
                            choices=["先甜后虐","先虐后甜","全程爽","全程虐","高开低走","低开高走"],
                            value="先甜后虐", scale=2,
                        )
                    with gr.Row():
                        episode_count = gr.Slider(
                            label="总集数", minimum=10, maximum=300, value=80, step=10, scale=2,
                        )
                        project_format = gr.Radio(
                            label="输出画幅",
                            choices=["竖屏 9:16（短剧默认）", "横屏 16:9（短剧 / 电影都可用）"],
                            value="竖屏 9:16（短剧默认）",
                            scale=2,
                        )

                gr.Markdown("---")

                # ── 模型设置 ─────────────────────────────────────────────────────
                with gr.Accordion("🤖 模型设置（展开修改）", open=False):
                    with gr.Row():
                        model = gr.Dropdown(label="全局备用模型", choices=models or ["qwen3.6:35b"],
                            value=default_model, allow_custom_value=True, scale=2)
                    model_profile_md = gr.Markdown(format_model_profile(default_model))
                    with gr.Row():
                        story_model = gr.Dropdown(label="📝 步骤1 剧本", choices=models or [default_model],
                            value="", allow_custom_value=True, scale=1)
                        char_model = gr.Dropdown(label="👤 步骤2 角色", choices=models or [default_model],
                            value="", allow_custom_value=True, scale=1)
                        scene_model = gr.Dropdown(label="🏞️ 步骤3 场景", choices=models or [default_model],
                            value="", allow_custom_value=True, scale=1)
                        art_model = gr.Dropdown(label="🎨 步骤4 美术/音乐", choices=models or [default_model],
                            value="", allow_custom_value=True, scale=1)

                # ── 分步运行 ─────────────────────────────────────────────────────
                with gr.Group():
                    gr.Markdown("### ▶️ 分步运行")
                    with gr.Row():
                        step1_btn = gr.Button("📝 步骤1: 剧本", scale=1)
                        step2_btn = gr.Button("👤 步骤2: 角色", scale=1)
                        step3_btn = gr.Button("🏞️ 步骤3: 场景", scale=1)
                        step4_btn = gr.Button("🎨 步骤4: 美术/音乐/音效", scale=1)
                        step5_btn = gr.Button("🎞️ 步骤5: 分镜", scale=1)
                    with gr.Row():
                        stage_status_btn = gr.Button("🔍 查看阶段状态", size="sm", scale=1)
                    stage_status_md = gr.Markdown("")

                with gr.Row():
                    gen_btn = gr.Button("🔥 一键全流程生成", variant="primary", size="lg", scale=2)
                    clear_btn = gr.Button("🗑️ 清空", size="lg", scale=1)

                gen_log = gr.Markdown("### 📋 管线日志\n等待启动...")
                gen_results = gr.JSON(value=None, label="生成结果摘要")

                gr.Markdown("---")

                # ── 渲染导出 ─────────────────────────────────────────────────────
                gr.Markdown("## 🎬 渲染 + 导出")
                with gr.Row():
                    comfyui_status_md = gr.Markdown(_comfyui_status_text())
                    comfyui_launch_btn = gr.Button("🚀 启动 ComfyUI", scale=1, size="sm")
                    comfyui_refresh_btn = gr.Button("🔄 刷新状态", scale=1, size="sm")
                comfyui_launch_log = gr.Markdown("", visible=False)

                pipeline_choices, active_pipeline_name = _pipeline_choices()
                with gr.Row():
                    pipeline_selector_dd = gr.Dropdown(
                        choices=pipeline_choices, value=active_pipeline_name,
                        label="活跃管线", scale=3, interactive=True,
                    )
                    pipeline_detect_btn = gr.Button("🔍 检测缺失", size="sm", scale=1, min_width=80)
                    pipeline_download_btn = gr.Button("⬇️ 一键下载", size="sm", scale=1, min_width=80, variant="primary")
                pipeline_status_card_md = gr.Markdown(_pipeline_status_card(active_pipeline_name))
                pipeline_detect_log = gr.Markdown("", visible=False)

                with gr.Row():
                    render_btn = gr.Button("🚀 全量渲染+导出", variant="secondary", size="lg",
                                           elem_classes="gr-button-secondary", scale=2)
                    resume_btn = gr.Button("♻️ 断点续跑", variant="primary", size="lg", scale=2)
                    render_stop_btn = gr.Button("🛑 停止", variant="stop", size="lg", scale=1)
                    render_kill_btn = gr.Button("☢️ 强杀", variant="stop", size="lg", scale=1)
                    pipeline_state_btn = gr.Button("📊 查看状态", size="lg", scale=1)

                gr.Markdown("`全量渲染+导出`：从头检查并执行完整 Phase 2。`断点续跑`：跳过已完成 shot。")

                with gr.Row(equal_height=True):
                    overall_progress_md = gr.Markdown(
                        "**整体进度** — 等待开始", elem_id="overall-progress-bar",
                    )
                    shot_progress_md = gr.Markdown(
                        "**当前 Shot** — 等待开始",
                        elem_id="shot-progress-bar",
                        every=4,
                        value=lambda: _poll_current_shot_status(),
                    )
                render_log = gr.Markdown("点击「全量渲染+导出」开始，或点击「断点续跑」从断点续跑...")
                render_results = gr.JSON(value=None, label="渲染结果")
                pipeline_state_md = gr.Markdown("", label="管线状态")

            # ════════════════════════════════════════════════════════════════════
            #  Tab 3 · 工作站
            # ════════════════════════════════════════════════════════════════════
            with gr.TabItem("🔧 工作站"):
                production_overview = gr.Markdown("运行管线后自动展示生产指标。")
                industrial_ops_default = "### 🎛️ 工业化总控\n先加载项目或生成内容。"
                industrial_bottleneck_default = "### 🚨 当前瓶颈\n等待审计。"
                industrial_model_audit_default = format_model_audit_markdown()
                industrial_sop_default = format_industrial_sop_markdown()
                with gr.Tabs(elem_classes=["workspace-tabs"]):
                    with gr.TabItem("🏭 工业化控制台"):
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🏭 面向批量生产的总控面板")
                            gr.Markdown("把模型资产、阶段状态、推荐动作和快捷入口放到一个界面，不再分散在几个小区域里。", elem_classes=["workbench-note"])
                            with gr.Row():
                                industrial_refresh_btn = gr.Button("🔄 刷新控制台", variant="primary", scale=1)
                                industrial_refresh_models_btn = gr.Button("🧱 只刷新模型审计", scale=1)
                        with gr.Group(elem_classes=["dashboard-grid"]):
                            with gr.Group(elem_classes=["dashboard-card"]):
                                industrial_ops_card = gr.Markdown(value=industrial_ops_default)
                            with gr.Group(elem_classes=["dashboard-card"]):
                                industrial_bottleneck_card = gr.Markdown(value=industrial_bottleneck_default)
                            with gr.Group(elem_classes=["dashboard-card"]):
                                industrial_model_audit_card = gr.Markdown(value=industrial_model_audit_default)
                            with gr.Group(elem_classes=["dashboard-card"]):
                                industrial_sop_card = gr.Markdown(value=industrial_sop_default)

                    # ─── 概览 ──────────────────────────────
                    with gr.TabItem("📺 概览"):
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 📺 项目概览")
                            gr.Markdown("这里集中看当前项目的可读内容与产出摘要，不再只是裸文本。", elem_classes=["workbench-note"])
                            view_md = gr.Markdown(value="运行管线后自动展示可读内容。")

                    # ─── 分镜 ──────────────────────────────
                    with gr.TabItem("🎞️ 分镜"):
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🎞️ 分镜审核台")
                            gr.Markdown("这里做审核、锁定、结构化修改和重渲染，不需要在多个小抽屉之间跳来跳去。", elem_classes=["workbench-note"])
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

                    # ─── 分镜工坊（新 UI）─────────────────────
                    with gr.TabItem("🎬 分镜工坊"):
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🎬 分镜工坊")
                            gr.Markdown("更适合集中处理镜头编排和镜头资产。", elem_classes=["workbench-note"])
                        from ui.workshop import build_workshop_tab
                        build_workshop_tab(app)

                    # ─── 剧本工作站 ───────────────────────────
                    with gr.TabItem("📖 剧本"):
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 📖 剧本工作站")
                            gr.Markdown("先看状态，再编辑，再决定要不要让 AI 辅助或重跑当前阶段。", elem_classes=["workbench-note"])
                        with gr.Row():
                            gr.Markdown("##### 状态")
                            script_step_refresh_btn = gr.Button("🔄", size="sm", scale=0, min_width=40)
                        script_step_status = gr.Markdown("加载项目后显示状态")
                        script_edit = gr.Textbox(label="剧本 JSON（可直接编辑）", lines=15)
                        with gr.Group(elem_classes=["choice-card"]):
                            gr.Markdown("### 🤖 AI 辅助")
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
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 👤 角色工作站")
                            gr.Markdown("角色设定、AI 辅助和单步重跑放在同一个面板里。", elem_classes=["workbench-note"])
                        char_edit = gr.Textbox(label="角色列表 JSON（可直接编辑）", lines=12)
                        with gr.Group(elem_classes=["choice-card"]):
                            gr.Markdown("### 🤖 AI 辅助")
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
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🏞️ 场景工作站")
                            gr.Markdown("场景描述、视觉优化和场景重跑收在同一层级。", elem_classes=["workbench-note"])
                        scene_edit = gr.Textbox(label="场景列表 JSON（可直接编辑）", lines=12)
                        with gr.Group(elem_classes=["choice-card"]):
                            gr.Markdown("### 🤖 AI 辅助")
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
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🎵 配乐工作站")
                            gr.Markdown("先看配乐状态，再编辑 prompt，再试听和单步生成。", elem_classes=["workbench-note"])
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
                        with gr.Group(elem_classes=["choice-card"]):
                            gr.Markdown("### 🤖 AI 辅助优化配乐描述")
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
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🔊 音效工作站")
                            gr.Markdown("音效描述、AI 优化和单步生成集中处理。", elem_classes=["workbench-note"])
                        sfx_edit = gr.Textbox(label="音效数据 JSON（可直接编辑）", lines=10)
                        with gr.Group(elem_classes=["choice-card"]):
                            gr.Markdown("### 🤖 AI 辅助优化音效描述")
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
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🎤 TTS 工作站")
                            gr.Markdown("对白查看、单 Shot 生成和全量生成统一在这里。", elem_classes=["workbench-note"])
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
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🎬 渲染工作站")
                            gr.Markdown("这里处理指定 Shot 或批量渲染，和上面的总渲染入口保持同一种结构。", elem_classes=["workbench-note"])
                        with gr.Row():
                            render_step_status_md = gr.Markdown(load_render_status(0))
                            render_status_refresh_btn = gr.Button("🔄 刷新", size="sm", scale=0, min_width=50)
                        gr.Markdown(
                            "渲染使用 ComfyUI + LTX 2.3 主链。已渲染的 Shot 自动跳过。",
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
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🎞️ 合成导出")
                            gr.Markdown("成片输出、路径记录和导出清单统一看这里。", elem_classes=["workbench-note"])
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
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 💬 字幕工作站")
                            gr.Markdown("字幕加载、编辑和保存不再散在其他地方。", elem_classes=["workbench-note"])
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
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### ▶️ 视频预览")
                            gr.Markdown("快速抽查某个 Shot 的落盘视频。", elem_classes=["workbench-note"])
                        with gr.Row():
                            shot_preview_id = gr.Number(label="Shot ID", value=0, precision=0, scale=1)
                            load_video_btn = gr.Button("▶️ 加载视频", scale=1)
                        shot_video_player = gr.Video(label="Shot 视频", interactive=False)
                        shot_video_status = gr.Markdown("")

                    # ─── 快速视频 ──────────────────────────────
                    with gr.TabItem("🤖 AI 编辑"):
                        with gr.Group(elem_classes=["tab-shell"]):
                            gr.Markdown("### 🤖 AI 联动编辑")
                            gr.Markdown("输入自然语言指令，AI 扫描所有受影响字段并预览变更。", elem_classes=["workbench-note"])
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

            # end 工作站 sub-tabs

            # ════════════════════════════════════════════════════════════════════
            #  Tab 4 · 系统
            # ════════════════════════════════════════════════════════════════════
            with gr.TabItem("⚙️ 系统"):
                with gr.Group(elem_classes=["tab-shell"]):
                    gr.Markdown("### ⚙️ 系统 · 模型管理")
                    gr.Markdown("系统状态、渲染参数、音频试听、模型浏览和下载。", elem_classes=["workbench-note"])

                    # ── 系统状态总览 ─────────────────────────
                    with gr.Group(elem_classes=["section-shell"]):
                        gr.Markdown("### 🖥️ 系统状态总览", elem_classes=["section-title"])
                        sys_status_md = gr.Markdown(get_system_status())
                        sys_refresh_btn = gr.Button("🔄 刷新状态", size="sm")

                    # ── 渲染参数配置 ─────────────────────────
                    with gr.Group(elem_classes=["section-shell"]):
                        gr.Markdown("### ⚙️ 渲染参数（Steps / CFG / 尺寸）", elem_classes=["section-title"])
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
                    with gr.Group(elem_classes=["section-shell"]):
                        gr.Markdown("### 🎤 TTS 配置 & 试听", elem_classes=["section-title"])
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
                    with gr.Group(elem_classes=["section-shell"]):
                        gr.Markdown("### 🎵 BGM 配置 & 试听", elem_classes=["section-title"])
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
                    with gr.Group(elem_classes=["section-shell"]):
                        gr.Markdown("### 🔍 已安装模型浏览", elem_classes=["section-title"])
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
                    with gr.Group(elem_classes=["section-shell"]):
                        gr.Markdown("### 📥 下载缺失模型", elem_classes=["section-title"])
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


        # ═════════ end main tabs ═════════════════════════════════════════════

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
        # ── 概念发现事件绑定 ────────────────────────
        concept_gen_btn.click(
            fn=concept_generate_flow,
            inputs=[concept_keywords, concept_requirements, concept_n,
                    concept_use_web, concept_model],
            outputs=[concept_data_state, concept_table, concept_select, concept_queue_log],
            concurrency_limit=2,
        )
        concept_add_btn.click(
            fn=concept_add_to_queue,
            inputs=[concept_select, concept_data_state, concept_queue_state],
            outputs=[concept_queue_state, concept_queue_md, concept_queue_select, concept_queue_log],
            queue=False,
        )
        concept_clear_queue_btn.click(
            fn=concept_clear_queue,
            inputs=[concept_queue_state],
            outputs=[concept_queue_state, concept_queue_md, concept_queue_select, concept_queue_log],
            queue=False,
        )
        concept_remove_btn.click(
            fn=concept_remove_from_queue,
            inputs=[concept_queue_select, concept_queue_state],
            outputs=[concept_queue_state, concept_queue_md, concept_queue_select, concept_queue_log],
            queue=False,
        )
        concept_fill_btn.click(
            fn=concept_fill_premise,
            inputs=[concept_select, concept_data_state],
            outputs=[premise],
            queue=False,
        )
        _concept_gen_event = concept_run_queue_btn.click(
            fn=concept_run_queue_flow,
            inputs=[
                concept_queue_state, project_name, genre, tone, acts, model,
                story_model, char_model, scene_model, art_model,
                genre_tags, tone_tags, emotion_arc, episode_count, project_format,
            ],
            outputs=[concept_queue_log, gen_results],
            concurrency_limit=1,
        )
        # stop/kill 必须在 gen_event 定义之后挂 cancels
        concept_stop_btn.click(
            fn=concept_stop_remaining_queue,
            inputs=[],
            outputs=[concept_queue_log],
            queue=False,
            cancels=[_concept_gen_event],
        )
        concept_kill_btn.click(
            fn=force_kill_comfyui,
            inputs=[],
            outputs=[concept_queue_log],
            queue=False,
            cancels=[_concept_gen_event],
        )

        # ── 主生成按钮 ───────────────────────────────
        gen_btn.click(
            fn=full_pipeline_flow,
            inputs=[premise, project_name, genre, tone, acts, model,
                    story_model, char_model, scene_model, art_model,
                    genre_tags, tone_tags, emotion_arc, episode_count, project_format],
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
        _render_gen_event = render_btn.click(
            fn=render_export_flow,
            inputs=[project_id_state, project_name, render_config_state],
            outputs=[render_log, render_results, project_id_state],
            concurrency_limit=2,
        )
        _render_gen_event.then(
            fn=load_industrial_dashboard,
            inputs=[project_id_state],
            outputs=[industrial_ops_card, industrial_bottleneck_card, industrial_model_audit_card, industrial_sop_card],
            queue=False,
        )

        # Phase 2: 续跑
        resume_btn.click(
            fn=resume_pipeline_flow,
            inputs=[project_id_state],
            outputs=[render_log, overall_progress_md],   # 一个日志 + 一个整体进度
        ).then(
            fn=get_pipeline_state,
            inputs=[project_id_state],
            outputs=[pipeline_state_md],
            queue=False,
        ).then(
            fn=load_industrial_dashboard,
            inputs=[project_id_state],
            outputs=[industrial_ops_card, industrial_bottleneck_card, industrial_model_audit_card, industrial_sop_card],
            queue=False,
        )

        render_stop_btn.click(
            fn=stop_rendering_now,
            inputs=[project_id_state],
            outputs=[render_log, pipeline_state_md],
            queue=False,
            cancels=[_render_gen_event],
        ).then(
            fn=load_industrial_dashboard,
            inputs=[project_id_state],
            outputs=[industrial_ops_card, industrial_bottleneck_card, industrial_model_audit_card, industrial_sop_card],
            queue=False,
        )
        render_kill_btn.click(
            fn=force_kill_comfyui,
            inputs=[],
            outputs=[render_log],
            queue=False,
            cancels=[_render_gen_event],
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

        # ── 首页 Step 1: 类型切换 ────────────────────────
        def _home_type_defaults(content_type: str):
            """根据内容类型更新首页视频默认值和说明文字。"""
            if "短剧" in content_type:
                return (
                    gr.update(value=15, minimum=6, maximum=60),
                    gr.update(value="竖屏 9:16"),
                    "> **短剧模式**：竖屏 9:16，每集 3–5 分钟，节奏快、爽点密集，典型红果/抖音短剧风格。",
                )
            elif "电影" in content_type:
                return (
                    gr.update(value=90, minimum=30, maximum=300),
                    gr.update(value="横屏 16:9"),
                    "> **电影模式**：横屏 16:9，叙事完整，场面调度丰富，适合长片/微电影。",
                )
            else:  # 短视频
                return (
                    gr.update(value=30, minimum=10, maximum=180),
                    gr.update(value="竖屏 9:16"),
                    "> **短视频模式**：广告/Vlog/带货/10-15 分钟内容，竖屏为主，节奏适中。",
                )

        def _home_type_note(content_type: str):
            if "短剧" in content_type:
                return "**短剧**：竖屏 9:16，每集 3–5 分钟，节奏快、爽点密集，典型红果/抖音短剧风格。"
            elif "电影" in content_type:
                return "**电影**：横屏 16:9，叙事完整，场面调度丰富，适合长片/微电影创作。"
            else:
                return "**短视频**：广告/Vlog/带货/10-15 分钟以内，竖屏为主，平台适配性强。"

        home_content_type.change(
            fn=_home_type_defaults,
            inputs=[home_content_type],
            outputs=[home_duration, home_aspect, home_target_note],
            queue=False,
        )
        home_content_type.change(
            fn=_home_type_note,
            inputs=[home_content_type],
            outputs=[home_type_note],
            queue=False,
        )

        # ── 首页 Step 3: 做视频按钮 ──────────────────────
        def _home_content_type_to_product(content_type: str) -> str:
            if "短剧" in content_type:
                return "做短剧"
            elif "电影" in content_type:
                return "做电影"
            return "做短视频"

        _QUALITY_MAP = {
            "标准（540p）":    "标准",
            "高清（720p）":    "720p",
            "全高清（1080p）": "1080p",
        }

        def _home_qv_flow(premise_text, content_type, execution_route, duration,
                          aspect, quality_label, bgm_prompt, crossfade,
                          cloud_api_key, cloud_api_base, cloud_model):
            product_target = _home_content_type_to_product(content_type)
            if not (premise_text or "").strip():
                yield "⚠️ 请先在上方「梗概 / 热词」框里输入内容再做视频。", gr.update(visible=False), ""
                return
            quality = _QUALITY_MAP.get(quality_label or "", "720p")
            yield from quick_video_flow(
                premise_text, bgm_prompt, duration, aspect,
                product_target, execution_route,
                cloud_api_key, cloud_api_base, cloud_model,
                "",    # pipeline (auto)
                None,  # reference_image
                crossfade,
                "",    # workflow_template (auto)
                "",    # render_strategy (auto)
                "",    # audio_strategy (auto)
                quality,
            )

        _home_gen_event = home_qv_btn.click(
            fn=_home_qv_flow,
            inputs=[
                premise,
                home_content_type, home_execution_route,
                home_duration, home_aspect, home_quality,
                home_bgm_prompt, home_crossfade,
                home_cloud_api_key, home_cloud_api_base, home_cloud_model,
            ],
            outputs=[home_qv_log, home_qv_video, home_qv_status],
            concurrency_limit=1,
        )
        _home_gen_event.then(
            fn=lambda v: gr.update(visible=bool(v)),
            inputs=[home_qv_video],
            outputs=[home_qv_video],
            queue=False,
        )

        def _home_soft_stop():
            msg, _ = stop_rendering_now(0)
            return msg, ""

        # 🛑 软停止：取消 Gradio generator + 发 ComfyUI interrupt
        home_stop_btn.click(
            fn=_home_soft_stop,
            inputs=[],
            outputs=[home_qv_log, home_qv_status],
            queue=False,
            cancels=[_home_gen_event],
        )

        # ☢️ 强杀：取消 Gradio generator + SIGKILL ComfyUI + 重启
        home_kill_btn.click(
            fn=force_kill_comfyui,
            inputs=[],
            outputs=[home_qv_log],
            queue=False,
            cancels=[_home_gen_event],
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
    app.launch(server_name="127.0.0.1", server_port=7860, share=False, show_error=True,
               theme=_STUDIO_THEME, css=CUSTOM_CSS)
