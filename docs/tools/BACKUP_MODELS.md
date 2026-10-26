# 备用模型管线清单

> 本机模型清单；可用性应同时核实权重、运行时和真实任务。最新接入状态见 [可选能力收敛](../examples/optional-capabilities/README.md)。切一个模型 = 改 `$STORY_AGENT_HOME/config/models.toml` 里 `[defaults]` 的一个 ref(热读,不用重启)。
> 权重实际在 `/Users/pengzhan/models/drawthings/models/`(共约 120G),不是文档旧路径 `~/myworkspace/DrawThings_models`。

## 图片(Draw Things 提供者,`provider = "drawthings"`)

| ref(建议名) | ckpt 文件 | 大小 | 家族 | 用途 |
|---|---|---|---|---|
| `flux2-klein-4b-drawthings` | `flux_2_klein_4b_q8p.ckpt` | 4.0G | FLUX.2 klein | **当前默认**,通用/干净 |
| `lustify-krea2-drawthings` | `lustifynsfwcheckpoint_v10krea2_fp8_f16.ckpt` | 25.7G | Lustify(Krea2) | 受限向(已登记,`rating=restricted`) |
| `biglust-sdxl-drawthings` | `biglust_v16_f16.ckpt` | 5.1G | SDXL | 写真/成人 |
| `illustrious-sdxl-drawthings` | `illustriousxl_v01_f16.ckpt` | — | SDXL | 2026-10-27 底模缺失，未启用 |
| `juggernaut-sdxl-drawthings` | `juggernautxl_ragnarok_f16.ckpt` | 5.1G | SDXL | 写实照片 |
| `pony-sdxl-drawthings` | `ponydiffusionv6xl_v6startwiththisone_f16.ckpt` | — | SDXL | 2026-10-27 底模缺失，未启用 |
| `noobai-sdxl-drawthings` | `noobaixlnaixl_vpred10version_f16.ckpt` | 5.1G | SDXL | 二次元(NAI) |

共用件(已就位):`sdxl_vae_v1.0_f16.ckpt`、`open_clip_vit_bigg14_f16.ckpt`、各模型自己的 `*_clip_vit_l14_f16.ckpt` / `*_open_clip_vit_bigg14_f16.ckpt`、`qwen_image_vae_f16.ckpt`(Lustify 用)、`qwen_3_vl_4b_q8p.ckpt`(Lustify 文本编码)。

## 视频(Phosphene 面板,`provider = "phosphene"`)

| ref | 引擎/权重 | 状态 | 用途 |
|---|---|---|---|
| `ltx-2.5-av` | ltx-2.5-mlx-q4/q8 | ✅ 已接 | 音画一体出片(首选) |
| `ltx-2.5-video` | 同上 | ✅ 已接 | 无声视频 |
| `ltx-2.5-lipsync` | 同上 | ✅ 已接 | 口型对话(≈5s) |
| `longcat-avatar-1.5-q4` | LongCat | ✅ 已接 | 数字人说话头 |
| `minimax-h3` | h3.c 独立 CLI / MiniMax-H3 目录 | 已有专用适配器，本轮未重新生成 | 见 [h3.c](H3C.md)，不能与 DT 版权重互换 |

## LTX-2.3 —— Draw Things 无声视频备用

- 本机已登记 `ltx-2.3-drawthings`（`video.generate`），在工坊显式选择，不进入生成 defaults。
- DT HTTP 返回 PNG 帧数组，系统用 ffmpeg 按请求帧率编码 MP4；这条接口不返回音轨。
- 蒸馏模型用 8 步、guidance 1.0、24 fps；帧数为 8n+1。音画一体主线仍为 Phosphene LTX-2.5。
- Phosphene 的 2.3 训练用途与 DT 的 2.3 生成入口分属不同提供者，不能相互推断。
- 完整配置和验证边界见 [可选能力收敛](../examples/optional-capabilities/README.md)。

## LoRA(备件,已下载)

| 文件 | 用途 |
|---|---|
| `hand_5.5_sdxl_lora_f16.ckpt` | 手部修正 |
| `detail_enhancer_sdxl_lora_f16.ckpt` / `add_detail_xl_lora_f16.ckpt` / `smooth_booster_v5_lora_f16.ckpt` | 细节/平滑 |
| `illustriousxlv01_stabilizer_v1.198_lora_f16.ckpt` | 插画稳定 |
| `expressive_h_000001_lora_f16.ckpt` | 表情 |
| `zy_amateurstyle_v2_lora_f16.ckpt` / `zy_amateur_pov_h3_lora_f16.ckpt` / `nsfwpovallinonelorasdxl_000009_lora_f16.ckpt` | 风格/视角 |

## 下载状态复核（2026-10-27）

- DT 的 `minimax_h3_fl2va_q8p.ckpt` 和 `pony__people_s_works_v8_illusv_2.0_stable_lora_f16.ckpt` 正式文件现已存在；旧 `.partial` 记录已过时。
- 正式文件存在不等于 DT H3 已通过 Story Agent 实测；本轮未新增这条生成入口。
- Illustrious / Pony 底模已缺失，LoRA 不能替代底模；需要恢复后再启用。

## 物理位置汇总

| 引擎 | 权重目录 |
|---|---|
| Draw Things | `/Users/pengzhan/models/drawthings/models/`(120G) |
| Phosphene(视频/音乐) | `~/pinokio/api/phosphene/mlx_models/` |
| Demucs | `~/models/demucs/`(已从 HF 缓存迁出) |
| YuE2 | `~/pinokio/api/phosphene/mlx_models/yue2`(约 10.5–11G) |

## 切换示例

```toml
# config/models.toml —— 把 image.generate 换成像系备用
[defaults]
"image.generate" = "juggernaut-sdxl-drawthings"
```
