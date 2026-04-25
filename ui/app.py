"""
Story Agent System — Gradio Web UI
Tabs: 项目管理 | ✍️ 剧本 | 👤 角色 | 🏞️ 场景 | 🎨 美术指导 | 🎵 音乐 | 🔊 音效 | 🎬 渲染 | 📦 导出 | ⚙️ 设置
"""
import gradio as gr
import json
import os
import time

from core.database import (
    init_db, list_projects, get_project, create_project, update_project, delete_project,
    list_scripts, get_script, create_script,
    list_characters, get_character, create_character,
    list_scene_assets, get_scene_asset,
    list_music, get_music,
    list_sfx, list_prompts, list_logs,
)
from core.ollama_client import list_models, refresh_models
from agents.director.core import analyze_request, summarize_project
from agents.writer.core import generate_storyline
from agents.character_designer.core import design_character
from agents.scene_designer.core import design_scene
from agents.art_director.core import define_color_palette, design_camera_language, review_visual_consistency
from agents.composer.core import compose_theme, compose_bgm
from agents.sound_designer.core import design_soundscape
from agents.voice_actor.core import run_action as run_voice_actor
from agents.reviewer.core import run_action as run_reviewer

APP_TITLE = "🎬 漫剧故事工坊 — Multi-Agent Story Forge"
APP_DESC = "本地 Ollama 多 Agent 智能剧本创作系统"
CURRENT_PROJECT_ID = {"val": 0}
CURRENT_MODEL = {"val": "gemma4:latest"}

THEME_CSS = """
footer {display:none !important}
.gradio-container {max-width: 1500px !important}
.tabs {margin-top: 0}
h1 {font-size: 1.8em !important; margin-bottom: 0.2em !important}
h2 {font-size: 1.3em !important; margin-top: 0.5em !important}
.monotext {font-family: 'SF Mono', 'Monaco', monospace; font-size: 0.9em}
.status-ok {color: #22c55e; font-weight: bold}
.status-warn {color: #eab308; font-weight: bold}
.status-err {color: #ef4444; font-weight: bold}
.progress-bar {height: 6px; border-radius: 3px}
"""

# ─────────── Helpers ───────────

def _ensure_project():
    projs = list_projects()
    if not projs:
        pid = create_project({"name": "示例项目","description": "自动创建","genre": "玄幻","status": "draft"})
        return pid
    return projs[0].id

def _refresh_projects():
    projs = list_projects()
    return gr.Dropdown(choices=[(p.name, p.id) for p in projs], value=projs[0].id if projs else None)

def _get_proj_info(pid: int) -> str:
    p = get_project(pid)
    if not p: return "请选择项目"
    return f"**{p.name}** | {p.genre} | 状态: {p.status} | {p.description}"

def _format_script_preview(sid: int) -> str:
    s = get_script(sid)
    if not s: return "无剧本"
    acts = s.get_acts()
    lines = [f"## {s.title}", f"梗概: {s.synopsis[:200]}", f"共 {s.total_scenes} 场戏", ""]
    for act in acts:
        lines.append(f"### 第{act['number']}幕: {act.get('title','')}")
        for sc in act.get("scenes", []):
            chars = ', '.join(sc.get("characters",[]))
            lines.append(f"  [{sc.get('number')}] {sc.get('location','')} | {sc.get('time_of_day','')} | 情绪:{sc.get('mood','')} | 角色:{chars}")
    return "\n".join(lines)

def _get_models():
    return list_models() or ["gemma4:latest", "deepseek-r1:70b"]

def _comfyui_status():
    import requests as req
    try:
        r = req.get("http://127.0.0.1:8188/queue", timeout=3)
        q = r.json()
        running = q.get("queue_running",0)
        pending = len(q.get("queue_pending",[]))
        return f"✅ 在线 (运行:{running} 排队:{pending})"
    except:
        return "❌ 离线"

def _ollama_status():
    import requests as req
    try:
        r = req.get("http://localhost:11434/api/tags", timeout=3)
        models = r.json().get("models",[])
        return f"✅ 在线 ({len(models)} 模型)"
    except:
        return "❌ 离线"


# ═══════════════════════════════════════════════════════
#  Gradio UI Builder
# ═══════════════════════════════════════════════════════

