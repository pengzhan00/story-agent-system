# 🎬 漫剧故事工坊 — Story Agent System

**本地 Ollama 多 Agent 智能剧本创作系统** — 从创意构思到动画视频，全本地运行，无需联网。

```
  创意构想 → 🎬导演分析 → ✍️编剧剧本 → 👤角色设计 → 🏞️场景设计
  → 🎨美术指导 → 🎵作曲 → 🔊音效 → 🎙️配音 → ✅审查 → 🎬渲染 → 📦导出
```

---

## ✨ 特性

- **🤖 10个专业Agent** — 每个都是独立的 CLI 模块，可单独运行或组合
- **🏠 全本地运行** — Ollama LLM + SQLite 数据库，不需要任何 API Key
- **🎬 ComfyUI 集成** — SDXL + AnimateDiff 视频生成（动画/写实风格）
- **🔄 任务队列** — Agent 间通过 DB 异步通信，支持断点续做
- **🖥️ 可视化 UI** — Gradio Web 界面，10个功能 Tab
- **🛠️ 可扩展** — 每个 Agent 独立 `main.py --action xxx`, 可替换/新增

---

## 🏗️ 架构

```
story-agent-system/
├── agents/                    # 🤖 10个独立 Agent（每个都是 CLI）
│   ├── director/              # 🎬 导演 — 分析需求、分解任务
│   ├── writer/                # ✍️ 编剧 — 剧情、幕场结构
│   ├── character_designer/    # 👤 角色设计师
│   ├── scene_designer/        # 🏞️ 场景设计师
│   ├── art_director/          # 🎨 美术指导
│   ├── composer/              # 🎵 作曲师
│   ├── sound_designer/        # 🔊 音效师
│   ├── voice_actor/           # 🎙️ 配音（TTS 台词生成）
│   ├── render_scheduler/      # 🎬 渲染调度（ComfyUI 管理）
│   └── reviewer/              # ✅ 质量审查
├── core/                      # 🔧 核心基础设施
│   ├── database.py            # SQLite 持久化（10+ 表）
│   ├── ollama_client.py       # Ollama API 客户端
│   ├── orchestrator.py        # 🔥 一键全流程管线
│   └── task_queue.py          # 任务队列（Agent 间通信）
├── pipelines/                 # 📦 ComfyUI 渲染管线
├── ui/                        # 🖥️ Gradio Web UI
│   └── app.py                 # 10 个功能 Tab
├── main.py                    # 项目入口
├── start.sh                   # 一键启动脚本
├── setup.sh                   # 环境安装脚本
└── requirements.txt           # Python 依赖
```

### 10 个 Agent 详解

| Agent | CLI 命令 | 职责 |
|-------|----------|------|
| 🎬 **Director** | `python agents/director/main.py` | 分析创作构想，分解故事结构，调度任务 |
| ✍️ **Writer** | `python agents/writer/main.py` | 生成剧本大纲、幕场结构、扩写场景 |
| 👤 **CharacterDesigner** | `python agents/character_designer/main.py` | 角色外貌、性格、背景、关系网 |
| 🏞️ **SceneDesigner** | `python agents/scene_designer/main.py` | 场景环境、光照、氛围、道具 |
| 🎨 **ArtDirector** | `python agents/art_director/main.py` | 色调板、镜头语言、视觉一致性检查 |
| 🎵 **Composer** | `python agents/composer/main.py` | 主题曲、场景 BGM 创作 |
| 🔊 **SoundDesigner** | `python agents/sound_designer/main.py` | 环境音效、动作音效方案 |
| 🎙️ **VoiceActor** | `python agents/voice_actor/main.py` | 角色台词 TTS 生成 |
| 🎬 **RenderScheduler** | `python agents/render_scheduler/main.py` | ComfyUI 渲染队列调度、状态监控 |
| ✅ **Reviewer** | `python agents/reviewer/main.py` | 剧本连贯性、角色一致性、视觉风格审查 |

---

## 🚀 快速开始

### 前置条件

- **macOS** (Apple Silicon M1-M5, 建议 32GB+ RAM)
- **Python 3.10+**
- **Ollama** (LLM 推理)
- **ffmpeg** (视频合并)

### 一键安装

```bash
# 1. 克隆仓库
git clone https://github.com/<你的用户名>/story-agent-system.git
cd story-agent-system

# 2. 运行安装脚本
chmod +x setup.sh
./setup.sh
```

### 安装 Ollama 模型

```bash
# 安装 Ollama
brew install ollama

# 启动服务
ollama serve

# 拉取推荐模型（至少一个）
ollama pull gemma4:latest          # 主力模型（推荐）
ollama pull deepseek-r1:70b        # 深度推理备用
```

### 安装 ComfyUI（如需视频渲染）

```bash
git clone https://github.com/comfyanonymous/ComfyUI ~/Documents/ComfyUI
cd ~/Documents/ComfyUI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 安装节点（通过 ComfyUI Manager 或手动）
# - AnimateDiff-Evolved
# - ComfyUI-Impact-Pack
# - comfyui_controlnet_aux
```

