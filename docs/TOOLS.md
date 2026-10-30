# 管线工具总览:每个工具是什么、命令在哪

系统把一集短剧拆成若干环节,每个环节由一个本地工具完成。这一页是地图:先看“哪个环节用哪个工具”,再点进工具页看命令、参数含义和排障。
每页的结构一样:是什么 → 装在哪 → 常用命令 → 参数的意思 → 系统里怎么用 → 常见问题。
你的 Obsidian 笔记里有更完整的实测记录,wiki 里能搜到(同步见 [知识库](WIKI.md))。

## 环节 → 工具

| 环节 | 工具 | 系统里的模型 | 页面 |
|---|---|---|---|
| 写故事、剧本、分镜、提示词 | Ollama / llama.cpp 大模型 | 各角色(`llm.toml`) | [LLM](tools/LLM.md) |
| 出图(人物肖像、场景) | FLUX.2 klein + mflux | `flux2-klein-base-4b` | [FLUX](tools/FLUX.md) |
| 改图 / 拼关键帧 | FLUX.2 klein 编辑(mflux 或 ComfyUI 蒸馏版) | `flux2-klein-base-4b-edit` / `flux2-klein-4b-edit-comfy` | [FLUX](tools/FLUX.md)、[ComfyUI](tools/COMFYUI.md) |
| 训角色 LoRA | mflux-train | `flux2-klein-4b-lora-trainer` | [FLUX](tools/FLUX.md) |
| 视频(音画一体,首选) | LTX-2.5(Phosphene 面板) | `ltx-2.5-av` / `ltx-2.5-video` | [LTX-2.5](tools/LTX.md)、[Phosphene](tools/PHOSPHENE.md) |
| 视频(长镜头) | MiniMax H3(h3.c 命令行) | `minimax-h3` | [h3.c](tools/H3C.md) |
| 口型对话 | LTX Lip-sync(中文口型偏弱,见 reviews/2026-10-30-a2v-ab.md) | `ltx-2.5-lipsync` | [LTX-2.5](tools/LTX.md) |
| 配音 / 声音克隆 | CosyVoice 3 / 2 | `cosyvoice3-0.5b` / `cosyvoice2-0.5b` | [CosyVoice](tools/COSYVOICE.md) |
| 换声 / 歌声换音色 | Seed-VC + Demucs | `seed-vc-v2` | [Seed-VC](tools/SEEDVC.md) |
| 配乐 / 主题曲(带人声) | ACE-Step 1.5 独立服务 | `acestep-1.5-turbo` | [ACE-Step](tools/ACESTEP.md) |
| 配乐 / 主题曲(带人声,可选) | YuE2-3B(Phosphene 面板的音乐引擎) | `yue2-3b` | [YuE2](tools/YUE2.md)、[Phosphene](tools/PHOSPHENE.md) |
| 音效 | Stable Audio 3(ComfyUI) | `stable-audio-3-medium` | [ComfyUI](tools/COMFYUI.md) |
| 听写 / 字幕 / 对白反查 | whisper.cpp | `whisper-large-v3-turbo` | [后期](tools/POST.md) |
| 装配 / 响度 / 字幕 / 交付 / 质检 | ffmpeg(ffmpeg-full) | `ffmpeg-assemble` / `ffmpeg-deliver` | [后期](tools/POST.md) |
| 图像(只有它能跑的模型) | Draw Things(桌面应用 + API) | `drawthings` 提供者 | 下面一节 |
| 接入你自己的工具 | ComfyUI 工作流 / 命令行向导 / HTTP / 插件 | `comfy` / `cli` / `http` | [接入外部工具](CONNECTORS.md) |

## 端口与位置速查

