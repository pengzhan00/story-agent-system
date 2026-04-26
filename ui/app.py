#!/usr/bin/env python3
"""
漫剧故事工坊 — 两步走 UI
Phase 1: 一键生成全部内容（不渲染）→ 可读查看 + JSON 编辑
Phase 2: 渲染 + 导出（用编辑后的数据）
"""
import sys, os, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import gradio as gr

from core.database import init_db, get_script, list_scripts, list_characters, list_scene_assets, list_music, list_sfx
from core.database import (
    update_script, update_character, update_scene_asset, update_music, update_sfx,
    get_project, list_shots, list_episodes, list_render_jobs
)
from core.ollama_client import list_models, refresh_models, resolve_model_profile
from core.orchestrator import run_pipeline_generator, run_render_export_generator
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
    rows = []
    for shot in list_shots(project_id=pid):
        characters = json.loads(shot.characters) if shot.characters else []
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
        ])
    return rows


def format_production_overview(pid: int) -> str:
    if not pid:
        return "运行管线后自动展示生产指标。"
    proj = get_project(pid)
    episodes = list_episodes(pid)
    shots = list_shots(project_id=pid)
    ready = sum(1 for s in shots if s.status == "ready")
    rendered = sum(1 for s in shots if s.status == "rendered")
    return "\n".join([
        "### 🏭 生产总览",
        f"- 项目: {proj.name if proj else '未知'}",
        f"- 集数: {len(episodes)}",
        f"- 分镜数: {len(shots)}",
        f"- 待渲染: {ready}",
        f"- 已渲染: {rendered}",
    ])


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


# ─── Phase 1: 全流程生成 ────────────────────────────

def full_pipeline_flow(premise, project_name, genre, tone, acts, model,
                       progress=gr.Progress()):
    """yield (gen_log, gen_result, view_md, edit_data..., pid)"""
    if not premise or not premise.strip():
        yield ("### ⚠️ 请先输入创作构想", None, "", "", "", "", "", "", "", [], 0)
        return

    result = None
    pid = 0
    try:
        for pct, log_md, partial in run_pipeline_generator(
            premise=premise.strip(),
            project_name=project_name.strip() if project_name else "",
            genre=genre or "玄幻", tone=tone or "热血",
            acts=int(acts) if acts else 3,
            model=model or "qwen2.5:7b",
            enable_render=False,
        ):
            progress(pct)
            result = partial
            yield (log_md, partial, gr.update(), gr.update(), gr.update(),
                   gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
                   result.get("project_id", 0) if result else 0)
    except Exception as e:
        import traceback
        yield (f"### ❌ 管线崩溃\n```\n{e}\n{traceback.format_exc()[-500:]}\n```",
               result, "", "", "", "", "", "", "", [], 0)
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
        pid,
    )


# ─── Phase 2: 渲染导出 ─────────────────────────────

def render_export_flow(pid, project_name, progress=gr.Progress()):
    """yield (render_log, render_result, render_pid)"""
    if not pid:
        yield ("### ⚠️ 请先生成内容", None, 0)
        return

    proj = get_project(pid)
    pname = project_name or (proj.name if proj else "")

    result = None
    try:
        for pct, log_md, partial in run_render_export_generator(
            project_id=pid, project_name=pname,
        ):
            progress(pct)
            result = partial
            yield (log_md, partial, pid)
    except Exception as e:
        import traceback
        yield (f"### ❌ 渲染出错\n```\n{e}\n{traceback.format_exc()[-500:]}\n```",
               result, pid)


# ─── 构建 UI ─────────────────────────────────────────

