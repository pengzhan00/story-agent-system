#!/usr/bin/env python3
"""
分镜工坊 (Shot Workshop) — 单镜头精细编辑 UI

独立 Gradio Blocks 应用，专注于单镜头的查看、编辑、渲染。
视觉规范:
  🖊️ 手动填写 — 橙色边框，用户必须自行填写
  🤖 AI 生成  — 蓝紫色，AI 产出内容（可编辑）
  ✅ 已完成   — 绿色 badge
  ⏳ 等待中   — 灰色 badge
"""
import sys
import os
import json
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import gradio as gr

from core.database import (
    list_projects, get_project, list_episodes, list_shots, get_shot,
    update_shot, list_render_jobs,
)
from core.asset_registry import get_shot_video, is_shot_rendered

# ─── 常量 ──────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"

PIPELINE_CHOICES = [
    "ltx_t2v",
    "stub",
]

TIME_OF_DAY_OPTIONS = ["白天", "清晨", "黄昏", "夜晚", "正午", "深夜"]
WEATHER_OPTIONS = ["晴", "阴", "多云", "雨", "小雨", "暴雨", "雪", "雾"]

STATUS_BADGE = {
    "draft":     "⏳ 草稿",
    "ready":     "🟡 待渲染",
    "rendering": "🔄 渲染中",
    "rendered":  "✅ 已渲染",
    "approved":  "✅ 已审核",
    "rejected":  "❌ 已拒绝",
}

# ─── CSS ──────────────────────────────────────────────────────────────

CUSTOM_CSS = """
:root {
  --bg: #0f1117; --card: #1a1d27; --border: #2d3147;
  --text: #e8eaed; --muted: #9aa0b0;
  --ai-color: #6366f1; --manual-color: #f59e0b; --ok-color: #22c55e;
}
.gradio-container { background: var(--bg) !important; color: var(--text); }
.gr-box, .gr-panel { background: var(--card) !important; border-color: var(--border) !important; border-radius: 12px !important; }
textarea, input[type=text], select {
  background: #1e2130 !important; color: var(--text) !important;
  border-color: var(--border) !important; border-radius: 8px !important;
}

/* AI 生成字段 — 蓝紫边框 */
.ai-field textarea, .ai-field input { border-color: var(--ai-color) !important; border-width: 1.5px !important; }
.ai-field label { color: var(--ai-color) !important; }

/* 手动填写字段 — 橙色边框 */
.manual-field textarea, .manual-field input { border-color: var(--manual-color) !important; border-width: 1.5px !important; }
.manual-field label { color: var(--manual-color) !important; }

/* Shot 列表项 */
.shot-list .gr-radio-row { padding: 6px 10px; border-radius: 6px; margin: 2px 0; }
.shot-list .gr-radio-row:hover { background: var(--border); }

/* 按钮 */
.btn-primary { background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
  border: none !important; color: white !important; font-weight: 700 !important;
  border-radius: 10px !important; }
.btn-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 16px rgba(99,102,241,.5); }
.btn-render { background: linear-gradient(135deg, #f59e0b, #d97706) !important;
  border: none !important; color: white !important; font-weight: 700 !important;
  border-radius: 10px !important; }
.btn-nav { background: var(--card) !important; border: 1px solid var(--border) !important;
  color: var(--text) !important; border-radius: 8px !important; }

/* Section headers */
.section-header { color: var(--muted); font-size: 13px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .06em; padding: 6px 0 4px; border-bottom: 1px solid var(--border); }

/* Status badge */
.status-ok { color: var(--ok-color); font-weight: 600; }
.status-wait { color: var(--muted); }
"""

# ─── 辅助函数 ──────────────────────────────────────────────────────────


def _shot_label(shot) -> str:
    """生成 Shot 列表显示文本: [#001] 场景名 | 状态"""
    badge = STATUS_BADGE.get(shot.status, shot.status)
    loc = shot.location or f"镜头{shot.shot_number}"
    return f"[#{shot.id:03d}] {loc}  |  {badge}"


def _load_payload(shot) -> dict:
    rp = shot.render_payload
    if isinstance(rp, str):
        try:
            return json.loads(rp) if rp else {}
        except Exception:
            return {}
    return rp or {}


def _project_choices() -> list[str]:
    try:
        projects = list_projects()
        return [f"{p.id}:{p.name}" for p in projects] if projects else []
    except Exception:
        return []