| 服务 | 地址 | 谁来启动 | 体检 |
|---|---|---|---|
| Ollama | `127.0.0.1:11434` | 你(`ollama serve`) | `/api/tags` |
| ComfyUI(Comfy Desktop) | `127.0.0.1:8188` | 你(打开应用) | `/system_stats` |
| ACE-Step | `127.0.0.1:8001` | 启动页 / `ops launch acestep` | `/v1/models` |
| Phosphene 面板(Pinokio) | `127.0.0.1:8198` | 你(`open -a Pinokio`) | `/status` |
| Draw Things API | `127.0.0.1:7860` | 你(打开应用并在设置里开 API Server) | `/` |
| llama.cpp | `127.0.0.1:8080` | 默认关闭 | `/health` |
| h3.c、CosyVoice、Seed-VC、whisper、mflux、ffmpeg | 没有服务,每个任务起一个子进程 | 系统在任务里调用 | 工具路径检查 |

系统**不会替你启动或关闭不属于它的进程**;启动页只会运行 `services.toml` 里 `[launch.*]` 列出、并且你点了的那几个。所有路径、端口都在 `services.toml`,代码里没有写死。
体检:`storyagent media healthcheck`。

## 同时只能跑一个重任务

视频(LTX、h3)、LoRA 训练是“重任务”,内存各占 50–80 GB,由 broker 串行;配音、出歌、音效、装配是轻任务,可以和重任务错开。
重任务期间不要再开别的大模型(包括 Ollama 里驻留的 31B)。

## Draw Things

macOS 应用,有自己的 `.ckpt` 格式模型,开了 **API Server**(设置 → 高级)后系统才能调(A1111 兼容的 `/sdapi/v1` 接口,`127.0.0.1:7860`)。
它能跑一些别处跑不了的模型,图和视频都能做、也能本地训 LoRA,但很慢(同一镜头 H3 在它里面 18.5 分钟,比 h3.c 慢很多)、难批量。
你的包装命令:`drawthings [open|quit|status|models|dir|logs]`。模型放在 `~/myworkspace/DrawThings_models`。
系统适配器的参数:`width height steps guidance sampler shift strength clip_skip loras`,另有 `request` 表可以原样塞入任何适配器不认识的键。
模型文件名写在模型记录的 `api_model`,不写就用应用里当前选中的模型。

## 选型一页纸(来自你的实测笔记)

```
要出片
├─ 单镜 ≤5 秒且要声音(对白/音效) → LTX-2.5(1536×832 + high + pipersr 1080p)
│   ├─ 要“同一个人”跨镜 → 先训 rank 32 的角色 LoRA,再 high
│   └─ 要逐字对口型 → LTX Lip-sync(图 + 音轨)
├─ 单镜 8–15 秒、画面为主、可后配音 → h3.c(h3batch --chain)→ up1080 → 装配后配音
├─ 整片(多镜 + 旁白 + 字幕) → 分镜 → 按镜长选引擎 → 统一尺寸 → 装配
└─ 赶时间 → 云上的通义万相(会计费、要上传,不属于本地管线)
```

| 需求 | 首选 | 备选 |
|---|---|---|
| 旁白像真人说话 | CosyVoice | ChatTTS(有呼吸感,不可克隆)、edge-tts(快,联网) |
| 带人声的歌 | ACE-Step 独立服务 | — |
| 歌声换成自己的音色 | Seed-VC V2(V1 用于唱歌)+ Demucs | — |
| 字幕、听写校验 | whisper + ffmpeg | — |
| 节点化实验 | ComfyUI | — |

## 三条铁律

1. 长任务一律 `caffeinate -i`(睡眠会冻死批次)。
2. 成品落到项目目录,不放 scratch(24 小时清)。
3. 出片后核实际分辨率(LTX 面板会静默压小)。

## 总表笔记(Obsidian)

- [[本机模型能力矩阵]] · [[音视频工具矩阵]] · [[音视频资产全景_MEDIA]] · [[AI生成_模型与管线总表]] · [[本地 AI 工具链清单]] · [[本地大模型部署与能力分布表]]
