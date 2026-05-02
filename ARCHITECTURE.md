# Story Agent System — 架构真相源
> **版本**: 2026-05-02 | **环境**: macOS M5 Max 128GB | **目标**: 红果短剧 + 大电影工业化生产

---

## 目录
1. [系统定位](#1-系统定位)
2. [硬件环境](#2-硬件环境)
3. [技术栈全景](#3-技术栈全景)
4. [能力矩阵（必备 vs 现状）](#4-能力矩阵)
5. [模型生态体系](#5-模型生态体系)
6. [系统架构](#6-系统架构)
7. [数据模型](#7-数据模型)
8. [生产流水线（12 阶段）](#8-生产流水线)
9. [渲染管线（5 级 fallback）](#9-渲染管线)
10. [音频管线](#10-音频管线)
11. [视频合成管线](#11-视频合成管线)
12. [配置文件真相](#12-配置文件真相)
13. [模型清单（完整）](#13-模型清单)
14. [差距分析与路线图](#14-差距分析与路线图)

---

## 1. 系统定位

```
Story Agent System = 本地 AI 影视制造系统

输入：一句创意（中文自然语言）
输出：可直接上传红果/抖音/快手的 MP4 短剧

核心能力：
  - 全程离线（Ollama + ComfyUI + 本地模型）
  - 多 Agent 协作创作（9 个专业 Agent + 12 阶段流水线）
  - 多级渲染回退（保证批量生产不卡死）
  - SQLite 持久化（随时断点续作）
```

---

## 2. 硬件环境

| 资源 | 规格 | 战略意义 |
|---|---|---|
| CPU | Apple M5 Max | Metal 加速，无独显显存上限 |
| 内存 | 128 GB 统一内存 | **可同时装载全部模型** |
| 存储 | NVMe SSD | 渲染中间帧 I/O 瓶颈低 |
| 加速 | Metal Performance Shaders (MPS) | GPU 推理，无需 CUDA |

### 内存分配计划（全时在线）

```
进程                               内存占用    状态
──────────────────────────────────────────────────
ComfyUI #1 (视频生成)              ~25–40 GB  常驻
  └─ Wan2.2 TI2V 5B GGUF (1.4GB)
  └─ UMT5 Encoder (11GB)
  └─ Wan2.2 VAE
  └─ InstantID 全套 (5GB)
ComfyUI #2 (音乐生成)              ~15–25 GB  常驻（可选）
  └─ ACE-Step 1.5 XL-SFT (9.3GB)
  └─ qwen_0.6b + qwen_4b (8.9GB)
  └─ ace_1.5_vae (322MB)
Ollama (qwen3:8b)                  ~5–8 GB   常驻
ChatTTS                            ~2 GB     常驻
ffmpeg 合成                        ~1–2 GB   按需
──────────────────────────────────────────────────
估算峰值合计:                       ~50–75 GB
剩余安全缓冲:                       ~50–75 GB  ✅ 充裕
```

---

## 3. 技术栈全景

```
┌─────────────────────────────────────────────────────────────┐
│                     Web UI (Gradio)                         │
│  ui/app.py (7860)              ui/render_app.py (7861)      │
└─────────────────┬───────────────────────────┬───────────────┘
                  │                           │
┌─────────────────▼───────────────────────────▼───────────────┐
│              Orchestrator (core/orchestrator.py)            │
│         12-stage generator pipeline + task queue            │
└────────┬────────┬────────┬────────┬────────┬────────────────┘
         │        │        │        │        │
    ┌────▼──┐ ┌───▼──┐ ┌───▼──┐ ┌───▼──┐ ┌───▼──────────┐
    │Agents │ │ LLM  │ │Render│ │Audio │ │  Compositor  │
    │ (×9)  │ │Ollama│ │ Pipe │ │ Pipe │ │  (ffmpeg)    │
    └────┬──┘ └───┬──┘ └───┬──┘ └───┬──┘ └──────────────┘
         │        │        │        │
    ┌────▼────────▼───┐ ┌──▼────────▼──┐
    │  SQLite DB      │ │   ComfyUI    │
    │  (18 tables)    │ │  (port 8188) │
    └─────────────────┘ └──────────────┘
```

### 核心依赖

| 组件 | 版本 | 路径 |
|---|---|---|
| Python | 3.14.4 | `/opt/homebrew/bin/python3` |
| Gradio | latest | `.venv/` |
| Ollama | latest | `http://localhost:11434` |
| ComfyUI | latest | `~/Documents/ComfyUI/` |
| ffmpeg | 8.1 | `/opt/homebrew/bin/ffmpeg` |
| ChatTTS | latest | `~/chattts/venv/` |
| Default LLM | `qwen3:8b` | 本地 Ollama |

---

## 4. 能力矩阵

> **工业化生产必备能力 × 当前实现状态**
>
> **模型选型原则：成套选，确保输入/输出格式一致。**
> 视频: Wan2.2 生态为主干（T2V / TI2V / VACE 同一架构）
> 音频: ACE-Step 1.5 生态为主干（BGM / 人声 / 音效）

### 4.1 视觉生成能力

| 能力 | 重要性 | 当前状态 | 技术路径 |
|---|---|---|---|
| **文生图** (txt2img) | 必备 | ✅ 已有工作流 | Flux 2 Klein 9B + ComfyUI |
| **图生图** (img2img) | 必备 | ❌ 无工作流 | Flux img2img workflow 待创建 |
| **文生视频** (txt2video) | 必备 | ❌ 需 T2V 模型 | Wan2.2 **T2V-14B**（与 TI2V 不同模型） |
| **图生视频** (img2video) | 必备 | ✅ 已有管线 | Wan2.2 TI2V-5B |
| **视频生视频** (vid2vid) | 重要 | ⚠️ 节点已在无工作流 | Wan2.2 VACE (`WanVaceToVideo` 节点已确认) |
| **LoRA 注入** | 必备 | ✅ 代码已实现 | `inject_loras()` 已在 render_pipeline.py |
| **LoRA 训练** | 重要 | ❌ 未实现 | kohya-ss / LoRA-Easy-Training Scripts |
| **角色一致性** InstantID | 必备 | ⚠️ 模型就绪未激活 | `inject_instantid()` 已写，未连通 render() |
| **ControlNet 引导** | 有用 | ⚠️ 代码有，未测试 | `inject_controlnet()` 在 render_pipeline.py |

### 4.2 音频生成能力

> **重要澄清：ACE-Step 是音乐生成模型，不是对话 TTS。**
>
> | 场景 | 工具 | 说明 |
> |---|---|---|
> | 角色台词朗读 | ChatTTS → EdgeTTS | 自然语音对话，ACE-Step 不适合 |
> | BGM / 背景音乐 | ACE-Step 1.5 | 主力，模型已下载 |
> | 主题曲 / 带词人声 | ACE-Step 1.5 + lyrics | 中文歌词支持 |
> | 音效 (SFX) | AudioCraft (audiogen) | 待安装 |

| 能力 | 重要性 | 当前状态 | 技术路径 |
|---|---|---|---|
| **对话 TTS（朗读）** | 必备 | ✅ ChatTTS 就绪 | ChatTTS(主) → EdgeTTS(备) → pyttsx3(兜底) |
| **文生音乐/BGM** | 必备 | ⚠️ 模型就绪有Bug | ACE-Step 1.5 via ComfyUI（路径/节点需修复） |
| **音乐续写/编辑** | 重要 | ❌ 未实现 | ACE-Step retake 功能 |
| **图/视频生音乐** | 有用 | ❌ 未实现 | ACE-Step 多模态输入 |
| **带歌词人声合成** | 有用 | ❌ 未实现 | ACE-Step lyrics→vocal 功能 |
| **音效生成** | 必备 | ⚠️ ffmpeg 占位 | AudioCraft audiogen-medium（待安装） |

### 4.3 LoRA 体系

```
角色 LoRA   → 固定特定角色的外貌（最重要，每集保持一致性）
风格 LoRA   → 锁定整部剧的画风（水墨/写实/卡通/动漫）
场景 LoRA   → 固定特定场景的环境（古镇/宫殿/现代办公室）
动作 LoRA   → 特定动作/姿势引导

当前状态：
  ✅ inject_loras()     代码已实现（render_pipeline.py）
  ✅ characters.lora_ref  DB 字段已有
  ✅ scene_assets.lora_ref DB 字段已有
  ❌ LoRA 训练入口       需集成 kohya-ss 或 SimpleTuner
  ❌ LoRA 文件管理 UI   需在 Gradio 界面添加上传/绑定入口
```

---

## 5. 模型生态体系

### 5.1 视频生成体系（Wan2.2 生态为核心）

```
Wan2.2 生态（阿里通义·万象 2.2）— 成套使用，格式统一
│
├── T2V-14B      纯文→视频（对话中提及场景直接生成）
│   官方: huggingface.co/Wan-AI/Wan2.2-T2V-14B-GGUF
│   推荐: Wan2.2-T2V-14B-Q4_K_M.gguf (~8.5GB)
│   ComfyUI节点: WanVideoToVideo (复用 TI2V 节点集)
│   状态: ❌ 未下载（正在下载中）
│
├── TI2V-5B      文+参考图→视频（角色一致性最强）★ 主力
│   本地: ~/Documents/ComfyUI/models/unet/Wan2.2-TI2V-5B-Q4_K_M.gguf
│   大小: 1.4 GB ✅
│   文字编码器: wan2.2_umt5/models_t5_umt5-xxl-enc-bf16.pth (11GB) ✅
│   VAE: Wan2.2_VAE.safetensors ✅
│   ComfyUI节点: WanImageToVideo ✅
│
└── VACE         视频→视频（风格迁移/重绘）
    官方: github.com/Wan-AI/Wan2.2-VACE / huggingface.co/Wan-AI/Wan2.2-VACE
    ComfyUI节点: WanVaceToVideo ✅ 已在但无工作流
    状态: ❌ 未下载

高质量帧体系（Flux 2 Klein）— 用于 Stage1 关键帧生成
├── Klein 9B: checkpoints/flux_2_klein_9B/flux-2-klein-9b.safetensors (17GB) ✅
└── Klein 4B: diffusion_models/flux-2-klein-4b.safetensors (0B ❌ 空文件)

动漫风格体系（AnimateDiff 回退）
└── AnimagineXL 3.1 + hsxl motion ✅ 已就绪
```

### 5.2 音频体系（ACE-Step 1.5 生态为核心）

```
ACE-Step 1.5 生态（ACE Studio + StepFun）— 音乐生成主力
│
├── 模型选型（三档，已全部下载）:
│   ├── Turbo (3.5B):   acestep_v1.5_turbo.safetensors        (3.9GB) ✅
│   ├── XL-SFT (4B):    acestep_v1.5_xl_sft_bf16.safetensors  (9.3GB) ✅ 推荐质量最优
│   └── XL-Turbo (4B):  acestep_v1.5_xl_turbo_bf16.safetensors(9.3GB) ✅ 推荐速度+质量
│
├── 文字编码器（已全部下载）:
│   ├── qwen_0.6b_ace15.safetensors (1.1GB) ✅
│   └── qwen_4b_ace15.safetensors   (7.8GB) ✅
│
├── VAE: ace_1.5_vae.safetensors (322MB) ✅
│
├── ComfyUI 节点（已安装确认）:
│   ├── UNETLoader             ✅ (确认能看到 ACE-Step 模型)
│   ├── DualCLIPLoader         ✅ (qwen_0.6b + qwen_4b)
│   ├── EmptyAceStep1.5LatentAudio ✅
│   ├── TextEncodeAceStepAudio1.5  ✅
│   ├── AceStepText2MusicGenParams ✅
│   ├── AceStepText2MusicServer    ✅
│   ├── VAEDecodeAudio             ✅
│   └── SaveAudioMP3               ✅
│
└── ⚠️ 代码 Bug（待修复）:
    audio_pipeline._check_acestep_music() 路径硬编码
    ~/Documents/ComfyUI/models/ → 应改为 ~/myworkspace/ComfyUI_models/

对话 TTS 体系（独立于 ACE-Step，处理角色朗读）
├── ChatTTS    ✅ ~/chattts/venv/  中文最优，男:seed=2，女:seed=42
├── EdgeTTS    ❌ 需 pip install edge-tts（在线，无模型）
├── Kokoro     ❌ 需 pip install kokoro（本地，英文较好）
└── pyttsx3    ✅ 兜底

音效体系
├── AudioCraft (facebook/audiogen-medium) ← 推荐下载（约1.5GB）
└── ffmpeg 合成音效（当前占位方案）
```

### 5.3 LLM 体系（Ollama 本地）

```
当前: qwen3:8b (DEFAULT_MODEL in core/ollama_client.py)

建议分工（M5 Max 128GB 可同时加载）:
  创作阶段 (director/writer/character/scene/art)
    → qwen2.5:72b 或 deepseek-r1:32b（最强中文创作）
  分析阶段 (music/sound/review)
    → qwen3:8b（够用，速度快）
  分镜规划 (shot planning)
    → qwen3:14b（平衡，结构化输出）
```

---

## 6. 系统架构

### 目录结构

```
story-agent-system/
├── main.py                     # 入口: UI(7860) / 渲染服务(7861) / CLI
├── ARCHITECTURE.md             # ← 本文件（真相源）
├── capability_audit.json       # 模型扫描快照（手动按需更新）
├── story_agents.db             # SQLite 主数据库（WAL 模式）
│
├── core/
│   ├── orchestrator.py         # 12 阶段流水线总控
│   ├── database.py             # CRUD（18 张表，RLock + WAL）
│   ├── models.py               # 数据模型（dataclass）
│   ├── ollama_client.py        # LLM 调用（qwen3:8b 默认）
│   ├── asset_registry.py       # 资产缓存与复用
│   ├── comfyui_env.py          # ComfyUI 环境检测
│   ├── model_manager.py        # 模型生命周期
│   ├── task_queue.py           # 异步任务队列
│   ├── pipeline_state.py       # 流水线状态追踪
│   ├── change_manifest.py      # 变更记录
│   └── edit_agent.py           # AI 辅助编辑
│
├── agents/                     # 9 个专业 Agent
│   ├── director/               # 分析创意，定方向
│   ├── writer/                 # 生成剧本
│   ├── character_designer/     # 外貌/性格/声音/ComfyUI prompt
│   ├── scene_designer/         # 环境/灯光/氛围/ComfyUI prompt
│   ├── art_director/           # 色调/镜头语言
│   ├── composer/               # 主题/BGM 方案
│   ├── sound_designer/         # 环境音/动效设计
│   ├── voice_actor/            # TTS 调度
│   ├── reviewer/               # 内容评估
│   └── render_scheduler/       # 分镜规划
│
├── pipelines/
│   ├── render_pipeline.py      # 渲染管线（ABC + 5 实现 + Dispatcher）
│   ├── pipeline_config.json    # 管线配置（真相源）
│   ├── audio_pipeline.py       # 音频（TTS + ACE-Step + 混音）
│   ├── batch_renderer.py       # 批量渲染调度
│   ├── compositor.py           # 视频合成（shot → episode）
│   ├── quality_gate.py         # 质量门控
│   ├── output_manager.py       # 输出文件管理
│   │
│   ├── wan2_ti2v_workflow.json        ✅ Wan2.2 TI2V
│   ├── flux_txt2img_workflow.json     ✅ Flux 9B txt2img
│   ├── animatediff_workflow.json      ✅ AnimateDiff 回退
│   ├── wan2_t2v_workflow.json         ❌ 待创建（纯文→视频）
│   ├── wan2_vace_workflow.json        ❌ 待创建（视频→视频）
│   └── img2img_workflow.json          ❌ 待创建（图→图）
│
└── ui/
    ├── app.py                  # 主 UI（创作 + 管理）
    ├── render_app.py           # 渲染服务 UI
    └── edit_panel.py           # AI 辅助编辑面板
```

---

## 7. 数据模型

### 实体关系

```
Project
  ├── Script → Acts(JSON) → Scenes → Shots
  ├── Character       (lora_ref, ip_ref_images, voice_profile)
  ├── SceneAsset      (lora_ref, ref_images)
  ├── MusicTheme      (BGM 设计方案)
  ├── SoundEffect     (音效设计方案)
  └── Episode
        └── Shot
              ├── render_payload (JSON → ComfyUI prompt)
              ├── dialogue       (JSON → TTS 输入)
              ├── RenderJob      (渲染尝试，含 fallback 记录)
              ├── ShotReview     (QC 门控记录)
              └── AudioAsset     (音频文件引用)
```

### 18 张表状态

| 表 | 状态 | 说明 |
|---|---|---|
| projects, scripts, characters, scene_assets | ✅ 核心使用 | 主创作数据 |
| episodes, shots | ✅ 核心使用 | 生产调度核心 |
| music_themes, sound_effects | ✅ 核心使用 | 音频设计数据 |
| render_jobs | ✅ 核心使用 | 含 fallback_used, render_tier 字段 |
| audio_assets | ✅ 核心使用 | TTS/BGM/SFX 文件引用 |
| generation_logs | ✅ 核心使用 | LLM 调用审计 |
| export_manifests | ✅ 核心使用 | 导出记录 |
| shot_reviews | ⚠️ 记录但不触发重试 | QC 失败后需手动干预 |
| asset_versions, subtitle_revisions | ⚠️ 基础实现 | 版本控制轻度使用 |
| delivery_packages | ❌ 孤立表 | 未从 orchestrator 调用 |
| task_queue, pipeline_runs | ⚠️ 结构完整使用少 | 异步任务框架 |

---

## 8. 生产流水线（12 阶段）

```
用户输入创意
    ↓
Stage 1  导演分析     → projects.genre/synopsis
Stage 2  编剧        → scripts.acts (JSON)         ⚠️ 缺 target_shots 时长约束
Stage 3  角色设计    → characters (含 lora_ref)
Stage 4  场景设计    → scene_assets (含 lora_ref)
Stage 5  美术指导    → style_guide
Stage 6  音乐设计    → music_themes
Stage 7  音效设计    → sound_effects
Stage 8  分镜规划    → shots (render_payload + dialogue + audio_plan)
Stage 9  视频渲染    → RenderDispatcher → ComfyUI → MP4
Stage 10 音频生成    → TTS(对话) + ACE-Step(BGM) + ffmpeg(SFX)
Stage 11 音视频合成  → Compositor → 混音 + 字幕 + 过渡
Stage 12 集数导出    → episode MP4                ⚠️ DeliveryPackage 未连通
```

---

## 9. 渲染管线（5 级 fallback）

```
shot.render_payload → RenderDispatcher.render()
    │
    ├─ [P3] Wan2TI2VPipeline        ★ production_ready=true, active
    │        文+图→视频，16fps，角色一致性强
    │        条件: Wan2.2 GGUF (1.4GB ✅) + UMT5 Encoder (11GB ✅) + VAE ✅
    │        输出: 49帧 832×480 16fps → ⚠️ 需改竖屏 720×1280
    │
    ├─ [P2] FluxWan2TwoStagePipeline  production_ready=false
    │        Flux Klein 4B → Wan2.2 TI2V 两阶段
    │        条件: flux-2-klein-4b.safetensors (0B ❌ 空文件)
    │        状态: 当前不可用，待 4B 下载完成
    │
    ├─ [P1] AnimateDiffPipeline (animagine)  production_ready=false
    │        动漫风格 SDXL 动画
    │        条件: animagine-xl-3.1 ✅ + hsxl motion ✅
    │        输出: 16帧 1024×1024 8fps → ⚠️ 方屏，帧率低
    │
    ├─ [P1] AnimateDiffPipeline (sd_xl_base)  回退1
    │
    ├─ [P10] StaticFramePipeline  回退2（单帧延伸为视频）
    │
    └─ [P99] StubPipeline  最终兜底（黑帧）

ComfyUI 工作流文件:
  ✅ wan2_ti2v_workflow.json     — Wan2TI2VPipeline
  ✅ flux_txt2img_workflow.json  — FluxWan2 Stage1
  ✅ animatediff_workflow.json   — AnimateDiffPipeline
  ❌ wan2_t2v_workflow.json      — 纯文→视频（待创建）
  ❌ wan2_vace_workflow.json     — 视频→视频（待创建）
  ❌ img2img_workflow.json       — 图→图（待创建）
```

---

## 10. 音频管线

### 对话 TTS 链（角色台词朗读）

```
角色台词 → _ranked_tts_backends()
    ├─ [1] ChatTTS        ✅ ~/chattts/venv/  中文最优
    │        男:seed=2, 女:seed=42（已校正）
    ├─ [2] EdgeTTS        ❌ 未安装 pip install edge-tts
    ├─ [3] Kokoro         ❌ 未安装 pip install kokoro
    ├─ [4] Bark           ⚠️ 模型未完整下载
    └─ [5] pyttsx3        ✅ 系统兜底

⚠️ Bug: TTS 时长 = 字符数 × 0.12s（估算非实测）
   → 字幕时间轴漂移，音视频不同步
   修复: 先生成音频 → ffprobe 读实际时长 → 再构建视频时间轴
```

### 音乐生成链（BGM、主题曲）

```
音乐需求 → generate_music()
    ├─ [1] ACE-Step 1.5 via ComfyUI   ★ 模型全套已下载
    │        生成: BGM / 带歌词人声 / 中文歌曲
    │        推荐模型: XL-SFT (9.3GB) 质量最优
    │        推荐组合: qwen_0.6b + qwen_4b + ace_1.5_vae
    │        ⚠️ Bug1: 路径检查指向 ~/Documents/ComfyUI/models/
    │                  实际: ~/myworkspace/ComfyUI_models/
    │        ⚠️ Bug2: DualCLIPLoader type="ace" 不在下拉列表
    │                  待确认正确 type 值或改用 AceStepText2MusicServer
    │
    ├─ [2] MusicGen (AudioCraft)       ✅ musicgen-small (802MB)
    │        质量较低，建议升级 musicgen-large (3.3GB)
    │
    └─ [3] ffmpeg 静音                  兜底

正确的 ACE-Step ComfyUI 工作流:
  "1" UNETLoader → acestep_v1.5_xl_sft_bf16.safetensors
  "2" DualCLIPLoader → qwen_0.6b + qwen_4b
  "3" VAELoader → ace_1.5_vae.safetensors
  "4" TextEncodeAceStepAudio1.5 (tags, lyrics, bpm, duration, language="zh")
  "5" ConditioningZeroOut (负向条件)
  "6" EmptyAceStep1.5LatentAudio (seconds=duration)
  "7" ModelSamplingAuraFlow (shift=3.0)
  "8" KSampler (steps=8, cfg=1.0, euler, simple)
  "9" VAEDecodeAudio
  "10" SaveAudioMP3
```

### 音效生成链

```
音效描述 → generate_sfx()
    ├─ AudioCraft (audiogen-medium)   ❌ 待安装（约1.5GB）
    └─ ffmpeg 合成                    当前占位
```

---

## 11. 视频合成管线

### Shot 级合成（compositor.compose_shot）

```
视频 + TTS + BGM + SFX
    → ffmpeg amix（TTS:1.0 + BGM:0.25 + SFX:0.5）
    → SRT 生成（⚠️ 时间轴基于 TTS 估算）
    → ffmpeg 字幕 burn-in（硬编码）
    → composed_{shot_id}.mp4
```

### Episode 级合成（compositor.compose_episode）

```
所有 composed shots（按 act/scene/shot 排序）
    ├── crossfade > 0: pairwise xfade（慢，流畅）
    └── crossfade = 0: concat demuxer（快，无过渡）
    → episode_{n}.mp4 → exports/
```

### 红果平台交付标准

| 参数 | 标准 | 当前状态 |
|---|---|---|
| 分辨率 | 1080×1920 或 720×1280 (9:16) | ⚠️ 横屏/方屏 |
| 帧率 | 24fps 或 30fps | ⚠️ 8-16fps |
| 编码 | H.264 | ✅ libx264 |
| 字幕 | 底部居中，黑体加粗，描边≥40pt | ⚠️ 未优化 |
| 单集时长 | 3-8 分钟 | ⚠️ 无约束参数 |

---

## 12. 配置文件真相

### pipeline_config.json 关键字段

```
active_pipeline: "wan2_ti2v"

wan2_ti2v:
  gguf:     ~/Documents/ComfyUI/models/unet/Wan2.2-TI2V-5B-Q4_K_M.gguf  ✅ (1.4GB)
  encoder:  ~/Documents/ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
            ⚠️ 此文件 0B！实际有效文件:
            wan2.2_umt5/models_t5_umt5-xxl-enc-bf16.pth (11GB) via extra_model_paths
  width/height: 832×480  ⚠️ 横屏，需改 720×1280
  instantid: enabled=true ✅ 配置开，代码未激活

extra_model_paths.yaml（ComfyUI 模型映射）:
  base_path: ~/myworkspace/ComfyUI_models/
  checkpoints, unet, diffusion_models, text_encoders, vae 等均已映射
```

---

## 13. 模型清单（完整）

### 视频生成

| 模型文件 | 大小 | 状态 | 用途 |
|---|---|---|---|
| checkpoints/animagine-xl-3.1/animagine-xl-3.1.safetensors | 6.5 GB | ✅ | AnimateDiff 动漫 |
| checkpoints/sd_xl_base_1.0.safetensors | 6.5 GB | ✅ | AnimateDiff 通用 |
| animatediff_models/hsxl_temporal_layers.f16.safetensors | 453 MB | ✅ | AnimateDiff 动作 |
| checkpoints/flux_2_klein_9B/flux-2-klein-9b.safetensors | 17 GB | ✅ | txt2img 超高质量 |
| diffusion_models/flux-2-klein-4b.safetensors | 0 B | ❌ 空 | FluxWan2 Stage1 |
| unet/Wan2.2-TI2V-5B-Q4_K_M.gguf | 1.4 GB | ✅ | TI2V 主力 |
| text_encoders/wan2.2_umt5/models_t5_umt5-xxl-enc-bf16.pth | 11 GB | ✅ | Wan2.2 编码器 |
| text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors | 0 B | ❌ 空 | — |
| vae/Wan2.2_VAE.safetensors | ~1 GB | ✅ | Wan2.2 VAE |
| Wan2.2-T2V-14B-Q4_K_M.gguf | ~8.5 GB | ⬇️ 下载中 | T2V 主力 |

### 音频

| 模型文件 | 大小 | 状态 | 用途 |
|---|---|---|---|
| diffusion_models/acestep_v1.5_turbo.safetensors | 3.9 GB | ✅ | ACE-Step 快速 |
| diffusion_models/acestep_v1.5_xl_sft_bf16.safetensors | 9.3 GB | ✅ | ACE-Step 最高质量 ★推荐 |
| diffusion_models/acestep_v1.5_xl_turbo_bf16.safetensors | 9.3 GB | ✅ | ACE-Step 速度+质量 |
| text_encoders/qwen_0.6b_ace15.safetensors | 1.1 GB | ✅ | ACE-Step 编码器 |
| text_encoders/qwen_4b_ace15.safetensors | 7.8 GB | ✅ | ACE-Step 编码器 |
| vae/ace_1.5_vae.safetensors | 322 MB | ✅ | ACE-Step VAE |
| ChatTTS (全套) | ~1.5 GB | ✅ | 中文对话 TTS |
| musicgen-small | 802 MB | ✅ | BGM 备用（低质） |

### 角色一致性（InstantID）

| 模型文件 | 大小 | 状态 |
|---|---|---|
| controlnet/InstantID-ControlNet.safetensors | 2.3 GB | ✅ |
| instantid/ip-adapter.bin | 1.6 GB | ✅ |
| insightface/models/antelopev2/ | ~265 MB | ✅ |
| clip_vision/ | 3.7 GB | ✅ |

### LLM (Ollama)

| 模型 | 大小 | 状态 | 建议用途 |
|---|---|---|---|
| qwen3:8b | ~5 GB | ✅ 当前默认 | 分析/分类/分镜 |
| qwen2.5:72b 或 deepseek-r1:32b | ~45 GB | ❌ 建议下载 | 创作阶段 |

---

## 14. 差距分析与路线图

### P0 — 阻塞生产（立即修复）

| # | 问题 | 位置 | 修复 | 工时 |
|---|---|---|---|---|
| 1 | 竖屏分辨率 | `pipeline_config.json` | Wan2.2: 720×1280；AnimateDiff: 768×1344 | 1h |
| 2 | ACE-Step 路径 Bug | `audio_pipeline.py:_check_acestep_music()` | 路径改为 `~/myworkspace/ComfyUI_models/` | 1h |
| 3 | ACE-Step 工作流用 XL 模型 | `audio_pipeline.py:generate_music_acestep()` | 切换到 xl_sft 模型 + 修正节点 type | 2h |
| 4 | InstantID 未激活 | `render_pipeline.py:AnimateDiffPipeline.render()` | reference_face_image 存在时调用 inject_instantid() | 4h |

### P1 — 严重影响质量

| # | 问题 | 修复 | 工时 |
|---|---|---|---|
| 5 | TTS 时长估算非实测 | TTS 先行 → ffprobe → 重建时间轴 | 6h |
| 6 | QC 失败不重试 | 自动降级到下一 tier 重试 | 4h |
| 7 | 帧率 8/16fps → 24fps | 集成 RIFE 补帧节点 | 4h |
| 8 | 无 txt2video 管线 | 下载 Wan2.2-T2V + 创建工作流 | 4h |
| 9 | 无 vid2vid 管线 | WanVaceToVideo 节点已有，创建工作流 | 4h |
| 10 | LLM 创作质量低 | 下载 qwen2.5:72b，更新 STAGE_MODEL_DEFAULTS | 1h |

### P2 — 工业化加分项

| # | 问题 | 修复 | 工时 |
|---|---|---|---|
| 11 | 单集时长无约束 | target_shots 参数注入 Orchestrator | 3h |
| 12 | DeliveryPackage 未连通 | orchestrator 末尾调用 create_delivery_package | 2h |
| 13 | 多集角色漂移 | project 级 face bank + LoRA 版本锁定 | 8h |
| 14 | LoRA 训练入口 | 集成 kohya-ss CLI | 16h |
| 15 | img2img 无工作流 | 创建 Flux img2img workflow | 3h |
| 16 | 两 ComfyUI 实例并行 | max_workers=2，端口 8188+8189 | 8h |
| 17 | 字幕样式优化 | ffmpeg fontfile/fontsize/borderw | 1h |
| 18 | EdgeTTS 安装 | pip install edge-tts | 30min |

---

## 附录：关键 API

```python
# 渲染
from pipelines.render_pipeline import get_dispatcher
dispatcher = get_dispatcher()
matrix = dispatcher.probe(force=True)   # {name: PipelineStatus}
path = dispatcher.render(shot_payload, output_path)

# 音频
from pipelines.audio_pipeline import generate_tts, generate_music
generate_tts(text, output_path, backend="chattts")  # fallback 自动
generate_music(prompt, output_path, duration=30, mood="cinematic")  # ACE-Step first

# 全流水线
from core.orchestrator import run_pipeline_generator
for pct, msg in run_pipeline_generator(
    prompt="穿越古代的现代女医生复仇",
    model="qwen3:8b",
    genre="古装悬疑",
    episodes=3,
): print(f"{pct:.0f}% {msg}")
```

---

*真相源由 Claude Code 于 2026-05-02 根据代码库 + 模型扫描生成。*
*下次更新请先检查: pipeline_config.json active_pipeline、capability_audit.json、模型文件实际大小。*
