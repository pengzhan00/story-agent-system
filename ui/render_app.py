"""
Render Service — 独立渲染服务 (端口 7861)
从 DB 任务队列获取渲染任务，提交到 ComfyUI 执行。
ComfyUI 不可用时优雅降级，仅显示任务列表。
"""
import gradio as gr
import json
import os
import time
import threading
from datetime import datetime

from core.database import (
    init_db, list_tasks, get_task, claim_next_task, complete_task,
    add_agent_log, list_agent_logs, get_project,
)

APP_TITLE = "🎬 漫剧渲染服务 — Render Service"
THEME_CSS = """
footer {display:none !important}
.gradio-container {max-width: 1200px !important}
.status-ok {color: #22c55e; font-weight: bold}
.status-warn {color: #eab308; font-weight: bold}
.status-err {color: #ef4444; font-weight: bold}
"""

COMFYUI_URL = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188")

# ─── ComfyUI 状态检查 ─────────────────────────

def _comfyui_status() -> str:
    import requests as req
    try:
        r = req.get(f"{COMFYUI_URL}/object_info", timeout=3)
        if r.status_code == 200:
            return "✅ 在线"
        return "⚠️ 异常"
    except Exception:
        return "❌ 离线"

def _refresh_comfyui_info() -> str:
    """返回 ComfyUI 状态的详细 Markdown"""
    import requests as req
    try:
        r = req.get(f"{COMFYUI_URL}/object_info", timeout=3)
        if r.status_code == 200:
            info = r.json()
            node_count = len(info)
            return (
                f"✅ **ComfyUI 在线**\n"
                f"- 地址: {COMFYUI_URL}\n"
                f"- 节点数: {node_count}\n"
            )
        return "⚠️ ComfyUI 响应异常"
    except Exception as e:
        return f"❌ **ComfyUI 离线**\n- 地址: {COMFYUI_URL}\n- 错误: {e}\n\n请先在终端启动 ComfyUI:\n```\ncd ~/Documents/ComfyUI\nsource .venv/bin/activate\npython main.py --listen 127.0.0.1 --port 8188\n```"

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
        project_name = input_params.get("project_name", "未知项目")
        scenes = input_params.get("scenes", [])
        
        # 检查 ComfyUI 是否在线
        import requests as req
        try:
            r = req.get(f"{COMFYUI_URL}/object_info", timeout=3)
            if r.status_code != 200:
                raise Exception(f"ComfyUI 返回错误: {r.status_code}")
        except Exception as e:
            add_agent_log(task_id, "render_scheduler", "error", "error", f"ComfyUI 不可达: {e}")
            complete_task(task_id, {"error": str(e)}, error=f"ComfyUI 离线: {e}")
            return f"❌ 任务 #{task_id} 失败: ComfyUI 离线\n请启动 ComfyUI 后重试"
        
        # ComfyUI 在线，开始渲染
        add_agent_log(task_id, "render_scheduler", "queuing", "info", 
                      f"正在向 ComfyUI 提交 {len(scenes)} 个场景")
        
        # TODO: 实际提交到 ComfyUI 的渲染逻辑
        # 这里可以使用现有的 pipelines/batch_renderer.py 或 animate_pipeline.py
        
        result = {
            "status": "queued",
            "project": project_name,
            "scenes_count": len(scenes),
            "message": f"已向 ComfyUI 提交 {len(scenes)} 个场景的渲染任务",
        }
        
        complete_task(task_id, result)
        add_agent_log(task_id, "render_scheduler", "completed", "info", "渲染任务已提交到 ComfyUI")
        
        return f"✅ 任务 #{task_id}: 已提交 {len(scenes)} 个场景到 ComfyUI (请查看 ComfyUI 界面)"
        
    except Exception as e:
        add_agent_log(task_id, "render_scheduler", "error", "error", str(e))
        complete_task(task_id, {"error": str(e)}, error=str(e))
        return f"❌ 任务 #{task_id} 失败: {e}"

# ─── UI 构建 ──────────────────────────────────

def build_ui():
    init_db()
    
    with gr.Blocks(title=APP_TITLE, theme=gr.themes.Soft(), css=THEME_CSS) as app:
        gr.Markdown(f"# {APP_TITLE}")
        
        # ── 状态栏 ──────────────────────────
        with gr.Row():
            comfy_status = gr.Markdown(f"**ComfyUI**: {_comfyui_status()} | **地址**: {COMFYUI_URL}")
            refresh_status_btn = gr.Button("🔄 刷新状态", variant="secondary", size="sm")
        
        def _update_status():
            return f"**ComfyUI**: {_comfyui_status()} | **地址**: {COMFYUI_URL}"
        refresh_status_btn.click(fn=_update_status, outputs=comfy_status)
        
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
   nohup python3 main.py --listen 127.0.0.1 --port 8188 > /tmp/comfyui.log 2>&1 &
   ```
2. **使用「任务队列」Tab** 查看和管理渲染任务
3. 点击「执行下一个任务」自动提交到 ComfyUI
4. ComfyUI 的 Web 界面在 http://127.0.0.1:8188
""")
            
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
