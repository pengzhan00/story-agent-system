# 模型清单 — Wan2.2 + ACE-Step 1.5 核心栈

> 更新：2026-05-02  
> 基座路径：`~/myworkspace/ComfyUI_models/`  
> HF 镜像（国内）：`export HF_ENDPOINT=https://hf-mirror.com`

---

## 一、视频生成 — Wan2.2 生态

### 已有 ✅

| 文件 | 大小 | 相对路径 |
|------|------|---------|
| `Wan2.2-TI2V-5B-Q4_K_M.gguf` | 3.2 GB | `unet/` |
| `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | 6.3 GB | `text_encoders/` （symlink → ModelScope 缓存）|
| `wan2.2_umt5/models_t5_umt5-xxl-enc-bf16.pth` | 11 GB | `text_encoders/wan2.2_umt5/` |
| `Wan2.2_VAE.safetensors` | 1.3 GB | `vae/` （symlink → ModelScope 缓存）|

> ⚠️ `text_encoders/` 有两份 UMT5（fp8 6.3GB + bf16 11GB），二选一即可。  
> workflow JSON 和 pipeline_config 均使用 fp8 版本，bf16 可安全删除节省 11 GB。

### 待下载 ❌

#### Wan2.2 T2V-A14B GGUF（文生视频，无需参考图）

选 **HighNoise-Q4_K_M**（短剧首选，画面动感，配合 InstantID 保一致性）

```bash
export HF_ENDPOINT=https://hf-mirror.com

# bullerwins repo：文件名扁平，ComfyUI 友好
hf download bullerwins/Wan2.2-T2V-A14B-GGUF \
  wan2.2_t2v_high_noise_14B_Q4_K_M.gguf \
  --local-dir ~/myworkspace/ComfyUI_models/unet/
```

| 仓库 | 文件 | 约 |
|------|------|----|
| `bullerwins/Wan2.2-T2V-A14B-GGUF` | `wan2.2_t2v_high_noise_14B_Q4_K_M.gguf` | ~8.5 GB |

HF 页面：<https://huggingface.co/bullerwins/Wan2.2-T2V-A14B-GGUF>

---

#### Wan2.2 VACE-Fun-A14B GGUF（视频生视频 / 局部重绘）

```bash
export HF_ENDPOINT=https://hf-mirror.com

hf download QuantStack/Wan2.2-VACE-Fun-A14B-GGUF \
  "HighNoise/Wan2.2-VACE-Fun-A14B-high-noise-Q4_K_M.gguf" \
  --local-dir ~/myworkspace/ComfyUI_models/unet/
```

> 下载后路径：`unet/HighNoise/Wan2.2-VACE-Fun-A14B-high-noise-Q4_K_M.gguf`  
> 需同步更新 `pipeline_config.json` → `wan2_vace.config.gguf_path`

| 仓库 | 文件 | 约 |
|------|------|----|
| `QuantStack/Wan2.2-VACE-Fun-A14B-GGUF` | `HighNoise/Wan2.2-VACE-Fun-A14B-high-noise-Q4_K_M.gguf` | ~8.5 GB |

HF 页面：<https://huggingface.co/QuantStack/Wan2.2-VACE-Fun-A14B-GGUF>

---

## 二、音乐生成 — ACE-Step 1.5 生态

### 已有 ✅（全部就绪）

| 文件 | 大小 | 相对路径 |
|------|------|---------|
| `acestep_v1.5_xl_sft_bf16.safetensors` | 9.3 GB | `diffusion_models/` — 最高质量主力 |
| `acestep_v1.5_xl_turbo_bf16.safetensors` | 9.3 GB | `diffusion_models/` — 快速回退 |
| `qwen_0.6b_ace15.safetensors` | 1.1 GB | `text_encoders/` |
| `qwen_4b_ace15.safetensors` | 7.8 GB | `text_encoders/` |
| `ace_1.5_vae.safetensors` | 322 MB | `vae/` |

---

## 三、角色一致性 — InstantID（全部就绪 ✅）

| 文件 | 大小 | 相对路径 |
|------|------|---------|
| `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` | 3.7 GB | `clip_vision/` |
| `InstantID-ControlNet.safetensors` | 2.3 GB | `controlnet/` |
| `ip-adapter.bin` | 1.6 GB | `instantid/` |
| `antelopev2/` (全套 ONNX) | ~410 MB | `insightface/models/antelopev2/` |

---

## 四、语言模型 — Ollama（全部就绪 ✅）

| 模型 | 大小 | 用途 |
|------|------|------|
| `deepseek-r1:70b` | 42 GB | 创作阶段（导演 / 编剧）— **已有，尚未激活** |
| `qwen3:8b` | ~5 GB | 快速阶段（角色 / 场景描述 / 音乐词）|

> ⚠️ `ollama_client.py` 中 `STAGE_MODEL_DEFAULTS` 的 `director` / `writer` 仍指向 `qwen3:8b`，需改为 `deepseek-r1:70b`。

---

## 五、语音合成 — TTS（无需模型文件）

| 工具 | 状态 | 安装 |
|------|------|------|
| ChatTTS | ✅ 已安装 | `~/chattts/venv/` |
| edge-tts | ✅ 已在 requirements.txt | `pip install edge-tts` |

---

## 六、可选清理（省 11 GB）

```bash
# UMT5 bf16（workflow 用 fp8，bf16 冗余）
rm -rf ~/myworkspace/ComfyUI_models/text_encoders/wan2.2_umt5/
```

---

## 七、下载命令汇总

```bash
export HF_ENDPOINT=https://hf-mirror.com

# T2V-A14B（文生视频）~8.5 GB
hf download bullerwins/Wan2.2-T2V-A14B-GGUF \
  wan2.2_t2v_high_noise_14B_Q4_K_M.gguf \
  --local-dir ~/myworkspace/ComfyUI_models/unet/

# VACE-Fun-A14B（视频生视频）~8.5 GB
hf download QuantStack/Wan2.2-VACE-Fun-A14B-GGUF \
  "HighNoise/Wan2.2-VACE-Fun-A14B-high-noise-Q4_K_M.gguf" \
  --local-dir ~/myworkspace/ComfyUI_models/unet/
```

> `huggingface-cli` 已弃用，统一用 `hf`（`/opt/homebrew/bin/hf`）。  
> Wan2.2 T2V 模型新名称为 **T2V-A14B**（非 T2V-14B）。
