#!/usr/bin/env python3
"""
🎬 漫剧故事工坊 — Story Agent System
本地 Ollama 多 Agent 智能剧本创作系统
支持一键全流程：创意→剧本→角色→场景→美术→音乐→音效→渲染→导出

Usage:
  python main.py               # 启动 Gradio Web UI (默认)
  python main.py --cli         # 命令行模式
  python main.py --demo        # 演示模式
  python main.py --check       # 环境检查
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database import init_db, list_projects, get_project
from core.ollama_client import refresh_models, DEFAULT_MODEL

BANNER = r"""
  __ _   ___   ___   ___   _ __    ___   ___   _ __   ___
 / _` | / __| / __| / _ \ | '_ \  / __| / _ \ | '_ \ / __|
| (_| | \__ \ \__ \|  __/ | | | | \__ \|  __/ | | | |\__ \
 \__,_| |___/ |___/ \___| |_| |_| |___/ \___| |_| |_||___/
   ___ _         ___         __ _   ___   ___   ___   ___
  / __| |_  __ _| __|_ _ __ / _` | / __| / __| / __| / __|
  \__ \ ' \/ _` | _|\ \ '_/ | (_| | \__ \ \__ \ \__ \ \__ \
  |___/_||_\__,_|___/_/_|   \__,_| |___/ |___/ |___/ |___/
   __ _   ___   ___   ___   _ _    __ _   ___   ___
  / _` | / __| / __| / _ \ | ' \  / _` | / _ \ / __|
 | (_| | \__ \ \__ \|  __/ |_||_| \__,_| \___/ \___|
  \__,_| |___/ |___/ \___|
"""


def check_environment() -> dict:
    """全面环境检查"""
    import requests as req
    results = {}

    # Ollama
    try:
        r = req.get("http://localhost:11434/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            results["ollama"] = f"✅ 在线 ({len(models)} 模型: {', '.join(models[:4])}...)"
        else:
            results["ollama"] = "❌ 异常"
    except:
        results["ollama"] = "❌ 离线 (请运行 ollama serve)"

    # ComfyUI
    try:
        r = req.get("http://127.0.0.1:8188/queue", timeout=5)
        if r.status_code == 200:
            results["comfyui"] = "✅ 在线"
        else:
            results["comfyui"] = "⚠️ 返回异常"
    except:
        results["comfyui"] = "❌ 离线 (请启动 ComfyUI)"

    # Database
    db_path = os.path.expanduser("~/myworkspace/projects/story-agent-system/story_agents.db")
    if os.path.exists(db_path):
        size = os.path.getsize(db_path)
        results["database"] = f"✅ 存在 ({size/1024:.1f}KB)"
    else:
        results["database"] = "⚠️ 将被创建"

    # Output dir
    out_dir = os.path.expanduser("~/myworkspace/projects/story-agent-system/output")
    if os.path.isdir(out_dir):
        n_files = len([f for f in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, f))])
        results["output"] = f"✅ 就绪 ({n_files} 文件)"
    else:
        results["output"] = "✅ 就绪"

    return results


def cli_mode():
    """命令行模式"""
    print(BANNER)
    print("🎬 漫剧故事工坊 — CLI Mode")
    print("=" * 60)

    init_db()
    projs = list_projects()
    print(f"\n📁 Projects: {len(projs)}")
    for p in projs:
        print(f"  [{p.id}] {p.name} ({p.genre}) — {p.status}")

    print("\n🔍 环境检查:")
    env = check_environment()
    for k, v in env.items():
        print(f"  {k}: {v}")

    models = refresh_models()
    if models:
        print(f"\n🤖 可用模型: {len(models)}")
        for m in models[:5]:
            print(f"  • {m}")
        if len(models) > 5:
            print(f"  ... 及 {len(models)-5} 个其他")
    print(f"\n💡 运行 python main.py 启动 Web UI → http://localhost:7860")


def demo_mode():
    """演示模式"""
    init_db()
    projs = list_projects()
    if not projs:
        print("⚠️ 没有项目。请先启动 UI 创建项目或运行一键全流程。")
        return

    from agents.director import summarize_project
    for p in projs:
        print(f"\n{'='*60}")
        print(summarize_project(p.id))

    env = check_environment()
    print(f"\n{'='*60}")
    print("🔍 环境状态:")
    for k, v in env.items():
        print(f"  {k}: {v}")
    print(f"\n💡 控制台: http://localhost:7860")


def main():
    if "--cli" in sys.argv:
        cli_mode()
        return

    if "--demo" in sys.argv:
        demo_mode()
        return

    if "--check" in sys.argv:
        env = check_environment()
        print("=" * 50)
        print("🔍 漫剧故事工坊 — 环境检查")
        print("=" * 50)
        for k, v in env.items():
            icon = "✅" if v.startswith("✅") else ("⚠️" if v.startswith("⚠️") else "❌")
            print(f"  {icon} {k}: {v[1:].strip()}")
        return

    # 默认：启动 Web UI
    init_db()
    refresh_models()

    print(BANNER)
    print("🎬 漫剧故事工坊 — Starting Web UI...")

    from ui.app import build_ui
    app = build_ui()

    print("  🌐 http://127.0.0.1:7860")
    print("  📁 工作目录: ~/myworkspace/projects/story-agent-system/")
    print("  💡 按 Ctrl+C 退出\n")

    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True,
    )


if __name__ == "__main__":
    main()
