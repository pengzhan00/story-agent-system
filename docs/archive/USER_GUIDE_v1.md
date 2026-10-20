# 🎬 漫剧故事工坊 — 用户操作指南

> 全面指南：从零开始到完成一部动画短片

---

## 📑 目录

1. [系统概述](#1-系统概述)
2. [首次部署](#2-首次部署)
3. [核心工作流](#3-核心工作流)
4. [分步操作详解](#4-分步操作详解)
5. [CLI 模式](#5-cli-模式)
6. [跨设备迁移](#6-跨设备迁移)
7. [故障排除](#7-故障排除)
8. [FAQ](#8-faq)

---

## 1. 系统概述

漫剧故事工坊是一个**全本地的多 Agent 动画生产系统**。它把以下 AI 能力整合成一个自动化管线：

- **LLM 剧本创作** — 通过 Ollama 运行本地大模型（Gemma4 / DeepSeek）
- **动画渲染** — 通过 ComfyUI (SDXL + AnimateDiff) 生成视频
- **项目管理** — 所有资产保存在 SQLite 数据库，可长期复用

### 适用场景

- 🎬 **短视频创作者** — 快速生成故事动画
- 🎮 **游戏叙事原型** — 批量生成剧情分支
- 📚 **小说可视化** — 将文字章节转为动画预览
- 🧪 **AI 叙事实验** — 多 Agent 协作叙事研究

### 系统架构图

```
┌─────────────────────────────────────────────────────┐
│                  Gradio UI (:7860)                    │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐      │
│  │项目管理│ │剧本  │ │角色  │ │场景  │ │...10 Tabs   │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘      │
└─────────────────┬───────────────────────────────────┘
                  │ 调用
┌─────────────────▼───────────────────────────────────┐
│               Agent 层（10个独立CLI）                   │
│  director  writer  character_designer  scene_designer │
│  composer  art_director  sound_designer  voice_actor  │
│  render_scheduler  reviewer                           │
└──────────────┬──────────────────┬────────────────────┘
               │                  │
     ┌─────────▼──────┐  ┌───────▼──────────┐
     │   SQLite DB     │  │  ComfyUI (:8188)  │
     │  (项目/角色/剧本)│  │  (SDXL+AnimateDiff)│
     └────────────────┘  └──────────────────┘
               │
     ┌─────────▼──────┐
     │   Ollama (:11434)│
     │  (Gemma4等模型)  │
     └────────────────┘
```

---

## 2. 首次部署

### 第1步：安装基础工具

```bash
# 安装 Homebrew（如未安装）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 安装 Ollama
brew install ollama

# 安装 ffmpeg
brew install ffmpeg

# 安装 Python（如未安装）
brew install python@3.11
```

### 第2步：拉取 LLM 模型

```bash
# 启动 Ollama 服务
ollama serve &

# 拉取模型（至少选择一个）
ollama pull gemma4:latest       # ~5GB，轻量好用（推荐）
ollama pull deepseek-r1:70b     # ~40GB，深度推理但较慢
```

### 第3步：部署系统

```bash
# 克隆仓库
git clone <你的仓库URL>
cd story-agent-system

# 运行安装脚本
chmod +x setup.sh
./setup.sh
```

### 第4步：安装 ComfyUI（可选，渲染需要）

```bash
git clone https://github.com/comfyanonymous/ComfyUI ~/Documents/ComfyUI
cd ~/Documents/ComfyUI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 下载 SDXL 模型（至少一个 checkpoint）
# HuggingFace: https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0
# 放入 ~/Documents/ComfyUI/models/checkpoints/

# 安装 AnimateDiff 节点（通过 ComfyUI Manager）
# 或手动: git clone 到 ~/Documents/ComfyUI/custom_nodes/
```

### 第5步：启动

```bash
cd ~/myworkspace/projects/story-agent-system
./start.sh
# → 浏览器打开 http://127.0.0.1:7860
```

---

## 3. 核心工作流

### 3.1 一键全流程（推荐新手）

```
输入故事构想 → 系统自动执行：
  ① Director 分析构想 → 分解故事结构
  ② Writer 生成剧本 → 分幕分场
  ③ CharacterDesigner 创建角色档案
  ④ SceneDesigner 设计场景
  ⑤ ArtDirector 定义色调/镜头风格
  ⑥ Composer 创作配乐方案
  ⑦ SoundDesigner 设计音效方案
  ⑧ RenderScheduler 调度 ComfyUI 渲染
  ⑨ OutputManager 合并视频 + ZIP 导出
```

**操作路径**：
1. 打开「项目管理」Tab
2. 在「创作构想」输入框中描述你的故事
3. 选择类型（玄幻/仙侠/科幻...）、基调（热血/治愈/悬疑...）
4. 设置幕数（建议 2-3 幕）
5. 点击「🔥 一键全流程启动」
6. 等待系统完成（LLM 生成约 2-5 分钟，渲染约 5-10 分钟/场景）

### 3.2 分步精细化（推荐进阶用户）

如果你对某个环节不满意，可以分步操作：

```
Step 1: 📋 项目管理 → 新建项目
Step 2: ✍️ 剧本 → 生成剧本 → 预览
Step 3: 👤 角色 → 设计每个角色
Step 4: 🏞️ 场景 → 设计关键场景
Step 5: 🎨 美术指导 → 调色+镜头，检查一致性
Step 6: 🎵 音乐 → 主题曲 + 场景 BGM
Step 7: 🔊 音效 → 音效方案
Step 8: 🎬 渲染 → 选场景 → 开始渲染
Step 9: 📦 导出 → 合并视频 → ZIP 下载
```

---

## 4. 分步操作详解

### 📋 项目管理 Tab

**功能**：项目 CRUD + 一键全流程

- **新建项目**：填写名称、类型、简介后点击「创建」
- **切换项目**：从下拉框选择已有项目
- **删除项目**：选中后点击「删除」（⚠️ 不可恢复）
- **一键全流程**：输入构想后一键完成所有步骤

> 💡 **建议**：每个故事独立项目，方便管理

### ✍️ 剧本 Tab

**功能**：生成故事大纲

- 输入故事前提（或用项目描述）
- 点击「生成剧本」
- 预览显示：幕结构 → 每幕场景列表

> 剧本保存到数据库后，其他 Agent（角色/场景/渲染）会自动读取

### 👤 角色 Tab

**功能**：设计角色档案

- 输入角色名
- 点击「设计角色」
- 返回 JSON：包括外貌、性格、背景、声线、关系网

> 💡 **提示**：在同一项目中多次设计，角色会自动关联到剧本

### 🏞️ 场景 Tab

**功能**：设计场景

- 输入场景名称
- 选择情绪基调
- 点击「设计场景」
- 返回 JSON：环境描述、光照、道具、氛围

### 🎨 美术指导 Tab

含三个子 Tab：

- **色调板**：生成项目的色彩方案（主色/辅色/强调色/背景色）
- **镜头语言**：根据不同情绪设计镜头运动方案
- **一致性检查**：检查角色设计和场景描述是否视觉统一

### 🎵 音乐 Tab

含两个子 Tab：

- **主题曲**：为项目创作主题曲方案（风格/乐器/节奏）
- **场景 BGM**：为特定场景生成背景音乐描述

### 🔊 音效 Tab

**功能**：设计环境/动作音效方案

- 输入场景数量
- 点击「设计音效方案」
- 返回每个场景的音效清单

### 🎬 渲染 Tab

**功能**：剧本场景 → ComfyUI → 视频

- 查看 ComfyUI 运行状态
- 点击「获取场景列表」→ 从剧本中读取场景
- 选择要渲染的场景（可多选）
- 点击「开始渲染」

**渲染时间参考**（Mac M5 Max 128GB）：
| 分辨率 | 帧数 | 时间 |
|--------|------|------|
| 1024×1024 | 16帧 (8fps×2s) | ~6-8 分钟 |
| 1024×1024 | 32帧 (8fps×4s) | ~10-15 分钟 |
| 768×768 | 16帧 | ~4-6 分钟 |

### 📦 导出 Tab

- **合并视频**：将渲染好的场景视频合并成一个
- **导出 ZIP**：打包整个项目输出

### ⚙️ 设置 Tab

- **模型选择**：切换 Ollama 模型（gemma4 / deepseek-r1）
- **系统状态**：查看 Ollama、ComfyUI、数据库状态
- **运行日志**：查看最近 20 条日志

---

## 5. CLI 模式

每个 Agent 都是独立 CLI 程序，可以直接在终端运行：

```bash
# 激活虚拟环境
cd story-agent-system
source .venv/bin/activate

# 导演：分析创意
python agents/director/main.py \
  --action analyze \
  --input '{"project_name":"剑客传奇","request":"一个少年剑客的成长故事"}' \
  --project-id 1

# 编剧：生成剧本
python agents/writer/main.py \
  --action generate_storyline \
  --input '{"project_id":1,"premise":"少年剑客拜师学艺","acts":3}' \
  --project-id 1

# 作曲：创作主题曲
python agents/composer/main.py \
  --action compose_theme \
  --input '{"style":"epic","genre":"玄幻"}' \
  --project-id 1

# 渲染调度：检查 ComfyUI
python agents/render_scheduler/main.py --action check_status

# 渲染调度：提交渲染任务
python agents/render_scheduler/main.py \
  --action submit_render \
  --input '{"scene_number":1,"positive_prompt":"...","negative_prompt":"..."}' \
  --project-id 1

# 质量审查
python agents/reviewer/main.py \
  --action review_script \
  --input '{"script_id":1}' \
  --project-id 1

# 任务队列模式（Agent 作为长期 Worker 运行）
python agents/director/main.py --task-mode
```

### 管道操作示例

```bash
# 一键全流程（CLI 版）
python core/orchestrator.py \
  --premise "一只会说话的猫意外获得超能力" \
  --genre "玄幻" \
  --acts 3 \
  --render
```

---

## 6. 跨设备迁移

### 迁移步骤

```bash
# 旧设备 → 推送到 GitHub
cd story-agent-system
git add .
git commit -m "项目导出 $(date +%Y-%m-%d)"
git push

# 新设备 → 克隆并部署
git clone <你的仓库URL>
cd story-agent-system
chmod +x setup.sh
./setup.sh
```

### 需要在新设备单独安装

| 组件 | 安装方式 | 数据是否同步 |
|------|----------|-------------|
| Python 依赖 | `./setup.sh` 自动安装 | ✅ 包含在仓库 |
| Ollama | `brew install ollama` | ❌ 需要单独下载模型 |
| ComfyUI | `git clone` + 节点安装 | ❌ 需要单独下载 |
| LLM 模型 | `ollama pull gemma4:latest` | ❌ 需要下载 |
| SDXL 模型 | 放入 ComfyUI/models/ | ❌ 需要下载 |
| **项目数据** | 存储在 GitHub（SQLite DB） | ✅ 自动同步 |

> **重要**：数据库文件 (`story_agents.db`) 包含所有项目数据，推送 Git 后会自动同步到新设备

---

## 7. 故障排除

### 7.1 Ollama 连接失败

```
症状: 系统显示 "Ollama 离线"
解决:
  # 检查 Ollama 是否运行
  curl http://localhost:11434/api/tags
  
  # 如果没有返回，启动 Ollama
  ollama serve &
  
  # 检查是否有模型
  ollama list
  # 如果没有模型，拉取一个
  ollama pull gemma4:latest
```

### 7.2 ComfyUI 连接失败

```
症状: 渲染 Tab 显示 "ComfyUI 离线"
解决:
  # 检查 ComfyUI 是否运行
  curl http://127.0.0.1:8188/queue
  
  # 如果不在运行，启动
  cd ~/Documents/ComfyUI
  source .venv/bin/activate
  python3 main.py --listen 127.0.0.1
  
  # 检查正确 venv
  # 必须用 .venv/bin/python3，不用系统 python
```

### 7.3 渲染失败/输出黑屏

```
可能原因:
  1. 模型不兼容 — 检查 SDXL checkpoint + VAE + LoRA 匹配
  2. 节点未安装 — AnimateDiff / ControlNet 等节点缺失
  3. 显存不足 — 降低分辨率或用 Tiled VAE 解码
  4. IPAdapter + LoRA 冲突 — 两者不能同时启用

解决:
  # 检查 ComfyUI 日志
  tail -f /tmp/comfyui.log
```

### 7.4 BrokenPipeError

```
症状: ComfyUI 启动后崩溃，报 BrokenPipeError
原因: nohup 后台运行时未重定向 stdout
解决:
  # 正确启动方式
  nohup ./.venv/bin/python3 main.py > /tmp/comfyui.log 2>&1 &
```

### 7.5 Python 导入错误

```
症状: ModuleNotFoundError: No module named 'agents.xxx'
原因: 从旧版单文件升级到独立 Agent 目录后，缓存未更新
解决:
  find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
```

---

## 8. FAQ

**Q: 需要 GPU 吗？**
A: Apple Silicon M 系列芯片即可（MPS 加速）。Intel Mac 不推荐。

**Q: 需要联网吗？**
A: 不需要。所有 AI 推理都在本地通过 Ollama 进行。

**Q: 一个项目能保存多少内容？**
A: 数据库无上限。所有剧本、角色、场景、音乐方案都持久化存储。

**Q: 可以替换 LLM 模型吗？**
A: 可以。在 `.env` 中修改 `DEFAULT_MODEL`，或在设置 Tab 中选择。

**Q: 渲染出来的视频是什么规格？**
A: 默认 1024×1024, 8fps, 2-4 秒。可在 workflow JSON 中调整。

**Q: 可以批量渲染吗？**
A: 可以。在渲染 Tab 选择多个场景，或在渲染 Tab 单击渲染。

**Q: 系统支持中文吗？**
A: 完全支持。UI、剧本、提示词全部中文。

**Q: 添加自己的 Agent？**
A: 复制 `agents/xxx/` 模板，实现 `core.py` 中的 `run_action()`，在 UI 中 import 即可。

---

> **最后更新**: 2026-04-25
> **系统版本**: v2.0 (架构C — 独立Agent CLI)
