"""
Render Service — 独立渲染服务 (端口 7861)
从 DB 任务队列获取渲染任务，提交到 ComfyUI 执行。
ComfyUI 不可用时优雅降级，仅显示任务列表。
"""
import gradio as gr
import json
import json as _json
import os
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.database import (
    init_db, list_tasks, get_task, claim_next_task, complete_task,
    add_agent_log, list_agent_logs, get_project,
    cancel_running_tasks, cancel_running_render_jobs,
)
def get_pipeline_config() -> dict:
    _cfg_path = Path(__file__).parent.parent / "pipelines" / "pipeline_config.json"
    return _json.loads(_cfg_path.read_text())

def get_active_pipeline() -> str:
    return get_pipeline_config().get("active_pipeline", "ltx_t2v")

def list_pipelines_with_capabilities() -> list:
    cfg = get_pipeline_config()
    from pipelines.render_pipeline import RenderDispatcher
    matrix = RenderDispatcher.from_config().probe(force=True)
    rows = []
    for p in cfg.get("pipelines", []):
        status = matrix.get(p["name"])
        rows.append({
            "id": p["name"],
            "name": p["name"],
            "description": p.get("description", ""),
            "production_ready": p.get("production_ready", False),
            "available": bool(status and status.available),
            "missing": status.missing if status else [],
        })
    return rows

def set_active_pipeline(pipeline_id: str) -> dict:
    _cfg_path = Path(__file__).parent.parent / "pipelines" / "pipeline_config.json"
    cfg = _json.loads(_cfg_path.read_text())
    cfg["active_pipeline"] = pipeline_id
    _cfg_path.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False))
    return {"active": pipeline_id}

def inspect_pipeline_capability(pipeline_id: str) -> dict:
    cfg = get_pipeline_config()
    pipeline = next((p for p in cfg.get("pipelines", []) if p["name"] == pipeline_id), None)
    from pipelines.render_pipeline import RenderDispatcher
    status = RenderDispatcher.from_config().probe(force=True).get(pipeline_id)
    return {
        "id": pipeline_id,
        "available": bool(status and status.available),
        "missing": status.missing if status else [],
        "production_ready": (pipeline or {}).get("production_ready", False),
    }
from pipelines.batch_renderer import BatchRenderer

APP_TITLE = "🎬 漫剧渲染服务 — Render Service"
THEME_CSS = """
footer {display:none !important}
.gradio-container {max-width: 1200px !important}
.status-ok {color: #22c55e; font-weight: bold}
.status-warn {color: #eab308; font-weight: bold}
.status-err {color: #ef4444; font-weight: bold}
"""

def _comfyui_url() -> str:
    _env = os.getenv("COMFYUI_URL", "").strip()
    if _env:
        return _env
    from core.service_ports import get_comfyui_url
    return get_comfyui_url(auto_launch=False)


# ─── ComfyUI 状态检查 ─────────────────────────

def _comfyui_status() -> str:
    import requests as req
    try:
        r = req.get(f"{_comfyui_url()}/object_info", timeout=3)
        if r.status_code == 200:
            return "✅ 在线"
        return "⚠️ 异常"
    except Exception:
        return "❌ 离线"

def _refresh_comfyui_info() -> str:
    """返回 ComfyUI 状态的详细 Markdown"""
    import requests as req
    try:
        url = _comfyui_url()
        r = req.get(f"{url}/object_info", timeout=3)
        if r.status_code == 200:
            info = r.json()
            node_count = len(info)
            return (
                f"✅ **ComfyUI 在线**\n"
                f"- 地址: {url}\n"
                f"- 节点数: {node_count}\n"
            )
        return "⚠️ ComfyUI 响应异常"
    except Exception as e:
        return f"❌ **ComfyUI 离线**\n- 地址: {_comfyui_url()}\n- 错误: {e}\n\n请先在终端启动 ComfyUI:\n```\ncd ~/Documents/ComfyUI\nsource .venv/bin/activate\npython main.py --listen 127.0.0.1 --port 8188\n```"

# ─── 任务操作 ─────────────────────────────────