### 启动系统

```bash
./start.sh
# → Gradio UI: http://127.0.0.1:7860
```

或分步手动：

```bash
# 1. 确保 Ollama 运行
ollama serve

# 2. 启动 ComfyUI（可选，渲染需要）
# ⚠️ 必须使用 ComfyUI 自己的 .venv（包含 torch）！
cd ~/Documents/ComfyUI && ./.venv/bin/python3 main.py --listen 127.0.0.1

# 3. 启动本系统
cd ~/myworkspace/projects/story-agent-system
source .venv/bin/activate
python3 main.py
```

### 🔍 环境诊断

如遇渲染失败，先检查环境：

```bash
./start.sh --env
# 输出示例（关键指标）：
#   ComfyUI Python: ~/Documents/ComfyUI/.venv/bin/python3
#   ComfyUI torch:  2.11.0         ← 必须成功，否则渲染不工作
#   ComfyUI pid:    12345
```

**常见问题排查：**

| 症状 | 原因 | 修复 |
|------|------|------|
| ComfyUI 端口通但 `KeyError: 't5xxl'` | 用错了 Python（无 torch 的 venv） | `./start.sh --restart` 自动修复 |
| CLIPTextEncodeFlux 节点不存在 | ComfyUI 降级模式运行 | 检查 `/tmp/comfyui.log` 的 ModuleNotFoundError |
| 渲染很慢或 MPS 崩溃 | batch_size 过大 | 降到 batch_size=8（ADE）或 1（Wan） |
| ComfyUI 启动后 BrokenPipeError | 后台运行未重定向 stdout | `nohup ... > file 2>&1 &`（start.sh 已处理） |

---

## 🎮 使用指南

### 一键全流程（最快上手）

1. 打开 UI → `http://127.0.0.1:7860`
2. 在「项目管理」Tab 输入创作构想
3. 选择类型和基调
4. 点击「🔥 一键全流程启动」
5. 等待系统自动完成：分析→剧本→角色→场景→美术→音乐→音效→渲染→导出

### 分步创作（精细控制）

| Tab | 说明 |
|-----|------|
| 📋 项目管理 | 创建/切换项目，一键全流程 |
| ✍️ 剧本 | 生成故事大纲、查看幕场结构 |
| 👤 角色 | 设计角色档案 |
| 🏞️ 场景 | 设计场景和氛围 |
| 🎨 美术指导 | 调色板、镜头语言、视觉一致性检查 |
| 🎵 音乐 | 创作主题曲和场景 BGM |
| 🔊 音效 | 设计环境/动作音效方案 |
| 🎬 渲染 | 选择场景 → ComfyUI 渲染 → 视频预览 |
| 📦 导出 | ffmpeg 合并视频 → ZIP 打包 |
| ⚙️ 设置 | 模型选择、系统状态、日志查看 |

### CLI 模式（高级用户）

```bash
# 导演 Agent — 分析创意
python agents/director/main.py --action analyze \
  --input '{"project_name":"测试","request":"一个关于少年剑客的成长故事"}' \
  --project-id 1

# 编剧 Agent — 生成剧本
python agents/writer/main.py --action generate_storyline \
  --input '{"premise":"少年剑客拜师学艺","acts":3}' \
  --project-id 1

# 渲染调度 — 查看 ComfyUI 状态
python agents/render_scheduler/main.py --action check_status

# 质量审查 — 检查项目一致性
python agents/reviewer/main.py --action review_script \
  --input '{"script_id":1}' \
  --project-id 1
```

---

## ⚙️ 配置

编辑 `.env` 文件：

```ini
# Ollama 模型配置
DEFAULT_MODEL=gemma4:latest       # 默认模型
CREATIVE_MODEL=gemma4:latest      # 创意类任务
DETAIL_MODEL=deepseek-r1:70b      # 深度分析任务

# ComfyUI
COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_DIR=~/Documents/ComfyUI

# UI
UI_HOST=127.0.0.1
UI_PORT=7860
```

---

## 📚 帮助

更多文档见 `docs/` 目录：
- `docs/USER_GUIDE.md` — 详细操作指南
- `ARCHITECTURE.md` — 架构设计说明

---

## 🖥️ 环境要求

| 组件 | 最低 | 推荐 |
|------|------|------|
| macOS | 14.0 (Sonoma) | 15.0 (Sequoia) |
| RAM | 16GB | 32GB+ (M5 Max 128GB ✅) |
| Python | 3.10 | 3.11-3.12 |
| Ollama | 0.5.0 | latest |
| ComfyUI | 0.19.0 | latest |
| ffmpeg | 6.0 | latest |
| 磁盘 | 20GB | 100GB+ (模型文件) |

---

## 📦 依赖

- **gradio** ≥ 4.0 — Web UI 框架
- **requests** — HTTP 客户端（Ollama/ComfyUI API）
- **Python 标准库** — sqlite3, json, subprocess, pathlib
