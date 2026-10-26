# 备用模型管线清单

> 本机已下载、可随时切换启用的模型汇总。切一个模型 = 改 `$STORY_AGENT_HOME/config/models.toml` 里 `[defaults]` 的一个 ref(热读,不用重启)。
> 权重实际在 `/Users/pengzhan/models/drawthings/models/`(共约 120G),不是文档旧路径 `~/myworkspace/DrawThings_models`。

## 图片(Draw Things 提供者,`provider = "drawthings"`)

| ref(建议名) | ckpt 文件 | 大小 | 家族 | 用途 |
|---|---|---|---|---|
| `flux2-klein-4b-drawthings` | `flux_2_klein_4b_q8p.ckpt` | 4.0G | FLUX.2 klein | **当前默认**,通用/干净 |
| `lustify-krea2-drawthings` | `lustifynsfwcheckpoint_v10krea2_fp8_f16.ckpt` | 25.7G | Lustify(Krea2) | 受限向(已登记,`rating=restricted`) |
| `biglust-sdxl-drawthings` | `biglust_v16_f16.ckpt` | 5.1G | SDXL | 写真/成人 |
| `illustrious-sdxl-drawthings` | `illustriousxl_v01_f16.ckpt` | 5.1G | SDXL | 二次元/插画 |
| `juggernaut-sdxl-drawthings` | `juggernautxl_ragnarok_f16.ckpt` | 5.1G | SDXL | 写实照片 |
| `pony-sdxl-drawthings` | `ponydiffusionv6xl_v6startwiththisone_f16.ckpt` | 5.1G | SDXL | 二次元(pony) |
| `noobai-sdxl-drawthings` | `noobaixlnaixl_vpred10version_f16.ckpt` | 5.1G | SDXL | 二次元(NAI) |

共用件(已就位):`sdxl_vae_v1.0_f16.ckpt`、`open_clip_vit_bigg14_f16.ckpt`、各模型自己的 `*_clip_vit_l14_f16.ckpt` / `*_open_clip_vit_bigg14_f16.ckpt`、`qwen_image_vae_f16.ckpt`(Lustify 用)、`qwen_3_vl_4b_q8p.ckpt`(Lustify 文本编码)。

## 视频(Phosphene 面板,`provider = "phosphene"`)

| ref | 引擎/权重 | 状态 | 用途 |
|---|---|---|---|
| `ltx-2.5-av` | ltx-2.5-mlx-q4/q8 | ✅ 已接 | 音画一体出片(首选) |
| `ltx-2.5-video` | 同上 | ✅ 已接 | 无声视频 |
| `ltx-2.5-lipsync` | 同上 | ✅ 已接 | 口型对话(≈5s) |
| `longcat-avatar-1.5-q4` | LongCat | ✅ 已接 | 数字人说话头 |
| `minimax-h3` | `minimax_h3_fl2va_q8p.ckpt` | ⚠️ **`.partial` 未下完**(35G) | 8–15s 长镜画面 |

## LTX-2.3 —— 只做训练引擎,不进生成 defaults

- 权重 `ltx_2.3_22b_distilled_1.1_i8x.ckpt`(25.5G)+ `ltx_2.3_audio_video_vae_f16.ckpt` + 两个 upscaler(`spatial_upscaler_x1.5` / `x2_1.1`),**都已就位**。
- 但 Phosphene 面板明说:LTX-2.3 只用于**训练角色 LoRA**(trainer 跑 2.3),出片一律走 2.5。所以它不登记成生成模型。

## LoRA(备件,已下载)

| 文件 | 用途 |
|---|---|
| `hand_5.5_sdxl_lora_f16.ckpt` | 手部修正 |
| `detail_enhancer_sdxl_lora_f16.ckpt` / `add_detail_xl_lora_f16.ckpt` / `smooth_booster_v5_lora_f16.ckpt` | 细节/平滑 |
| `illustriousxlv01_stabilizer_v1.198_lora_f16.ckpt` | 插画稳定 |
| `expressive_h_000001_lora_f16.ckpt` | 表情 |
| `zy_amateurstyle_v2_lora_f16.ckpt` / `zy_amateur_pov_h3_lora_f16.ckpt` / `nsfwpovallinonelorasdxl_000009_lora_f16.ckpt` | 风格/视角 |

## 未完成下载(需补)

- `minimax_h3_fl2va_q8p.ckpt.partial`(35G,H3)
- `pony__people_s_works_v8_illusv_2.0_stable_lora_f16.ckpt.partial`(pony 角色 LoRA)

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