def _list_all_tasks(limit: int = 50) -> list:
    """返回所有渲染任务的表格数据"""
    tasks = list_tasks(agent_type="render_scheduler", limit=limit)
    rows = []
    for t in tasks:
        rows.append([
            t.get("id", ""),
            t.get("project_id", ""),
            t.get("action", ""),
            t.get("status", ""),
            t.get("priority", ""),
            str(t.get("created_at", ""))[:19],
            str(t.get("completed_at", ""))[:19] if t.get("completed_at") else "",
            (t.get("error", "") or "")[:60],
        ])
    return rows

def _claim_and_run_next_task() -> str:
    """Claim 下一个渲染任务并尝试执行"""
    task = claim_next_task("render_scheduler")
    if not task:
        return "⚠️ 没有待处理的渲染任务"
    
    task_id = task["id"]
    add_agent_log(task_id, "render_scheduler", "started", "info", "开始处理渲染任务")
    
    try:
        input_params = json.loads(task.get("input_params", "{}"))
        project_id = int(task.get("project_id") or input_params.get("project_id") or 0)
        project_name = input_params.get("project_name", "未知项目")
        scenes = input_params.get("scenes", [])

        report = inspect_pipeline_capability(get_pipeline_config().get("active", "C"))
        if not report["ready"]:
            reason = "; ".join(report["errors"][:3]) or report["status_text"]
            add_agent_log(task_id, "render_scheduler", "blocked", "error", f"管线未就绪: {reason}")
            complete_task(task_id, {"error": reason}, error=f"渲染管线未就绪: {reason}")
            return f"❌ 任务 #{task_id} 阻塞: 渲染管线未就绪\n{reason}"
        
        # 检查 ComfyUI 是否在线
        import requests as req
        try:
            r = req.get(f"{_comfyui_url()}/object_info", timeout=3)
            if r.status_code != 200:
                raise Exception(f"ComfyUI 返回错误: {r.status_code}")
        except Exception as e:
            add_agent_log(task_id, "render_scheduler", "error", "error", f"ComfyUI 不可达: {e}")
            complete_task(task_id, {"error": str(e)}, error=f"ComfyUI 离线: {e}")
            return f"❌ 任务 #{task_id} 失败: ComfyUI 离线\n请启动 ComfyUI 后重试"
        
        # ComfyUI 在线，开始渲染
        add_agent_log(task_id, "render_scheduler", "queuing", "info", 
                      f"正在向 ComfyUI 提交 {len(scenes)} 个场景")

        renderer = BatchRenderer(project_name, project_id=project_id)
        rendered_files = renderer.render_multi_scene(scenes, max_workers=1, max_retries=1)
        rendered_count = len(rendered_files)
        if rendered_count != len(scenes):
            msg = f"只完成 {rendered_count}/{len(scenes)} 个场景"
            add_agent_log(task_id, "render_scheduler", "partial_failure", "error", msg)
            complete_task(task_id, {
                "project": project_name,
                "scenes_count": len(scenes),
                "rendered_count": rendered_count,
                "videos": rendered_files,
            }, error=msg)
            return f"❌ 任务 #{task_id} 失败: {msg}"

        result = {
            "status": "completed",
            "project": project_name,
            "scenes_count": len(scenes),
            "rendered_count": rendered_count,
            "videos": rendered_files,
            "message": f"已完成 {rendered_count} 个场景渲染",
        }
        
        complete_task(task_id, result)
        add_agent_log(task_id, "render_scheduler", "completed", "info", f"渲染完成 {rendered_count} 个场景")
        
        return f"✅ 任务 #{task_id}: 已完成 {rendered_count} 个场景渲染"
        
    except Exception as e:
        add_agent_log(task_id, "render_scheduler", "error", "error", str(e))
        complete_task(task_id, {"error": str(e)}, error=str(e))
        return f"❌ 任务 #{task_id} 失败: {e}"

# ─── 管线切换 ──────────────────────────────────