def _episode_choices(project_choice: str) -> list[str]:
    if not project_choice:
        return []
    try:
        pid = int(project_choice.split(":")[0])
        episodes = list_episodes(pid)
        return [f"{e.id}:{e.title or f'第{e.number}集'}" for e in episodes] if episodes else ["0:全部"]
    except Exception:
        return ["0:全部"]


def _shot_choices(project_choice: str, episode_choice: str) -> list[str]:
    if not project_choice:
        return []
    try:
        pid = int(project_choice.split(":")[0])
        eid = int(episode_choice.split(":")[0]) if episode_choice and episode_choice != "0:全部" else 0
        shots = list_shots(project_id=pid, episode_id=eid)
        return [_shot_label(s) for s in shots] if shots else []
    except Exception:
        return []


def _shot_id_from_label(label: str) -> int:
    """从 label "[#012] ..." 提取 shot id"""
    try:
        return int(label.split("]")[0].lstrip("[#"))
    except Exception:
        return 0


def _shot_counts(project_choice: str, episode_choice: str) -> str:
    if not project_choice:
        return ""
    try:
        pid = int(project_choice.split(":")[0])
        eid = int(episode_choice.split(":")[0]) if episode_choice and episode_choice != "0:全部" else 0
        shots = list_shots(project_id=pid, episode_id=eid)
        total = len(shots)
        by_status: dict[str, int] = {}
        for s in shots:
            by_status[s.status] = by_status.get(s.status, 0) + 1
        parts = [f"共 {total} 镜"]
        for st, cnt in by_status.items():
            parts.append(f"{STATUS_BADGE.get(st, st)} ×{cnt}")
        return "  |  ".join(parts)
    except Exception:
        return ""


def _load_shot_fields(shot_label: str):
    """根据选中的 shot label 返回所有 detail 字段值（按 outputs 顺序）"""
    EMPTY = ("", "", "", "", "", "白天", "晴", "", "", None, "AI占位", None, "", "ltx_t2v", "暂无渲染记录")
    if not shot_label:
        return EMPTY
    sid = _shot_id_from_label(shot_label)
    if not sid:
        return EMPTY
    try:
        shot = get_shot(sid)
        if not shot:
            return EMPTY
        payload = _load_payload(shot)
        pos_prompt = payload.get("positive_prompt") or shot.visual_prompt or ""
        neg_prompt = payload.get("negative_prompt") or ""
        ref_img = payload.get("reference_image_path") or ""
        ref_source = payload.get("ref_image_source", "AI占位")
        pipeline = payload.get("pipeline") or "ltx_t2v"

        # dialogue / narration
        try:
            dlg_list = json.loads(shot.dialogue) if isinstance(shot.dialogue, str) else (shot.dialogue or [])
            dialogue_text = "\n".join(
                f"{d.get('character','')}: {d.get('line','')}" if isinstance(d, dict) else str(d)
                for d in dlg_list
            ) if dlg_list else ""
        except Exception:
            dialogue_text = shot.dialogue or ""

        # video
        proj = get_project(shot.project_id)
        proj_name = proj.name if proj else ""
        vid_path = get_shot_video(shot.project_id, sid, proj_name) or None

        # render jobs summary
        jobs = list_render_jobs(project_id=shot.project_id, shot_id=sid)
        if jobs:
            lines = []
            for j in jobs[-5:]:
                st = getattr(j, "status", "?")
                pl = getattr(j, "used_pipeline", "") or getattr(j, "requested_pipeline", "")
                ts = getattr(j, "created_at", "")[:16]
                lines.append(f"{ts}  [{pl}]  {st}")
            jobs_text = "\n".join(reversed(lines))
        else:
            jobs_text = "暂无渲染记录"

        ref_img_display = ref_img if ref_img and Path(ref_img).exists() else None

        return (
            shot.narration or "",          # scene_desc
            dialogue_text,                  # dialogue
            pos_prompt,                     # prompt_pos
            neg_prompt,                     # prompt_neg
            shot.mood or "",                # mood
            shot.time_of_day or "白天",     # time_of_day
            shot.weather or "晴",           # weather
            ref_img or "",                  # ref_img_path (hidden)
            ref_source,                     # ref_source_label
            ref_img_display,                # ref_img_preview
            vid_path,                       # video_preview
            "",                             # video_upload_path (hidden)
            pipeline,                       # pipeline
            jobs_text,                      # render_jobs_text
        )
    except Exception as e:
        return EMPTY[:-1] + (f"加载出错: {e}",)


