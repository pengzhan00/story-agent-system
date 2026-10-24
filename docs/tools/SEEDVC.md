# Seed-VC 与 Demucs:换声和歌声换音色

Seed-VC 把一段录音的**内容、节奏、情绪保持不变,只把说话人换成参考音色**。系统里的模型是 `seed-vc-v2`(`audio.convert`),
适配器 `storyagent/media/adapters/seedvc.py`,在 Seed-VC 自己的虚拟环境里运行。要换歌声音色,先用 Demucs 把人声拆出来,
只转换人声,再把伴奏混回去(`separate_vocals = true` 一步完成)。

相关页面:[工具总览](../TOOLS.md) · [CosyVoice](COSYVOICE.md) · [ACE-Step](ACESTEP.md)(出歌后再换成自己的声音)

## 装在哪

| 项目 | 内容 |
|---|---|
| 代码 | `~/seed-vc`(GPL-3.0 代码,作为独立进程运行;上游 2025-11-21 已归档只读,**没有 v3**) |
| 环境 | `~/venvs/seedvc`(uv 建的,没有 pip,先 `python -m ensurepip --upgrade`;Demucs 已并入这个环境,需要 `sphn`) |
| V2 结构 | AR(`ar_base.pth` 342M)+ CFM(`cfm_small.pth` 337M)+ BigVGAN 44k 声码器 |
| 额外权重 | `facebook/hubert-large-ll60k`(1.26 GB)、`nvidia/bigvgan_v2_22khz_80band_256x`(V2 配置写死) |

## 命令

```bash
cd ~/seed-vc

# 说话换声(V2):热态 3.7 秒音频约 16 秒出结果
HF_HUB_DISABLE_XET=1 ~/venvs/seedvc/bin/python inference_v2.py \
  --source <源录音.wav> --target <音色参考.wav> --output <输出目录> --diffusion-steps 13

# 歌声换声(V1 + 音高条件,44.1 kHz)
~/venvs/seedvc/bin/python3 inference.py --source <干声.wav> --target <参考音色> \
  --output <输出目录> --diffusion-steps 30 --f0-condition True --fp16 True

# 先拆人声(Demucs):5.9 秒音频约 0.6 秒
~/venvs/seedvc/bin/demucs --two-stems=vocals -n htdemucs -o <输出目录> <文件>
```

## 参数的意思

| 参数 | 含义 |
|---|---|
| `diffusion_steps` | 扩散步数。**几乎不影响速度**(瓶颈在声码器),V2 官方标定 13 步 |
| `length_adjust` | 语速/长度调整 |
| `intelligibility_cfg_rate`、`similarity_cfg_rate` | V2:清晰度 / 像参考音色的程度 |
| `top_p`、`temperature`、`repetition_penalty` | V2 的采样参数 |
| `convert_style` | V2:连口音/风格一起转换(才会走 AR 部分;普通换声不走 AR) |
| `inference_cfg_rate`、`f0_condition`、`auto_f0_adjust`、`semi_tone_shift`、`fp16` | V1(唱歌):音高条件、自动音高调整、半音偏移(-24..24,调音域)、半精度 |
| `separate_vocals` | 歌曲模式:Demucs 拆人声 → 只转人声 → 伴奏混回(`accompaniment_gain`、`demucs_model`、`demucs_device`) |
| `ref_min_sec` / `ref_max_sec` | 参考音色长度窗口,最合适 10–30 秒 |

输入:`audio`(源录音)和 `voice_ref`(`{"audio","consent"}`,要成为谁)。V2 **没有音高参数**:跨性别换声要“变调 + 换音色”两步,会有卡通化。

## 实测数字

| 项 | 事实 |
|---|---|
| 冷启动 | 首次约 **234 秒**(下载 hubert/bigvgan、MPS 编译着色器),只发生一次 |
| 热态 | 3.69 秒音频:30 步 16.4 秒 / 13 步 14.6 秒 / 8 步 14.8 秒(约 4.4 倍实时) |
| 精度优化 | 声码器改 bf16 在 MPS 上不支持;改 fp16 能跑但不提速。**结论:别改** |
| 音色参考 | 240 秒的参考不行,**切成约 15 秒**才正常;一个参考只放一个人 |

## 在 macOS / MPS 上必须知道的坑

| 现象 | 原因和办法 |
|---|---|
| 进程零 CPU 一直挂着 | HuggingFace 的 Xet 存储不通。设 `HF_HUB_DISABLE_XET=1`;系统默认离线(`[tools.seedvc] offline = true`),只用本地检查点 |
| `LocalEntryNotFoundError` | hub 客户端 HEAD 超时(不是被墙)。`HF_HUB_ETAG_TIMEOUT=60 HF_HUB_DOWNLOAD_TIMEOUT=600` |
| `hf-mirror` 失败 | 对未缓存文件 308 跳回原站,被判成“非 HF 域名” |
| `TypeError: Cannot convert a MPS Tensor to float64` | V1 的 F0 上 MPS 前要转 float32(源码补丁) |
| `TorchCodec is required` | torchaudio 2.14 新依赖,arm64 无轮子;改用 `soundfile` 保存(源码补丁) |
| `No module named 'sphn'` | Demucs 4.1 起的硬依赖,`pip install sphn` |
| “v2 挂了” | 常是两个独立故障叠加:Demucs 环境被当重复删了 + Xet 不通 |

## 在系统里怎么用

- 默认引擎 V2(说话);唱歌换 `engine = "v1"` 并开 `f0_condition`。
- 歌曲:`separate_vocals = true` 一步完成拆轨、转换、混回。
- 许可:代码 GPL-3.0,权重自己的条款没核过,商用前确认。合规闸门要求 `voice_ref` 带授权勾选(换成真人的声音必须有本人同意)。
- 想要更高相似度再考虑 RVC,但那要 10–30 分钟干净干声和训练,目前没装。

## 你的实测笔记(Obsidian)

- [[本地 AI 出歌实战（ACE-Step 1.5 + 换声纹）]]
- [[音视频资产全景_MEDIA]]

(这些链接在知识库里指向同步进来的笔记;没同步笔记时只显示名字。)
