# Local Capability Inventory

Last audited: 2026-04-29
Host: `/Users/pengzhan`

## Runtime Apps

- ComfyUI: `/Users/pengzhan/Documents/ComfyUI`
- Ollama data: `/Users/pengzhan/.ollama`
- ChatTTS repo: `/Users/pengzhan/chattts`
- Bark repo: `/Users/pengzhan/bark`
- Audiocraft repo: `/Users/pengzhan/audiocraft`
- Pinokio data: `/Users/pengzhan/.pinokio`
- ffmpeg: `/opt/homebrew/bin/ffmpeg`

## Ollama Models

- `gemma4:31b-chat`
- `qwen3:8b`
- `qwen3-vl:latest`
- `minicpm-v:latest`
- `qwen2.5vl:7b`
- `llava:13b`
- `deepseek-r1:70b`
- `gemma4:31b`
- `gemma4:latest`

## ComfyUI Custom Nodes

- `ComfyUI-AnimateDiff-Evolved`
- `ComfyUI-VideoHelperSuite`
- `ComfyUI-GGUF`
- `comfyui_controlnet_aux`
- `comfyui_instantid`
- `ComfyUI_IPAdapter_plus`
- `ComfyUI-Manager`
- `comfyui-model-manager`
- `comfyui-model-downloader`
- `comfyui-vid2vid`
- `was-node-suite-comfyui`

## Installed ComfyUI Models

### Checkpoints

- `sd_xl_base_1.0.safetensors`
- `animagine-xl-3.1/`
- `flux_2_klein_4B/`
- `flux_2_klein_9B/`
- `models/unet/Wan2.2-TI2V-5B-Q4_K_M.gguf`

Current convention:

- AnimateDiff / SDXL / Flux checkpoints live under `models/checkpoints/`
- Wan GGUF lives under `models/unet/`
- Wan UMT5 encoder lives under `models/text_encoders/wan2.2_umt5/`

### ControlNet

- `InstantID-ControlNet.safetensors`
- `diffusers_xl_canny_mid.safetensors`
- `diffusers_xl_depth_mid.safetensors`
- `kohya_controllllite_xl_canny_anime.safetensors`
- `kohya_controllllite_xl_depth.safetensors`
- `kohya_controllllite_xl_openpose_anime_v2.safetensors`

### LoRA

- `pastel-anime-xl.safetensors`

Notes:

- `anime.safetensors` is not currently present in `models/loras/`
- Current production candidate does not depend on that LoRA

### VAE

- `sdxl_vae.safetensors`
- `sdxl_vae.1.safetensors`
- `Wan2.1_VAE.pth`
- `Wan2.2_VAE.safetensors` (file size abnormal, should be revalidated)

Missing / unresolved:

- `flux2/ae.safetensors` was not found on disk during audit

## Verified ComfyUI Node Availability

Confirmed via `GET /object_info`:

- `CheckpointLoaderSimple`
- `KSampler`
- `CLIPTextEncode`
- `LoraLoader`
- `ControlNetLoader`
- `ControlNetApply`
- `ADE_UseEvolvedSampling`
- `VHS_VideoCombine`
- `UnetLoaderGGUF`
- `CLIPLoaderGGUF`

Missing from current live node registry during audit:

- `FluxLoader`
- `WanVideoSampler`

## Production Readiness Summary

- Pipeline `animatediff_animagine`: available
- Pipeline `animatediff_sdxl`: available
- Pipeline `A` (`Flux 2 Klein 4B + Wan 2.2`): experimental only
- Pipeline `B` (`Flux 2 Klein 9B + Wan 2.2`): experimental only

## Blocking Gaps

- `flux_wan_workflow.json` is still a structural template, not a production workflow
- Flux VAE `flux2/ae.safetensors` is still missing
- Legacy code paths still assume some old model directories and labels

## Operational Recommendation

- Prefer `animatediff_animagine` as the default render path on this machine
- Use `animatediff_sdxl` as the stable fallback
- Keep `wan2_ti2v` and `flux_wan2_twostage` behind explicit operator choice until they are validated with real jobs