def build_ui():
    init_db()
    default_pid = _ensure_project()
    CURRENT_PROJECT_ID["val"] = default_pid

    with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft(), css=THEME_CSS) as app:
        gr.Markdown(f"# {APP_TITLE}")
        gr.Markdown(f"*{APP_DESC}*  |  ComfyUI: {_comfyui_status()}  |  Ollama: {_ollama_status()}")

        # ─── 状态栏 ──────────────────────────────
        with gr.Row():
            proj_dd = gr.Dropdown(label="📁 当前项目", choices=[], scale=3, interactive=True)
            refresh_btn = gr.Button("🔄 刷新", scale=1, variant="secondary")
            proj_info = gr.Markdown("请选择项目")

        # ─── Tab 容器 ────────────────────────────
        with gr.Tabs() as tabs:
            # ══════ Tab 1: 项目管理 ═════════════════
            with gr.TabItem("📋 项目管理", id="project"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 新建项目")
                        new_name = gr.Textbox(label="项目名称", value="")
                        new_genre = gr.Dropdown(label="类型", choices=["玄幻","仙侠","都市","科幻","悬疑","言情","喜剧","冒险"], value="玄幻")
                        new_desc = gr.Textbox(label="简介", lines=2)
                        create_btn = gr.Button("✨ 创建项目", variant="primary")
                        del_btn = gr.Button("🗑️ 删除项目", variant="stop")

                    with gr.Column(scale=2):
                        gr.Markdown("### 🚀 一键全流程")
                        premise = gr.Textbox(label="创作构想", lines=4,
                            placeholder="输入故事创意，如: 一个普通高中生意外穿越到修仙世界，发现自己拥有罕见的雷灵根...")
                        with gr.Row():
                            acts_num = gr.Slider(label="幕数", minimum=1, maximum=5, value=3, step=1)
                            tone_dd = gr.Dropdown(label="基调", choices=["热血","治愈","悬疑","搞笑","黑暗","温馨","奇幻","史诗"], value="热血")
                            one_click_btn = gr.Button("🔥 一键全流程启动", variant="primary", size="lg")
                            render_toggle = gr.Checkbox(label="启用渲染", value=True)

                        pipeline_log = gr.Markdown("### 管线日志\n等待启动...")
                        pipeline_progress = gr.Progress()

                        # 一键全流程 callback
                        def run_pipeline(premise, name, genre, acts, tone_, render_on, proj_state):
                            from core.orchestrator import run_one_click_pipeline
                            last_msg = ""
                            result = run_one_click_pipeline(
                                premise=premise, project_name=name or "",
                                genre=genre, tone=tone_, acts=int(acts),
                                render_enabled=render_on,
                            )
                            # 刷新项目列表
                            projs = list_projects()
                            choices = [(p.name, p.id) for p in projs]
                            log_text = "### 📋 管线结果\n"
                            if result.get("error"):
                                log_text += f"❌ 错误: {result['error']}\n"
                            log_text += f"- 项目ID: {result.get('project_id',0)}\n"
                            log_text += f"- 剧本ID: {result.get('script_id',0)}\n"
                            log_text += f"- 角色: {len(result.get('characters',[]))} 个\n"
                            log_text += f"- 场景: {len(result.get('scenes',[]))} 个\n"
                            log_text += f"- 音乐: {len(result.get('music',[]))} 个\n"
                            log_text += f"- 视频: {len(result.get('videos',[]))} 个\n"
                            if result.get("merged_video"):
                                log_text += f"- 📦 合并视频: {result['merged_video']}\n"
                            return log_text, gr.Dropdown(choices=choices, value=result.get('project_id', proj_state))
                        one_click_btn.click(
                            fn=run_pipeline,
                            inputs=[premise, new_name, new_genre, acts_num, tone_dd, render_toggle, proj_dd],
                            outputs=[pipeline_log, proj_dd],
                        )

                # 项目列表
                proj_list = gr.Dataframe(
                    headers=["ID","名称","类型","状态","创建时间"],
                    label="所有项目", interactive=False,
                )
                def refresh_proj_list():
                    projs = list_projects()
                    return [[p.id, p.name, p.genre, p.status, p.created_at[:10]] for p in projs]
                refresh_btn.click(fn=refresh_proj_list, outputs=proj_list)
                app.load(fn=refresh_proj_list, outputs=proj_list)

            # ══════ Tab 2: 剧本 ════════════════════
            with gr.TabItem("✍️ 剧本", id="script"):
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### 生成剧本")
                        gen_premise = gr.Textbox(label="故事前提", lines=3,
                            placeholder="输入故事梗概，或留空用项目描述")
                        gen_btn = gr.Button("🎬 生成剧本", variant="primary")
                    with gr.Column(scale=2):
                        gr.Markdown("### 剧本预览")
                        script_preview = gr.Markdown("请先生成或选择项目")
                gen_btn.click(
                    fn=lambda prem, pid: (_format_script_preview(
                        create_script({"project_id": pid, "title": "新剧本","synopsis": prem or "待生成","acts": "[]"})
                    ) if prem else "请输入前提"),
                    inputs=[gen_premise, proj_dd], outputs=script_preview
                )

            # ══════ Tab 3: 角色 ════════════════════
            with gr.TabItem("👤 角色", id="characters"):
                gr.Markdown("### 角色管理")
                with gr.Row():
                    with gr.Column(scale=1):
                        char_name = gr.Textbox(label="角色名")
                        char_btn = gr.Button("✨ 设计角色", variant="primary")
                    with gr.Column(scale=2):
                        char_out = gr.Markdown("输入角色名后点击设计")
                char_btn.click(
                    fn=lambda name, pid: json.dumps(
                        design_character(name, f"项目#{pid}", "玄幻", "热血", pid), 
                        ensure_ascii=False, indent=2
                    ),
                    inputs=[char_name, proj_dd], outputs=char_out
                )

            # ══════ Tab 4: 场景 ════════════════════
            with gr.TabItem("🏞️ 场景", id="scenes"):
                gr.Markdown("### 场景设计")
                with gr.Row():
                    with gr.Column(scale=1):
                        sc_name = gr.Textbox(label="场景名称")
                        sc_mood = gr.Dropdown(label="情绪基调", choices=["平静","神秘","紧张","欢乐","悲伤","壮丽","压抑","温馨"], value="平静")
                        sc_btn = gr.Button("🏗️ 设计场景", variant="primary")
                    with gr.Column(scale=2):
                        sc_out = gr.Markdown("输入场景信息后点击设计")
                sc_btn.click(
                    fn=lambda name, mood, pid: json.dumps(
                        design_scene(name, mood, "白天", pid),
                        ensure_ascii=False, indent=2
                    ),
                    inputs=[sc_name, sc_mood, proj_dd], outputs=sc_out
                )

            # ══════ Tab 5: 美术指导 ── 新增 ─────
            with gr.TabItem("🎨 美术指导", id="art"):
                gr.Markdown("### 🎨 美术指导 — 视觉风格统一")
                with gr.Tabs():
                    with gr.TabItem("🎨 色调板"):
                        with gr.Row():
                            with gr.Column(scale=1):
                                art_project = gr.Textbox(label="项目名")
                                art_genre = gr.Dropdown(label="类型", choices=["玄幻","仙侠","都市","科幻","悬疑","言情","喜剧","冒险"], value="玄幻")
                                art_tone = gr.Textbox(label="基调", value="热血")
                                palette_btn = gr.Button("🎨 生成色调板", variant="primary")
                            with gr.Column(scale=2):
                                palette_out = gr.JSON(label="色调方案")
                        palette_btn.click(
                            fn=lambda pid, genre, tone: define_color_palette(
                                get_project(pid).name if get_project(pid) else "项目",
                                genre, tone, pid
                            ) if pid else {"error":"请先选择项目"},
                            inputs=[proj_dd, art_genre, art_tone], outputs=palette_out
                        )

                    with gr.TabItem("🎥 镜头语言"):
                        with gr.Row():
                            with gr.Column(scale=1):
                                cam_genre = gr.Dropdown(label="类型", choices=["玄幻","仙侠","都市","科幻","悬疑","言情","喜剧","冒险"], value="玄幻")
                                cam_moods = gr.Textbox(label="情绪序列(逗号分隔)", value="神秘,紧张,欢乐,悲伤,壮丽",
                                    placeholder="eg: 平静,紧张,高潮,结局")
                                cam_btn = gr.Button("🎥 设计镜头语言", variant="primary")
                            with gr.Column(scale=2):
                                cam_out = gr.JSON(label="镜头方案")
                        cam_btn.click(
                            fn=lambda genre, moods_str, pid: design_camera_language(
                                genre, [m.strip() for m in moods_str.split(",") if m.strip()], pid
                            ),
                            inputs=[cam_genre, cam_moods, proj_dd], outputs=cam_out
                        )

                    with gr.TabItem("✅ 一致性检查"):
                        with gr.Row():
                            with gr.Column(scale=1):
                                check_btn = gr.Button("🔍 检查视觉一致性", variant="primary")
                            with gr.Column(scale=2):
                                check_out = gr.Markdown("检查项目中角色与场景的视觉一致性")
                        check_btn.click(
                            fn=lambda pid: json.dumps(
                                review_visual_consistency(pid) if pid else {"error":"请选择项目"},
                                ensure_ascii=False, indent=2
                            ),
                            inputs=[proj_dd], outputs=check_out
                        )

            # ══════ Tab 6: 音乐 ════════════════════
            with gr.TabItem("🎵 音乐", id="music"):
                gr.Markdown("### 🎵 音乐创作")
                with gr.Tabs():
                    with gr.TabItem("🎼 主题曲"):
                        with gr.Row():
                            with gr.Column(scale=1):
                                mt_project = gr.Textbox(label="项目名", value=lambda: get_project(CURRENT_PROJECT_ID["val"]).name if get_project(CURRENT_PROJECT_ID["val"]) else "")
                                mt_genre = gr.Dropdown(label="类型", choices=["玄幻","仙侠","都市","科幻"], value="玄幻")
                                mt_tone = gr.Textbox(label="基调", value="热血")
                                theme_btn = gr.Button("🎼 创作主题曲", variant="primary")
                            with gr.Column(scale=2):
                                theme_out = gr.JSON(label="主题曲方案")
                        theme_btn.click(
                            fn=lambda proj_name, genre, tone, pid: compose_theme(proj_name, genre, tone, "", "epic", pid),
                            inputs=[mt_project, mt_genre, mt_tone, proj_dd], outputs=theme_out
                        )

                    with gr.TabItem("🎵 场景BGM"):
                        with gr.Row():
                            with gr.Column(scale=1):
                                bgm_desc = gr.Textbox(label="场景描述", lines=2, placeholder="森林中的追逐戏")
                                bgm_mood = gr.Dropdown(label="情绪", choices=["平静","悬疑","紧张","欢乐","悲伤","壮丽","温馨","浪漫"], value="紧张")
                                bgm_btn = gr.Button("🎵 生成BGM", variant="primary")
                            with gr.Column(scale=2):
                                bgm_out = gr.JSON(label="BGM方案")
                        bgm_btn.click(
                            fn=lambda desc, mood, genre, pid: compose_bgm(desc, mood, genre, pid),
                            inputs=[bgm_desc, bgm_mood, mt_genre, proj_dd], outputs=bgm_out
                        )

            # ══════ Tab 7: 音效 ════════════════════
            with gr.TabItem("🔊 音效", id="sfx"):
                gr.Markdown("### 🔊 音效设计")
                with gr.Row():
                    with gr.Column(scale=1):
                        sfx_project = gr.Textbox(label="项目名")
                        sfx_scenes = gr.Number(label="场景数量", value=5, minimum=1, maximum=50)
                        sfx_btn = gr.Button("🔊 设计音效方案", variant="primary")
                    with gr.Column(scale=2):
                        sfx_out = gr.JSON(label="音效方案")
                sfx_btn.click(
                    fn=lambda name, scenes, pid: design_soundscape(name, "玄幻", int(scenes), pid),
                    inputs=[sfx_project, sfx_scenes, proj_dd], outputs=sfx_out
                )

            # ══════ Tab 8: 渲染 ── 新增 ─────────
            with gr.TabItem("🎬 渲染", id="render"):
                gr.Markdown("### 🎬 动画渲染 — 剧本场景 → ComfyUI → 视频")
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("#### ComfyUI 状态")
                        comfy_status = gr.Markdown(f"ComfyUI: {_comfyui_status()}")
                        refresh_comfy = gr.Button("🔄 刷新状态", size="sm")

                        gr.Markdown("#### 选择渲染场景")
                        scene_list = gr.Dropdown(label="场景", choices=[], multiselect=True)
                        refresh_scenes_btn = gr.Button("📋 获取场景列表", size="sm")

                        render_btn = gr.Button("🎬 开始渲染", variant="primary", size="lg")
                        render_progress = gr.Progress()

                    with gr.Column(scale=2):
                        gr.Markdown("#### 渲染日志")
                        render_log = gr.Markdown("等待渲染...")
                        gr.Markdown("#### 视频库")
                        video_gallery = gr.Gallery(label="已生成视频", columns=3, height=300)

                # 获取场景列表
                def get_scene_list(pid):
                    from core.database import list_scripts
                    scripts = list_scripts(pid)
                    if not scripts:
                        return gr.Dropdown(choices=[])
                    s = scripts[0]
                    acts = s.get_acts()
                    choices = []
                    for act in acts:
                        for sc in act.get("scenes", []):
                            label = f"[{act['number']}.{sc.get('number','')}] {sc.get('location','')}"
                            choices.append((label, json.dumps(sc, ensure_ascii=False)))
                    if not choices:
                        choices = [("(无场景)", "")]
                    return gr.Dropdown(choices=choices)

                refresh_scenes_btn.click(fn=get_scene_list, inputs=[proj_dd], outputs=scene_list)

                # 渲染
                def do_render(scene_json_list, pid):
                    from pipelines.batch_renderer import BatchRenderer
                    import shutil
                    
                    if not scene_json_list:
                        return "请选择场景", None
                    
                    scenes = [json.loads(s) for s in scene_json_list]
                    renderer = BatchRenderer(
                        get_project(pid).name if get_project(pid) else f"project_{pid}",
                        pid
                    )
                    
                    log_lines = [f"🎬 开始渲染 {len(scenes)} 个场景..."]
                    videos = []
                    for idx, sc in enumerate(scenes):
                        log_lines.append(f"  渲染 [{idx+1}/{len(scenes)}]: {sc.get('location','')}")
                        video = renderer.render_scene(sc, scene_id=f"render_{idx+1:03d}")
                        if video:
                            videos.append(video)
                            log_lines.append(f"  ✅ {video}")
                        else:
                            log_lines.append(f"  ⚠️ 渲染失败")
                    
                    log_lines.append(f"\n✅ 完成: {len(videos)}/{len(scenes)} 成功")
                    return "\n".join(log_lines), videos

                render_btn.click(
                    fn=do_render,
                    inputs=[scene_list, proj_dd],
                    outputs=[render_log, video_gallery],
                )

            # ══════ Tab 9: 导出 ── 新增 ─────────
            with gr.TabItem("📦 导出", id="export"):
                gr.Markdown("### 📦 导出与合并")
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("#### 合并视频")
                        ep_num = gr.Number(label="剧集号", value=1, minimum=1)
                        output_name = gr.Textbox(label="输出文件名", placeholder="自动生成")
                        merge_btn = gr.Button("🎬 合并当前项目视频", variant="primary")

                        gr.Markdown("---")
                        gr.Markdown("#### 导出成品")
                        export_btn = gr.Button("📦 导出项目包 (ZIP)", variant="primary")

                    with gr.Column(scale=2):
                        gr.Markdown("#### 输出目录")
                        export_log = gr.Markdown("等待操作...")
                        export_file = gr.File(label="下载")

                # 合并
                def do_merge(pid, ep, out_name):
                    from pipelines.output_manager import load_timeline, merge_episode
                    proj = get_project(pid)
                    if not proj:
                        return "项目不存在", None
                    if not out_name:
                        out_name = f"{proj.name}_EP{int(ep):02d}"
                    
                    merged = merge_episode(proj.name, episode=int(ep), overwrite=True)
                    if merged:
                        return f"✅ 合并完成: {merged}", str(merged) if os.path.exists(merged) else None
                    else:
                        # 检查原因
                        tl = load_timeline(proj.name)
                        scenes = tl.get("scenes", [])
                        if not scenes:
                            return "⚠️ 没有已渲染的场景视频，请先在「渲染」Tab生成视频", None
                        return f"⚠️ 合并失败（已有 {len(scenes)} 个场景，可能未生成完整）", None

                merge_btn.click(fn=do_merge, inputs=[proj_dd, ep_num, output_name], outputs=[export_log, export_file])

                # 导出ZIP
                def do_export(pid):
                    import zipfile
                    from pipelines.output_manager import OUTPUT_DIR
                    proj = get_project(pid)
                    if not proj:
                        return "项目不存在", None
                    
                    proj_output = OUTPUT_DIR / "projects" / proj.name
                    if not proj_output.exists():
                        return "该项目没有输出文件", None
                    
                    zip_path = OUTPUT_DIR / "exports" / f"{proj.name}_export.zip"
                    zip_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                        for fpath in proj_output.rglob("*"):
                            if fpath.is_file():
                                zf.write(fpath, fpath.relative_to(proj_output))
                    
                    return f"✅ 导出完成: {zip_path} ({zip_path.stat().st_size/1024/1024:.1f}MB)", str(zip_path)

                export_btn.click(fn=do_export, inputs=[proj_dd], outputs=[export_log, export_file])

            # ══════ Tab 10: 设置 ═════════════════
            with gr.TabItem("⚙️ 设置", id="settings"):
                gr.Markdown("### ⚙️ 系统设置")
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("#### Ollama 模型")
                        model_dd = gr.Dropdown(label="默认模型", choices=_get_models(), value=CURRENT_MODEL["val"])
                        refresh_models_btn = gr.Button("🔄 刷新模型列表", size="sm")
                        def refresh_ollama_models():
                            models = refresh_models()
                            return gr.Dropdown(choices=models, value=models[0] if models else "N/A")
                        refresh_models_btn.click(fn=refresh_ollama_models, outputs=model_dd)

                        def set_model(m):
                            CURRENT_MODEL["val"] = m
                            return f"已设置默认模型: {m}"
                        model_dd.change(fn=set_model, inputs=[model_dd], outputs=[gr.Markdown(visible=False)])

                    with gr.Column(scale=2):
                        gr.Markdown("#### 系统状态")
                        sys_status = gr.Markdown(
                            f"- Ollama: {_ollama_status()}\n"
                            f"- ComfyUI: {_comfyui_status()}\n"
                            f"- 数据库: {os.path.expanduser('~/myworkspace/projects/story-agent-system/story_agents.db')}\n"
                            f"- 项目目录: {os.path.expanduser('~/myworkspace/projects/story-agent-system/output/')}"
                        )
                        refresh_sys = gr.Button("🔄 刷新系统状态")
                        refresh_sys.click(
                            fn=lambda: (
                                f"- Ollama: {_ollama_status()}\n"
                                f"- ComfyUI: {_comfyui_status()}\n"
                                f"- 数据库: ✅\n"
                                f"- 项目目录: ✅"
                            ),
                            outputs=sys_status
                        )

                        gr.Markdown("#### 运行日志")
                        log_view = gr.Dataframe(headers=["时间","类型","内容"], label="最近日志")
                        def refresh_logs():
                            logs = list_logs(limit=20)
                            return [[l.created_at[:19], l.agent_type, l.prompt[:80]] for l in logs]
                        refresh_logs_btn = gr.Button("📋 查看日志")
                        refresh_logs_btn.click(fn=refresh_logs, outputs=log_view)

        # ─── 项目切换事件 ────────────────────────
        def on_project_change(pid):
            CURRENT_PROJECT_ID["val"] = pid
            p = get_project(pid)
            info = _get_proj_info(pid) if p else "请选择项目"
            return info
        proj_dd.change(fn=on_project_change, inputs=[proj_dd], outputs=proj_info)
        proj_dd.change(fn=lambda pid: [_get_proj_info(pid)], inputs=[proj_dd], outputs=[proj_info])

        # 初始化项目列表
        proj_dd.choices = [(p.name, p.id) for p in list_projects()]
        proj_dd.value = default_pid

    return app


# ─── 启动入口 ──────────────────────────────────────────

if __name__ == "__main__":
    app = build_ui()
    app.launch(server_name="127.0.0.1", server_port=7860, share=False)
