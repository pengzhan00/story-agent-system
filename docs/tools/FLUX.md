# FLUX.2 klein 与 mflux:出图、改图、训 LoRA

本机的图像生成走 **mflux**(FLUX 系模型的 MLX 命令行实现),主力模型是 **FLUX.2 klein 4B**(Apache-2.0,可商用)。
系统里有三类用法:`flux2-klein-base-4b`(文生图)、`flux2-klein-base-4b-edit`(用参考图改图,30 步)、
`flux2-klein-4b-edit-comfy`(蒸馏版走 ComfyUI,4 步,拼关键帧用),再加上 LoRA 训练 `flux2-klein-4b-lora-trainer`。
适配器是 `storyagent/media/adapters/mflux.py`:**命令、权重目录、基础模型名都写在模型记录里**,换一个 mflux 模型家族只改配置,不改代码。

相关页面:[工具总览](../TOOLS.md) · [ComfyUI](COMFYUI.md)(蒸馏版编辑走它) · [LTX-2.5](LTX.md)(拿关键帧当首帧)

## 装在哪

| 项目 | 内容 |
|---|---|
| 命令 | `~/.local/bin/mflux-*`(`services.toml` 的 `[tools.mflux] bin_dir`) |
| 权重 | `~/models/flux2-klein-base-4B`(本地目录;**系统运行时从不下载权重**,目录不在就直接报缺模型) |
| 训练 | `~/.local/bin/mflux-train`,工作目录 `~/myworkspace/flux2-lora`,基座 `~/models/flux2-klein-base-4B` |
| 许可 | klein **4B** Apache-2.0 可商用;klein **9B** 非商用;**LoRA 沿用基础模型的许可** |

## 常用命令

```bash
# 文生图(FLUX.2 没有负面提示词,想要什么就写什么)
mflux-generate-flux2 --model flux2-klein-base-4b --prompt "…" \
  --width 720 --height 1280 --steps 30 --guidance 4.0 --seed 7 --output out.png

# 改图 / 用参考图拼图(klein 编辑)
mflux-generate-flux2-edit --model flux2-klein-base-4b --image-paths a.png b.png \
  --prompt "…" --steps 30 --guidance 4.0 --output edit.png

# 看模型和命令清单
mflux-info
mflux-capabilities
```

`mflux-generate-flux2` 的选项:

| 选项 | 含义 |
|---|---|
| `--model, -m` | 内置模型名(如 `flux2-klein-4b`、`flux2-klein-base-4b`、`flux2-klein-9b`、`qwen-image`、`fibo`、`z-image-turbo` …)、HuggingFace 仓库,或本地路径 |
| `--base-model` | 用第三方权重/本地路径时,指明它基于哪个内置模型 |
| `--quantize, -q` | 量化位数 3/4/5/6/8,省内存 |
| `--prompt` / `--prompt-file`、`--negative-prompt` | 提示词(klein 忽略负面词) |
| `--seed`(可多个)、`--auto-seeds N` | 种子;一次出多张 |
| `--width` `--height` | 必须是 16 的倍数 |
| `--steps`、`--guidance`、`--scheduler` | 步数、引导强度、调度器 |
| `--lora PATH [SCALE]`、`--lora-style` | 加载 LoRA 与强度;`--bake-lora` 把 LoRA 烘进权重 |
| `--image PATH [STRENGTH]`、`--image-strength` | 用一张图做起点(图生图) |
| `--low-ram`、`--vae-tiling`、`--vae-tile-size`、`--mlx-cache-limit-gb` | 省内存:低内存模式、分块解码 VAE、限制 MLX 缓存 |
| `--compute-precision float16` | 注意力和前馈用 fp16;M4/M5 上收益很小 |
| `--make-conf` / `--config-from-conf` | 导出/读取参数文件,复现一张图 |
| `--output` | 输出文件名;`--battery-percentage-stop-limit` 电池供电时低于该电量就停 |

## 参数经验(同一种子的实测)

- 20 步、512×768 的脸会变形、武器缠在一起;**30–50 步、guidance 3.5–5** 脸和手才干净。引导强度是除步数以外唯一的质量旋钮。
- 系统默认竖屏 9:16:720×1280,30 步,`guidance 4.0`;草稿 576×1024 / 24 步;最终 864×1536 / 50 步。横屏项目会覆盖尺寸。
- 编辑模型(30 步,720×1280)实测约 **575 秒**;蒸馏版走 ComfyUI 4 步约 **56 秒**,所以快速出关键帧用后者(项目里在管线面板把 `image.edit` 换成 `flux2-klein-4b-edit-comfy`;系统默认的 `image.edit` 仍是 30 步的 base 编辑模型,见 [ComfyUI](COMFYUI.md))。
- `ref_megapixels` 越小越快、参考图保留的细节越少。

## 在系统里怎么用

- 适配器参数:`width height steps guidance quantize scheduler low_ram image_strength loras=[{path, scale}]`;没写的交给工具自己的默认值。
- 输入:`prompt`、`refs`(文生图时第一张当起始图,配合 `image_strength`;编辑时全部都是要改的图)。
- 关键帧默认流程:人物肖像 + 场景参考图 → `image.edit` 拼成首帧 → 交给视频模型。提示词写法见 `storyagent/agents/specs/shot.visual.toml`(中景以上、动作进行中、不写极端脸部特写等)。

## 训练 LoRA(角色一致性)

`mflux-train` 用一个 JSON 配置训练,系统把它模板化在 `models.toml` 的 `[models."flux2-klein-4b-lora-trainer".train_template]` 里,
常调的几个旋钮出现在工坊里:

| 参数 | 默认 | 含义 |
|---|---|---|
| `epochs` | 60 | 训练轮数 |
| `learning_rate` | 0.0001 | AdamW 学习率 |
| `max_resolution` | 1024 | 训练图最长边 |
| `save_frequency` | 5 | 每几轮存一次检查点 |
| `min_images` | 4 | 至少几张图才开训 |
| `seed` | 42 | |

LoRA 层目标是 `transformer_blocks.{block}.attn.to_q/to_k/to_v/…`,块 0–5,`rank 16`。数据集约定:`NN.png` + 同名 `NN.txt`(触发词加描述)。
训练是“重任务”,会和视频互斥;一次要几个小时(你的记录:33 张真人素材、1980 步、512px / 8bit,约 4.4 秒/步)。
你已经训过 v1 角色 LoRA(rank 16),实测“不够像”。要跨镜头保持同一个人,更可靠的路线是用肖像参考图做编辑拼图(上面的流程),或者在 LTX 里训 rank 32 的角色 LoRA(见 [LTX-2.5](LTX.md))。

## 常见问题

| 现象 | 原因和办法 |
|---|---|
| 报缺模型 | `weights` 目录不存在;系统不会自动下载,下载权重前先确认来源和大小 |
| 脸或手变形 | 步数太低,用 30 步以上,guidance 3.5–5 |
| 想要“不要某某” | FLUX.2 没有负面词,正向描述想要的画面;系统会丢掉负面词并记 `negative_prompt_ignored` 事件 |
| 内存紧 | `--low-ram`、`--vae-tiling` 或 `--quantize 8/6/4` |