def _save_shot(
    shot_label, scene_desc, dialogue, prompt_pos, prompt_neg,
    mood, time_of_day, weather, ref_img_path, pipeline
):
    if not shot_label:
        return "⚠️ 未选中镜头"
    sid = _shot_id_from_label(shot_label)
    if not sid:
        return "⚠️ 镜头 ID 无效"
    try:
        shot = get_shot(sid)
        if not shot:
            return f"⚠️ 未找到镜头 #{sid}"
        payload = _load_payload(shot)
        payload["positive_prompt"] = prompt_pos
        payload["negative_prompt"] = prompt_neg
        payload["pipeline"] = pipeline
        if ref_img_path:
            payload["reference_image_path"] = ref_img_path

        # dialogue: 保存为 JSON list
        try:
            lines = [l.strip() for l in dialogue.splitlines() if l.strip()]
            dlg_list = []
            for l in lines:
                if ": " in l:
                    ch, txt = l.split(": ", 1)
                    dlg_list.append({"character": ch.strip(), "line": txt.strip()})
                else:
                    dlg_list.append({"character": "", "line": l})
            dlg_json = json.dumps(dlg_list, ensure_ascii=False)
        except Exception:
            dlg_json = shot.dialogue

        update_shot(sid, {
            "narration": scene_desc,
            "dialogue": dlg_json,
            "visual_prompt": prompt_pos,
            "mood": mood,
            "time_of_day": time_of_day,
            "weather": weather,
            "render_payload": json.dumps(payload, ensure_ascii=False),
        })
        return f"✅ 镜头 #{sid} 已保存"
    except Exception as e:
        return f"❌ 保存失败: {e}"


def _upload_reference(shot_label, file_obj):
    """上传参考图，保存到 output/projects/{name}/references/shot_{id}_custom.png"""
    if not shot_label or file_obj is None:
        return gr.update(), "AI占位", None, "⚠️ 未选中镜头或文件为空"
    sid = _shot_id_from_label(shot_label)
    if not sid:
        return gr.update(), "AI占位", None, "⚠️ 无效 ID"
    try:
        shot = get_shot(sid)
        proj = get_project(shot.project_id) if shot else None
        proj_name = proj.name if proj else "unknown"
        dest_dir = OUTPUT_DIR / "projects" / proj_name / "references"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"shot_{sid:03d}_custom.png"
        src = file_obj.name if hasattr(file_obj, "name") else str(file_obj)
        shutil.copy2(src, str(dest))
        # 更新 payload
        payload = _load_payload(shot)
        payload["reference_image_path"] = str(dest)
        payload["ref_image_source"] = "自定义上传"
        update_shot(sid, {"render_payload": json.dumps(payload, ensure_ascii=False)})
        return str(dest), "自定义上传", str(dest), f"✅ 参考图已保存: {dest.name}"
    except Exception as e:
        return gr.update(), gr.update(), None, f"❌ 上传失败: {e}"


def _upload_video(shot_label, file_obj):
    """上传视频，直接作为成片，标记 status=rendered"""
    if not shot_label or file_obj is None:
        return None, "⚠️ 未选中镜头或文件为空"
    sid = _shot_id_from_label(shot_label)
    if not sid:
        return None, "⚠️ 无效 ID"
    try:
        shot = get_shot(sid)
        proj = get_project(shot.project_id) if shot else None
        proj_name = proj.name if proj else "unknown"
        dest_dir = OUTPUT_DIR / "projects" / proj_name / "scenes"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"shot_{sid:03d}_custom.mp4"
        src = file_obj.name if hasattr(file_obj, "name") else str(file_obj)
        shutil.copy2(src, str(dest))
        update_shot(sid, {"status": "rendered"})
        return str(dest), f"✅ 视频已上传，镜头状态更新为 rendered: {dest.name}"
    except Exception as e:
        return None, f"❌ 上传失败: {e}"