def _render_pipeline_info() -> str:
    """返回当前激活管线详情"""
    try:
        cfg = get_pipeline_config()
        active = cfg.get("active_pipeline", "—")
        pipes = {p.get("name"): p for p in cfg.get("pipelines", [])}
        active_pipe = (pipes.get(active) or {}).get("config", {})
        active_meta = pipes.get(active) or {}
        active_report = inspect_pipeline_capability(active)
        lines = [
            f"**当前管线**: {active} · {active_meta.get('description', '?')}",
            f"**状态**: {'✅ 可用' if active_report.get('available') else '⚠️ 未就绪'}",
            f"**生产级别**: {'production' if active_meta.get('production_ready') else 'experimental'}",
            f"**模型**: {active_pipe.get('checkpoint_name') or active_pipe.get('model_name') or active_pipe.get('gguf_path') or '?'}",
            f"**Workflow**: {active_pipe.get('workflow_file', '?')}",
            f"**分辨率**: {active_pipe.get('width', '?')} × {active_pipe.get('height', '?')}",
            f"**帧率**: {active_pipe.get('fps', '?')}fps",
            "",
            "**可用管线**:",
        ]
        if active_report.get("missing"):
            lines.append("")
            lines.append("**阻塞项**:")
            for item in active_report["missing"][:5]:
                lines.append(f"- {item}")
        for pipe in list_pipelines_with_capabilities():
            pid = pipe["id"]
            marker = "👉 " if pid == active else "   "
            lines.append(
                f"{marker}{'✅' if pipe.get('available', True) else '⚠️'} **{pid}**: {pipe.get('description', '')}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ 无法读取管线配置: {e}"


def _pipeline_choices() -> list[str]:
    cfg = get_pipeline_config()
    active = cfg.get("active_pipeline", "")
    choices = []
    for p in sorted(cfg.get("pipelines", []), key=lambda x: x.get("priority", 99)):
        name = p["name"]
        desc = p.get("description", "")
        steps = p.get("config", {}).get("steps", "?")
        prod = p.get("production_ready", False)
        # 层级标签
        if "【快·短剧】" in desc:
            tier = "⚡短剧"
        elif "【质·电影】" in desc:
            tier = "🎬电影"
        elif "【兜底】" in desc:
            tier = "🔧兜底"
        else:
            tier = "？"
        # 可用状态图标
        icon = "🟢" if prod else "🟡"
        core_desc = desc.replace("【快·短剧】", "").replace("【质·电影】", "").replace("【兜底】", "").strip()
        label = f"{icon} [{tier} {steps}步] {name} · {core_desc}"
        choices.append(label)
    return choices


def _current_pipeline_choice() -> Optional[str]:
    cfg = get_pipeline_config()
    active = cfg.get("active_pipeline", "")
    choices = _pipeline_choices()
    # 找到 active 对应的 label
    for label in choices:
        if f"] {active} " in label or label.endswith(f" {active}"):
            return label
    return choices[0] if choices else None


def _refresh_pipeline_ui() -> tuple[str, gr.update, str]:
    return (
        _download_status(),
        gr.update(choices=_pipeline_choices(), value=_current_pipeline_choice()),
        _render_pipeline_info(),
    )

def _switch_pipeline(pipeline_id: str) -> tuple[str, str]:
    """切换管线"""
    try:
        set_active_pipeline(pipeline_id)
        cfg = get_pipeline_config()
        entry = next((p for p in cfg.get("pipelines", []) if p["name"] == pipeline_id), None)
        desc = entry.get("description", pipeline_id) if entry else pipeline_id
        steps = entry.get("config", {}).get("steps", "?") if entry else "?"
        return (
            _render_pipeline_info(),
            f"✅ 已切换到 **{pipeline_id}** ({steps}步) — {desc}"
        )
    except Exception as e:
        info = _render_pipeline_info()
        return (info, f"❌ 切换失败: {e}")


def _switch_pipeline_from_choice(choice: str) -> tuple[str, str]:
    # 新格式: "🟢 [⚡短剧 10步] wan2_t2v · ..."  → 提取 ] 和 · 之间的管线名
    import re
    pipeline_id = ""
    if choice:
        m = re.search(r'\]\s*(\S+)\s*·', choice)
        if m:
            pipeline_id = m.group(1)
        else:
            pipeline_id = choice.split("·", 1)[0].strip()
    return _switch_pipeline(pipeline_id)

def _download_status() -> str:
    """检查模型下载进度"""
    status = ["### 模型与管线状态\n"]
    cfg = get_pipeline_config()
    for pid, pipe in cfg.get("options", {}).items():
        report = inspect_pipeline_capability(pid)
        state_key = report.get("state_key", "unknown")
        icon = {
            "ready": "✅",
            "validation_pending": "🧪",
            "missing_models": "📦",
            "missing_nodes": "🧩",
            "workflow_refinement_required": "🛠️",
            "blocked": "⚠️",
            "unknown": "❓",
        }.get(state_key, "⚠️")
        status.append(f"{icon} **{pid} · {pipe.get('name', pid)}**: {report.get('status_text', '未知')}")
        for msg in report.get("errors", [])[:4]:
            status.append(f"- {msg}")
        if report.get("last_error"):
            status.append(f"- 最近错误: {report['last_error'][:180]}")
    gguf_dir = os.path.expanduser("~/Documents/ComfyUI/custom_nodes/ComfyUI-GGUF")
    status.append("")
    status.append(f"{'✅' if os.path.isdir(gguf_dir) else '⬜'} **ComfyUI-GGUF**")
    return "\n".join(status)


# ─── UI 构建 ──────────────────────────────────

def build_ui():
    init_db()
    
    with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft(), css=THEME_CSS) as app:
        gr.Markdown(f"# {APP_TITLE}")
        
        # ── 状态栏 ──────────────────────────
        with gr.Row():
            comfy_status = gr.Markdown(f"**ComfyUI**: {_comfyui_status()} | **地址**: {_comfyui_url()}")
            stop_all_btn = gr.Button("🛑 停止所有渲染", variant="stop", size="sm")
            refresh_status_btn = gr.Button("🔄 刷新状态", variant="secondary", size="sm")

        def _update_status():
            return f"**ComfyUI**: {_comfyui_status()} | **地址**: {_comfyui_url()}"
        refresh_status_btn.click(fn=_update_status, outputs=comfy_status)
        
        def _stop_all_renders():
            import requests as req
            msgs = []
            # 1. 中断 ComfyUI 当前任务
            try:
                r = req.post(f"{_comfyui_url()}/interrupt", timeout=5)
                if r.status_code == 200:
                    msgs.append("✅ ComfyUI 已中断")
                else:
                    msgs.append(f"⚠️ ComfyUI 中断返回: {r.status_code}")
            except Exception as e:
                msgs.append(f"⚠️ ComfyUI 不可达: {e}")
            # 2. 清空 ComfyUI 队列
            try:
                r = req.post(f"{_comfyui_url()}/queue", json={"clear": True}, timeout=5)
                msgs.append("✅ ComfyUI 队列已清空")
            except Exception as e:
                msgs.append(f"⚠️ 清空队列: {e}")
            # 3. 取消 DB 任务队列
            n = cancel_running_tasks("render_scheduler")
            msgs.append(f"✅ 已取消 {n} 个任务队列")
            # 4. 取消 render_jobs
            n2 = cancel_running_render_jobs()
            msgs.append(f"✅ 已取消 {n2} 个渲染作业")
            return "\n".join(msgs)
        stop_all_btn.click(fn=_stop_all_renders, outputs=comfy_status)
        
        # ── Tab 容器 ────────────────────────
        with gr.Tabs() as tabs:
            
            # ══════ Tab 1: 任务队列 ══════════
            with gr.TabItem("📋 任务队列", id="tasks"):
                with gr.Row():
                    with gr.Column(scale=3):
                        gr.Markdown("### 渲染任务列表")
                        task_table = gr.Dataframe(
                            headers=["ID", "项目ID", "动作", "状态", "优先级", "创建时间", "完成时间", "错误"],
                            label="渲染任务",
                            interactive=False,
                        )
                    with gr.Column(scale=1):
                        gr.Markdown("### 操作")
                        refresh_tasks_btn = gr.Button("🔄 刷新任务列表", variant="primary")
                        run_next_btn = gr.Button("▶️ 执行下一个任务", variant="primary")
                        task_log = gr.Markdown("等待操作...")
                
                def _refresh_tasks():
                    return _list_all_tasks()
                refresh_tasks_btn.click(fn=_refresh_tasks, outputs=task_table)
                app.load(fn=_refresh_tasks, outputs=task_table)
                
                run_next_btn.click(fn=_claim_and_run_next_task, outputs=task_log)
                run_next_btn.click(fn=_refresh_tasks, outputs=task_table)
            
            # ══════ Tab 2: ComfyUI 信息 ══════
            with gr.TabItem("🔧 ComfyUI 信息", id="comfyui"):
                gr.Markdown("### ComfyUI 状态")
                comfy_detail = gr.Markdown(_refresh_comfyui_info())
                refresh_detail_btn = gr.Button("🔄 刷新详情")
                refresh_detail_btn.click(fn=_refresh_comfyui_info, outputs=comfy_detail)
                
                gr.Markdown("---")
                gr.Markdown("### 操作说明")
                gr.Markdown("""
1. **启动 ComfyUI**（如果未运行）:
   ```bash
   cd ~/Documents/ComfyUI
   source .venv/bin/activate
   nohup .venv/bin/python3 main.py --listen 127.0.0.1 --port 8188 > /tmp/comfyui.log 2>&1 &
   ```
2. **使用「任务队列」Tab** 查看和管理渲染任务
3. 点击「执行下一个任务」自动提交到 ComfyUI
4. ComfyUI 的 Web 界面在 http://127.0.0.1:8000
""")
            
            # ══════ Tab 4: 管线配置 ════════════════════
            with gr.TabItem("🔀 渲染管线", id="pipeline"):
                gr.Markdown("### 渲染管线选择")
                pipeline_info = gr.Markdown(_render_pipeline_info())
                with gr.Row():
                    pipeline_choice = gr.Dropdown(
                        choices=_pipeline_choices(),
                        value=_current_pipeline_choice(),
                        label="激活管线",
                        scale=3,
                    )
                    pipeline_apply_btn = gr.Button("✅ 切换到所选管线", variant="primary", size="lg", scale=1)
                pipeline_msg = gr.Markdown("")

                pipeline_apply_btn.click(
                    fn=_switch_pipeline_from_choice,
                    inputs=[pipeline_choice],
                    outputs=[pipeline_info, pipeline_msg],
                )
                
                gr.Markdown("---")
                gr.Markdown("### 各管线所需模型下载进度")
                dl_status = gr.Markdown(_download_status())
                refresh_dl_btn = gr.Button("🔄 刷新下载进度")
                refresh_dl_btn.click(fn=_refresh_pipeline_ui, outputs=[dl_status, pipeline_choice, pipeline_info])
            
            # ══════ Tab 3: 日志 ═════════════
            with gr.TabItem("📝 日志", id="logs"):
                gr.Markdown("### 渲染服务日志")
                log_table = gr.Dataframe(
                    headers=["ID", "时间", "Agent", "动作", "级别", "消息"],
                    label="Agent 日志",
                    interactive=False,
                )
                refresh_log_btn = gr.Button("🔄 刷新日志")
                
                def _refresh_logs():
                    logs = list_agent_logs(limit=50)
                    return [[l.get("id",""), str(l.get("created_at",""))[:19], 
                             l.get("agent_type",""), l.get("action",""),
                             l.get("level",""), l.get("message","")[:100]] for l in logs]
                
                refresh_log_btn.click(fn=_refresh_logs, outputs=log_table)
                app.load(fn=_refresh_logs, outputs=log_table)
    
    return app


def build_render_ui():
    """Backward-compatible entrypoint used by main.py."""
    return build_ui()


# ─── 启动入口 ──────────────────────────────────

if __name__ == "__main__":
    app = build_ui()
    app.launch(server_name="127.0.0.1", server_port=7861, share=False)