def build_ui():
    init_db()
    models = get_ollama_models()
    default_model = models[0] if models else "qwen2.5:7b"

    with gr.Blocks(title="🎬 漫剧故事工坊") as app:
        app.queue(default_concurrency_limit=5)

        gr.Markdown("# 🎬 漫剧故事工坊")
        gr.Markdown("**两步走**: ① 生成全部内容（可编辑） → ② 渲染导出成片")

        # ══════ Phase 1: 内容生成 ═══════════════════
        gr.Markdown("## 📝 Phase 1: 内容生成")

        premise = gr.Textbox(label="创作构想", lines=6,
            placeholder="输入故事创意...")

        with gr.Row():
            project_name = gr.Textbox(label="项目名称", placeholder="留空自动生成", scale=1)
            genre = gr.Dropdown(label="类型",
                choices=["玄幻","仙侠","都市","科幻","奇幻","武侠","历史","悬疑","恐怖","言情","校园","末日"],
                value="玄幻", scale=1)
            tone = gr.Dropdown(label="基调",
                choices=["热血","温馨","黑暗","搞笑","治愈","悬疑","史诗","浪漫","轻松","沉重"],
                value="热血", scale=1)
            acts = gr.Slider(label="幕数", minimum=1, maximum=5, value=3, step=1, scale=1)

        with gr.Row():
            model = gr.Dropdown(label="模型", choices=models or ["qwen2.5:7b"],
                value=default_model, allow_custom_value=True, scale=2)
        model_profile_md = gr.Markdown(format_model_profile(default_model))
        model.change(fn=format_model_profile, inputs=[model], outputs=[model_profile_md])

        with gr.Row():
            gen_btn = gr.Button("🔥 一键全流程生成", variant="primary", size="lg", scale=2)
            clear_btn = gr.Button("🗑️ 清空", size="lg", scale=1)

        gen_log = gr.Markdown("### 📋 管线日志\n等待启动...")
        gen_results = gr.JSON(value=None, label="生成结果摘要")

        # ══════ Phase 2: 渲染导出 ═══════════════════
        gr.Markdown("---")
        gr.Markdown("## 🎬 Phase 2: 渲染 + 导出")
        gr.Markdown("用数据库中最新内容渲染视频。可先编辑内容再执行。")

        render_btn = gr.Button("🎬 渲染导出", variant="secondary", size="lg",
                               elem_classes="gr-button-secondary")
        render_log = gr.Markdown("点击「渲染导出」开始...")
        render_results = gr.JSON(value=None, label="渲染结果")

        # ══════ 状态变量 ════════════════════════════
        project_id_state = gr.State(0)

        # ══════ 查看 + 编辑区 ════════════════════════
        gr.Markdown("---")
        gr.Markdown("## ✏️ 查看 & 编辑内容")
        production_overview = gr.Markdown("运行管线后自动展示生产指标。")

        with gr.Tabs():
            with gr.TabItem("📺 概览"):
                view_md = gr.Markdown(value="运行管线后自动展示可读内容。")

            with gr.TabItem("🎞️ 分镜"):
                shot_table = gr.Dataframe(
                    headers=["ID", "Act", "Scene", "Shot", "场景", "镜头", "情绪", "角色", "状态"],
                    value=[],
                    interactive=False,
                    label="分镜列表",
                )

            with gr.TabItem("📖 剧本"):
                script_edit = gr.Textbox(label="剧本 JSON", lines=15)
                with gr.Row():
                    save_script_btn = gr.Button("💾 保存剧本", elem_classes="save-btn", scale=1)
                    script_status = gr.Markdown("")

            with gr.TabItem("👤 角色"):
                char_edit = gr.Textbox(label="角色列表 JSON", lines=12)
                with gr.Row():
                    save_char_btn = gr.Button("💾 保存角色", elem_classes="save-btn", scale=1)
                    char_status = gr.Markdown("")

            with gr.TabItem("🏞️ 场景"):
                scene_edit = gr.Textbox(label="场景列表 JSON", lines=12)
                with gr.Row():
                    save_scene_btn = gr.Button("💾 保存场景", elem_classes="save-btn", scale=1)
                    scene_status = gr.Markdown("")

            with gr.TabItem("🎵 音乐"):
                music_edit = gr.Textbox(label="音乐数据 JSON", lines=10)
                with gr.Row():
                    save_music_btn = gr.Button("💾 保存音乐", elem_classes="save-btn", scale=1)
                    music_status = gr.Markdown("")

            with gr.TabItem("🔊 音效"):
                sfx_edit = gr.Textbox(label="音效数据 JSON", lines=10)
                with gr.Row():
                    save_sfx_btn = gr.Button("💾 保存音效", elem_classes="save-btn", scale=1)
                    sfx_status = gr.Markdown("")

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
                    value=[],
                    interactive=False,
                    label="近 30 条编辑记录",
                )
                refresh_history_btn = gr.Button("🔄 刷新历史", size="sm")

            with gr.TabItem("🎞️ 视频预览"):
                gr.Markdown("### Shot 视频预览")
                with gr.Row():
                    shot_preview_id = gr.Number(label="Shot ID", value=0, precision=0, scale=1)
                    load_video_btn = gr.Button("▶️ 加载视频", scale=1)
                shot_video_player = gr.Video(label="Shot 视频", interactive=False)
                shot_video_status = gr.Markdown("")

                gr.Markdown("### 集数合成视频")
                episode_video_path = gr.Textbox(
                    label="集数视频路径（生成后自动填入）",
                    interactive=False,
                )

        # ══════ 事件绑定 ═════════════════════════════

        # Phase 1: 全流程生成
        gen_outputs = [
            gen_log, gen_results,
            view_md,
            script_edit, char_edit, scene_edit, music_edit, sfx_edit,
            production_overview, shot_table,
            project_id_state,
        ]
        gen_btn.click(
            fn=full_pipeline_flow,
            inputs=[premise, project_name, genre, tone, acts, model],
            outputs=gen_outputs,
            concurrency_limit=2,
        )

        # 清空（绕过 queue，防止被生成器堵住）
        clear_btn.click(
            fn=lambda: (
                "### 📋 管线日志\n等待启动...", None,
                "运行管线后自动展示可读内容。",
                "", "", "", "", "", "运行管线后自动展示生产指标。", [], 0,
            ),
            inputs=[],
            outputs=gen_outputs,
            queue=False,
        )

        # Phase 2: 渲染导出（生成器，需要 queue 流式输出）
        render_btn.click(
            fn=render_export_flow,
            inputs=[project_id_state, project_name],
            outputs=[render_log, render_results, project_id_state],
            concurrency_limit=2,
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

    return app


if __name__ == "__main__":
    app = build_ui()
    app.launch(server_name="127.0.0.1", server_port=7860, share=False, show_error=True, css=CUSTOM_CSS)