def _render_shot(shot_label, pipeline_name, progress=gr.Progress()):
    """在独立线程中渲染单个镜头（阻塞，通过 yield 汇报进度）"""
    if not shot_label:
        yield "⚠️ 请先选中一个镜头"
        return
    sid = _shot_id_from_label(shot_label)
    if not sid:
        yield "⚠️ 无法解析镜头 ID"
        return
    yield f"⏳ 准备渲染镜头 #{sid}，管线: {pipeline_name} ..."
    try:
        os.environ["STORY_AGENT_ALLOW_UNREADY_PIPELINES"] = "1"
        from pipelines.render_pipeline import reset_dispatcher, set_active_pipeline_name
        from pipelines.batch_renderer import BatchRenderer

        shot = get_shot(sid)
        if not shot:
            yield f"❌ 未找到镜头 #{sid}"
            return
        proj = get_project(shot.project_id)
        if not proj:
            yield "❌ 未找到所属项目"
            return

        yield f"⚙️ 切换管线 → {pipeline_name} ..."
        set_active_pipeline_name(pipeline_name)

        renderer = BatchRenderer(proj.name, proj.id)
        msgs = []

        def on_progress(msg, pct):
            msgs.append(msg)

        renderer.set_progress_callback(on_progress)

        payload = _load_payload(shot)
        payload["project_name"] = proj.name
        payload["shot_id"] = sid

        yield f"🎬 提交渲染任务 (shot #{sid}) ..."
        result = renderer.render_scene_with_retry(payload, scene_id=f"shot_{sid:03d}")
        log = "\n".join(msgs) if msgs else ""
        if result:
            yield f"✅ 渲染成功!\n输出: {result}\n\n{log}"
        else:
            yield f"❌ 渲染失败（已重试 2 次）\n\n{log}"
    except Exception as e:
        yield f"❌ 渲染出错: {e}"


def _nav_shot(shot_label, choices, direction: int) -> str:
    """返回列表中前/后一个镜头的 label"""
    if not choices or not shot_label:
        return shot_label or ""
    try:
        idx = choices.index(shot_label)
    except ValueError:
        return shot_label
    new_idx = (idx + direction) % len(choices)
    return choices[new_idx]


# ─── UI 构建 ──────────────────────────────────────────────────────────


