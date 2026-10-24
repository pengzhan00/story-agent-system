# 后期与校验工具:ffmpeg、whisper、放大、看片

成片靠 **ffmpeg** 装配,配音和对白靠 **whisper** 反查,画面靠放大/补帧工具整理,再用本地视觉模型“看一遍”。
系统里这几块各自有位置:`compose.assemble`(装配)、`compose.deliver`(交付)、`text.asr`(听写)。

相关页面:[工具总览](../TOOLS.md) · [CosyVoice](COSYVOICE.md)(念完用 whisper 反查) · [LTX-2.5](LTX.md)

## ffmpeg(装配与交付)

| 项目 | 内容 |
|---|---|
| 要用哪个 | 系统读 `[tools.ffmpeg] binary`,指向 **ffmpeg-full**(`/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`)。PATH 上那份 ffmpeg 没有 `drawtext`/`subtitles`,烧字幕和 AIGC 水印会失败(没有 libass 时系统直接拒绝) |
| 装配 | `storyagent/media/runners/assemble_episode.py`(只用标准库):每个镜头一段中间文件(x264 `veryfast`、CRF 12),再拼视频、混音、出 `episode.mp4` 与 `episode.srt` |
| 时间轴 | 配音**先量**(ffprobe 实测时长)再排时间轴,不是按字数估;`compose/timeline.py` 是纯函数,不碰 ffmpeg |
| 混音 | 旁白/对白、音效、BGM 三轨;BGM 用 `sidechaincompress` 在说话时压低(闪避);默认语音增益 1.0、音效 0.5 |
| 响度 | `loudnorm`:积分响度 **-16 LUFS**、真峰值 **-1.5 dBTP**、响度范围 **11 LU**(`I=-16:TP=-1.5:LRA=11`)。纯静音不做 loudnorm(会得 NaN 并毁掉 aac) |
| 交付档位 | `delivery.toml`:竖屏 720×1280、24 fps、x264 CRF 18、yuv420p、aac 192k、48 kHz。另有 1080×1920 / 30 fps、横屏 1280×720 等档位 |
| 质检(QC) | 文件 ≥4096 字节、时长 ≥1 秒、分辨率 ≥320×180、黑帧占比 ≤0.85、冻结 ≤1.5 秒、必须有音轨、平均音量 ≥-45 dB;另有闪烁/横纹两项“建议性”检查(只报告,不拦截) |

常用命令:

```bash
# 看一个文件的尺寸、帧数、帧率、编码
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,nb_frames,r_frame_rate -of csv=p=0 x.mp4

# 量亮度(YAVG,0–255):画面是否发暗
ffmpeg -hide_banner -ss <中点秒> -i x.mp4 -frames:v 1 -vf signalstats,metadata=print:key=lavfi.signalstats.YAVG -f null -

# 判静音(mean 在 -91 dB 附近 = 纯静音;文件大小不是有声证据)
ffmpeg -hide_banner -i a.mp3 -af volumedetect -f null - 2>&1 | grep -E "mean_volume|max_volume"

# 响度测量(只量不改)
ffmpeg -hide_banner -i x.mp4 -af ebur128 -f null - 2>&1 | tail -12

# 烧字幕(要 ffmpeg-full 的 libass)
/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg -i in.mp4 -vf "subtitles=episode.srt" -c:a copy out.mp4
```

提醒(踩过的):
- 长任务一律 `caffeinate -i`,睡眠会冻死批次。
- 临时清单不要放 `/tmp`(本机的 concat 列表在那里打不开);成品不要放 scratch 目录(24 小时清)。
- 中文名打包用 Python `zipfile`,macOS 自带的归档会乱码。

## whisper.cpp(听写、字幕、对白反查)

| 项目 | 内容 |
|---|---|
| 程序 | `whisper-cli`(`[tools.whisper] binary`) |
| 模型 | `~/myworkspace/models/whisper/ggml-large-v3-turbo.bin`(1.55 GB) |
| 系统里 | 模型 `text.asr` → `whisper-large-v3-turbo`;参数 `language`(`zh`、`en`、`auto`),产物是 `transcript.txt` 和带时间戳的 `transcript.srt` |
| 三个用途 | 给克隆参考录音自动转写参考文本;出字幕;反查生成的对白“到底念没念对”(LTX 的中文对白就是这样验证的) |

```bash
whisper-cli -m ~/myworkspace/models/whisper/ggml-large-v3-turbo.bin -l zh -f a.wav -nt          # 只要文字
whisper-cli -m ~/myworkspace/models/whisper/ggml-large-v3-turbo.bin -l zh -f a.wav -osrt -of out # 出 SRT
```

输入要 16 kHz 单声道 wav(系统的适配器会先转);专有名词、同音字容易听错,反查是“大意对不对”,不是逐字验收。

## 放大、补帧、看片(你的包装脚本)

| 命令 | 作用 |
|---|---|
| `up1080 in.mp4 [in2.mp4 …]` | 统一放大/缩到 1920×1080,输出 `<name>_1080p.mp4`。选项:`--out DIR`、`--denoise`(轻降噪)、`--sharp N`(锐化强度,默认 0.6)、`--crf N`(默认 16);给目录就处理目录下所有 mp4 |
| `h3batch shots.json [--out DIR] [--chain] [--dry]` | 按镜头清单批量跑 h3.c;`--chain` 把上一镜末帧当下一镜首帧(跨镜一致性)。清单里写 `model_dir`、`h3`、尺寸、`frames`、`steps`、`layers`、`reuse` 和 `shots[]` |
| `vlmshot 片子.mp4 "问题"` | 抽帧后交给本地 gemma4:31b“看”,默认输出到 `~/Desktop/AI分析/`(`报告.md` + 抽出的帧)。`--audio` 附带音轨转写;图片也行 |
| `ltxshot` | 见 [LTX-2.5](LTX.md) |

RIFE 补帧:`~/myworkspace/RIFE_repo` 有上游仓库,需要把 12 fps 补到 24 fps 时用;系统里还没有它的适配器。
`vlmshot` 里 MiniCPM-o 已移除,只用 gemma4:31b。

## 其他本地工具

| 工具 | 说明 |
|---|---|
| `edge-tts` | 微软在线 TTS(需联网,不属于“全本地”),14 个中文音色,快,做旁白草稿 |
| macOS `say` | 系统自带,19 个中文嗓音 |
| ChatTTS | 本地口语化,有呼吸感,不可克隆,中文有时不稳 |
| InsightFace ArcFace | 人脸比对(`~/venvs/faceid`):同一人 0.7–0.9,不同人 0.0–0.1,用来量角色一致性 |
| Laplacian 方差 | 清晰度,只能做相对比较 |

## 你的实测笔记(Obsidian)

- [[音视频工具矩阵]]
- [[影音技术基础速查（编码·色彩·音频·图像）]]
- [[音视频资产全景_MEDIA]]

(这些链接在知识库里指向同步进来的笔记;没同步笔记时只显示名字。)