def build_workshop_tab(parent_blocks=None):
    """
    构建分镜工坊的 Tab 内容。
    可直接嵌入已有的 gr.Blocks 上下文（在 gr.TabItem 内部调用），
    也可通过 build_workshop_demo() 作为独立 Blocks 运行。

    parent_blocks: 父级 gr.Blocks 引用，用于注册 .load() 事件。
                   若为 None，则尝试使用当前上下文的 Blocks。
    """
    # ── 标题 ────────────────────────────────────────────────────
    gr.Markdown("## 🎬 分镜工坊  Shot Workshop")
    gr.Markdown("精细编辑每一个镜头的 AI 内容、参考图、渲染设置，并单独触发渲染。")

    # ── 顶部控制区 ───────────────────────────────────────────────
    with gr.Row():
        proj_dd = gr.Dropdown(
            label="📁 项目",
            choices=_project_choices(),
            scale=2,
            info="从数据库中选择项目",
        )
        ep_dd = gr.Dropdown(
            label="📺 集数",
            choices=[],
            scale=2,
            info="选择集数（留空=全部）",
        )
        refresh_btn = gr.Button("🔄 刷新列表", scale=1, elem_classes="btn-nav")

    # ── 主体三栏 ──────────────────────────────────────────────────
    with gr.Row(equal_height=False):

        # ── 左列：镜头列表 ──────────────────────────────────────
        with gr.Column(scale=1, min_width=260):
            gr.Markdown("### 🎞️ 镜头列表")
            shot_radio = gr.Radio(
                label="选择镜头",
                choices=[],
                elem_classes="shot-list",
                info="点击镜头以在右侧加载详情",
            )
            counts_md = gr.Markdown("", elem_classes="status-wait")

        # ── 中列：镜头详情 ───────────────────────────────────────
        with gr.Column(scale=2, min_width=480):
            gr.Markdown("### 🖊️ 镜头详情")

            # 隐藏状态
            selected_shot_label = gr.State("")

            # ── AI 生成内容 ──────────────────────────────────────
            with gr.Accordion("🤖 AI 生成内容（可编辑）", open=True):
                scene_desc = gr.Textbox(
                    label="🤖 场景描述 / 旁白",
                    lines=3,
                    info="AI 生成的场景叙述，可自由修改",
                    elem_classes="ai-field",
                )
                dialogue = gr.Textbox(
                    label="🤖 台词 / 对白",
                    lines=2,
                    placeholder="角色名: 台词内容（每行一条）",
                    info="格式: 角色: 台词。多行输入，每行一条",
                    elem_classes="ai-field",
                )
                prompt_pos = gr.Textbox(
                    label="🤖 渲染 Prompt（正向）",
                    lines=3,
                    info="发送给 ComfyUI 的正向提示词，影响画面内容",
                    elem_classes="ai-field",
                )
                prompt_neg = gr.Textbox(
                    label="🤖 渲染 Prompt（负向）",
                    lines=2,
                    info="要排除的元素（模糊、水印、低质量等）",
                    elem_classes="ai-field",
                )
                with gr.Row():
                    mood = gr.Textbox(
                        label="🤖 情绪基调",
                        scale=2,
                        info="如：紧张、温馨、压抑",
                        elem_classes="ai-field",
                    )
                    time_of_day = gr.Dropdown(
                        label="🤖 时间",
                        choices=TIME_OF_DAY_OPTIONS,
                        value="白天",
                        scale=1,
                        elem_classes="ai-field",
                    )
                    weather = gr.Dropdown(
                        label="🤖 天气",
                        choices=WEATHER_OPTIONS,
                        value="晴",
                        scale=1,
                        elem_classes="ai-field",
                    )

            # ── 参考图 ───────────────────────────────────────────
            with gr.Accordion("🖼️ 参考图（决定视频风格）", open=True):
                ref_img_path = gr.State("")   # 隐藏路径
                ref_img_preview = gr.Image(
                    label="当前参考图",
                    type="filepath",
                    height=200,
                    interactive=False,
                )
                with gr.Row():
                    ref_upload_btn = gr.UploadButton(
                        "📂 上传我的图片",
                        file_types=["image"],
                        scale=1,
                    )
                    ai_placeholder_btn = gr.Button(
                        "🤖 使用 AI 生成占位图",
                        scale=1,
                        elem_classes="btn-nav",
                    )
                ref_source_label = gr.Textbox(
                    label="图片来源",
                    value="AI占位",
                    interactive=False,
                    info="AI占位 = 由管线自动生成；自定义上传 = 用户上传",
                )
                ref_upload_status = gr.Markdown("")

            # ── 视频素材 ─────────────────────────────────────────
            with gr.Accordion("📹 视频素材（可跳过渲染直接上传）", open=False):
                video_preview = gr.Video(
                    label="当前视频",
                    height=200,
                    interactive=False,
                )
                vid_upload_btn = gr.UploadButton(
                    "📂 上传已有视频（跳过渲染）",
                    file_types=["video"],
                )
                gr.Markdown(
                    "<small style='color:var(--muted)'>上传后直接标记为成片，不再调用 ComfyUI 渲染</small>"
                )
                vid_upload_status = gr.Markdown("")

            # ── 渲染设置 ─────────────────────────────────────────
            with gr.Accordion("⚙️ 渲染设置", open=True):
                pipeline_dd = gr.Dropdown(
                    label="🖊️ 渲染管线",
                    choices=PIPELINE_CHOICES,
                    value="stub",
                    info="选择要使用的 ComfyUI 工作流管线",
                    elem_classes="manual-field",
                )
                gr.Markdown(
                    "<small style='color:var(--muted)'>分辨率: 480×832 (9:16)  由管线配置决定</small>"
                )

            # ── 操作按钮 ─────────────────────────────────────────
            gr.Markdown("---")
            with gr.Row():
                save_btn = gr.Button("💾 保存修改", variant="primary", scale=2, elem_classes="btn-primary")
                render_btn = gr.Button("🎬 渲染此镜头", scale=2, elem_classes="btn-render")
            with gr.Row():
                prev_btn = gr.Button("⏮️ 上一镜头", scale=1, elem_classes="btn-nav")
                next_btn = gr.Button("⏭️ 下一镜头", scale=1, elem_classes="btn-nav")

            save_status = gr.Markdown("")

        # ── 右列：状态与日志 ──────────────────────────────────────
        with gr.Column(scale=1, min_width=260):
            gr.Markdown("### 📊 渲染状态 & 日志")
            render_status = gr.Textbox(
                label="渲染输出",
                lines=14,
                interactive=False,
                info="渲染进度实时输出",
            )
            render_jobs_text = gr.Textbox(
                label="最近渲染记录",
                lines=8,
                interactive=False,
                info="该镜头最近 5 条渲染任务",
            )

    # Hidden state for video upload path (not shown in UI)
    _vid_upload_hidden = gr.State("")

    # ─── 事件绑定 ──────────────────────────────────────────────

    def refresh_all(proj_choice):
        ep_choices = _episode_choices(proj_choice)
        ep_val = ep_choices[0] if ep_choices else None
        shot_choices = _shot_choices(proj_choice, ep_val or "")
        counts = _shot_counts(proj_choice, ep_val or "")
        return (
            gr.update(choices=ep_choices, value=ep_val),
            gr.update(choices=shot_choices, value=None),
            counts,
        )

    proj_dd.change(
        refresh_all,
        inputs=[proj_dd],
        outputs=[ep_dd, shot_radio, counts_md],
    )

    def on_ep_change(proj_choice, ep_choice):
        shot_choices = _shot_choices(proj_choice, ep_choice)
        counts = _shot_counts(proj_choice, ep_choice)
        return gr.update(choices=shot_choices, value=None), counts

    ep_dd.change(
        on_ep_change,
        inputs=[proj_dd, ep_dd],
        outputs=[shot_radio, counts_md],
    )

    def on_refresh(proj_choice, ep_choice):
        shot_choices = _shot_choices(proj_choice, ep_choice)
        counts = _shot_counts(proj_choice, ep_choice)
        return gr.update(choices=shot_choices, value=None), counts

    refresh_btn.click(
        on_refresh,
        inputs=[proj_dd, ep_dd],
        outputs=[shot_radio, counts_md],
    )

    # Shot selected — load all fields
    _shot_detail_outputs = [
        scene_desc, dialogue, prompt_pos, prompt_neg,
        mood, time_of_day, weather,
        ref_img_path, ref_source_label, ref_img_preview,
        video_preview,
        _vid_upload_hidden,
        pipeline_dd,
        render_jobs_text,
    ]
    shot_radio.change(
        fn=_load_shot_fields,
        inputs=[shot_radio],
        outputs=_shot_detail_outputs,
    )

    # Save
    save_btn.click(
        fn=_save_shot,
        inputs=[
            shot_radio, scene_desc, dialogue, prompt_pos, prompt_neg,
            mood, time_of_day, weather, ref_img_path, pipeline_dd,
        ],
        outputs=[save_status],
    )

    # Upload reference image
    ref_upload_btn.upload(
        fn=_upload_reference,
        inputs=[shot_radio, ref_upload_btn],
        outputs=[ref_img_path, ref_source_label, ref_img_preview, ref_upload_status],
    )

    # AI placeholder button (stub — just clears custom path)
    def clear_custom_ref(shot_label):
        if not shot_label:
            return "", "AI占位", None, ""
        sid = _shot_id_from_label(shot_label)
        try:
            shot = get_shot(sid)
            payload = _load_payload(shot)
            payload.pop("reference_image_path", None)
            payload["ref_image_source"] = "AI占位"
            update_shot(sid, {"render_payload": json.dumps(payload, ensure_ascii=False)})
        except Exception:
            pass
        return "", "AI占位", None, "ℹ️ 已切换为 AI 生成占位图（渲染时自动生成）"

    ai_placeholder_btn.click(
        fn=clear_custom_ref,
        inputs=[shot_radio],
        outputs=[ref_img_path, ref_source_label, ref_img_preview, ref_upload_status],
    )

    # Upload video
    vid_upload_btn.upload(
        fn=_upload_video,
        inputs=[shot_radio, vid_upload_btn],
        outputs=[video_preview, vid_upload_status],
    )

    # Render (streaming)
    render_btn.click(
        fn=_render_shot,
        inputs=[shot_radio, pipeline_dd],
        outputs=[render_status],
    )

    # Prev / Next navigation
    def go_prev(shot_label, proj_choice, ep_choice):
        choices = _shot_choices(proj_choice, ep_choice)
        return _nav_shot(shot_label, choices, -1)

    def go_next(shot_label, proj_choice, ep_choice):
        choices = _shot_choices(proj_choice, ep_choice)
        return _nav_shot(shot_label, choices, +1)

    prev_btn.click(
        fn=go_prev,
        inputs=[shot_radio, proj_dd, ep_dd],
        outputs=[shot_radio],
    )
    next_btn.click(
        fn=go_next,
        inputs=[shot_radio, proj_dd, ep_dd],
        outputs=[shot_radio],
    )

    # Init: populate project dropdown on load
    if parent_blocks is not None:
        parent_blocks.load(
            fn=lambda: gr.update(choices=_project_choices()),
            outputs=[proj_dd],
        )


def build_workshop_demo() -> gr.Blocks:
    """独立运行模式：将 build_workshop_tab() 包入 gr.Blocks。"""
    with gr.Blocks(title="分镜工坊") as demo:
        build_workshop_tab(demo)
    return demo


# ─── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    build_workshop_demo().launch(
        server_port=7861,
        css=CUSTOM_CSS,
        theme=gr.themes.Base(),
    )
