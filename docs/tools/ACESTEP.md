# ACE-Step 1.5:出歌(带人声)

ACE-Step 是本机做**带人声的歌**的工具。系统里对应的模型是 `acestep-1.5-turbo`(`audio.music`),适配器是 `storyagent/media/adapters/acestep.py`,
它不碰 ComfyUI,而是调 **独立的 ACE-Step REST 服务**(端口 8001)。
实战细节在你的 Obsidian 笔记《本地 AI 出歌实战(ACE-Step 1.5 + 换声纹)》和《ACE-Step XL SFT 人声经验总结》;这一页是速查。

相关页面:[工具总览](../TOOLS.md) · [Seed-VC 换声](SEEDVC.md)(歌声换音色) · [ComfyUI](COMFYUI.md)

## 一句话结论

**要人声,走独立服务(8001),别走 ComfyUI 原生节点。** ComfyUI 的 ACE-Step 节点链没有 5Hz LM,带歌词的歌会输出纯静音
(实测 `volumedetect` mean = -91 dB;换 FLAC/MP3 保存、换负面条件都没用)。只做纯器乐时 ComfyUI 能出声。

## 装在哪、怎么起

| 项目 | 内容 |
|---|---|
| 仓库 | `~/ACE-Step-1.5`(自带 `.venv`,python 3.11,torch + MPS + MLX) |
| 模型 | `checkpoints/` 下:`acestep-v15-turbo`、`acestep-5Hz-lm-1.7B`(主力)、可选 `5Hz-lm-4B`、`Qwen3-Embedding`、`vae` |
| 服务地址 | `http://127.0.0.1:8001`(`services.toml` 的 `[services.acestep]`,体检 `GET /v1/models`) |

启动(系统的启动页 / `storyagent ops launch acestep` 用的就是这条):

```bash
cd ~/ACE-Step-1.5
ACESTEP_LM_BACKEND=mlx TOKENIZERS_PARALLELISM=false \
  .venv/bin/python3 -m acestep.api_server --host 127.0.0.1 --port 8001 \
  --lm-model-path acestep-5Hz-lm-1.7B
```

要点(都是踩过的):
- **`--lm-model-path` 必须显式给。** 128 GB 内存会被判成最高档,自动去选本地没有的 4B LM,然后到 HuggingFace 下载并卡死。
- **不要用 `start_api_server_macos.sh`**:它先跑 `git fetch` 检查更新,macOS 没有 `timeout` 命令,会永久挂住。
- 启动约 25–40 秒,日志出现 `Application startup complete.` 且 8001 在监听才算好。
- 重启服务会清空它的任务队列,重启前确认没有在跑的批次。

## 接口(系统适配器用的)

| 步骤 | 请求 |
|---|---|
| 提交 | `POST /release_task`,返回任务 id |
| 查询 | `POST /query_result`,**必须传完整 UUID**(只传前 8 位会一直返回 status=0) |
| 取音频 | `GET /v1/audio?path=…` |
| 看模型 | `GET /v1/model_inventory` |

## 参数的意思

适配器把系统参数翻成服务的参数(`duration → audio_duration`,`steps → inference_steps`),默认值在 `models.toml`:
`duration 30、steps 8、guidance_scale 7.0、vocal_language zh、infer_method ode`。

| 参数 | 含义 / 建议 |
|---|---|
| `duration` | 秒数,1–600。**吐字发糊几乎总是时长不够**:30 秒塞 11 行词必然含糊,上到整首长度(240 秒)才清楚 |
| `steps` | 去噪步数。turbo 模型 8 步够用;你的实战配方里整首歌用 16 |
| `guidance_scale` | 文本贴合程度。turbo 默认 7;你的“最好听”记录里 ComfyUI 路线 cfg 2.0 最稳,两条路线不通用,别照搬 |
| `shift` | 采样时间表偏移 |
| `infer_method` | `ode`(确定、稳)或 `sde`(有随机性) |
| `bpm`、`key_scale`、`time_signature` | 速度、调性(如 `E minor`)、拍号。古风配方:bpm 65–72、E minor、4/4 |
| `vocal_language` | 人声语言,中文歌用 `zh` |
| `audio_format` | `mp3` / `wav` / `flac` |
| `thinking`、`use_cot_caption`、`use_cot_language`、`constrained_decoding` | 让 5Hz LM 先“想”一遍:补全风格描述、判断语言、约束解码。出人声的歌建议开 `thinking` |
| `lm_temperature`、`lm_cfg_scale`、`lm_top_k`、`lm_top_p`、`lm_repetition_penalty` | LM 的采样参数 |
| `lm_backend`、`lm_model_path` | LM 后端(`mlx` 在 Apple Silicon 最快)和 LM 模型 |
| `batch_size` | 一次出几版,1–8;想比较版本时用 |
| `task_type`、`sample_mode` | 任务类型(如改写、翻唱)与采样模式 |

输入:`prompt` = 风格标签(caption);`text` = 歌词;`refs` 里第一个音频是**风格参考**(不是声纹克隆,也不是翻唱)。
想让歌曲用你的声音:先出歌,再用 [Seed-VC](SEEDVC.md) 换音色。

## LM 档位(实测)

| LM | 4 分钟歌耗时 | 说明 |
|---|---|---|
| `acestep-5Hz-lm-1.7B` | 约 60 秒 | 主力 |
| `acestep-5Hz-lm-4B` | 约 2–3 分钟 | 语义码更准,生僻字更可能读对,但慢 2–3 倍 |

4B 的权重在国内走 ModelScope(约 14 MB/s,4.5 分钟下完 8.4 GB);HF 镜像会 308 跳回原站并卡在 55%。
`acestep-download --force` 报 “already exists” 是假成功,它只校验配置文件、不检查权重分片,要对照 `index.json` 的 `weight_map` 自己查分片。**下载权重前先问我(或你自己确认),别自动拉。**

## 出歌配方(实测)

```
bpm 65~72 · E minor · 4/4 · 240 秒 · 16 步 · thinking=true · 1.7B LM
```

- 风格变化用**编制轴**,比换种子差异大得多:
  - 民乐:古筝/琵琶轮指、竹笛+箫、二胡与人声对话、鼓点极轻
  - 交响:弦乐铺底、木管点缀、铜管推副歌、打击乐只在副歌与转调
  - 钢琴弦乐:钢琴主线、大提琴小提琴副歌轻入,不用打击乐和铜管
- 歌词规矩:提交前逐行 `strip`(缩进会混进歌词);**不要写 `【主歌】` 这类标记**(会被唱出来);每行 ≤8 字、主歌 3–4 行、副歌 4 行、尾声 2 行、整首 ≤100 字,靠重复撑结构。
- 生僻字、多音字常读错(溯洄、晞、跻、坻、涘、沚)。先换 4B LM;仍错就用**同音字替换**(溯洄→素回、晞→希、跻→机、坻→迟、涘→四、沚→止、湄→眉),只被唱不显示,听感对就行。
- 每个主题先出 3–5 版(换种子)再挑一版,先出便宜的小样试水。

## ComfyUI 路线(只在做纯器乐或做实验时)

你的另一篇笔记记录了一条“App 模式拖放工作流能出人声”的 XL SFT 管线:`UNETLoader(acestep_v1.5_xl_sft_bf16) → ModelSamplingAuraFlow(shift=3) → KSampler(steps 50, cfg 7.0, euler, simple)`,
`DualCLIPLoader(qwen_0.6b_ace15 + qwen_4b_ace15, type=ace)`,`TextEncodeAceStepAudio1.5`,负面用 `ConditioningZeroOut`,`SaveAudioMP3`。
但同一批笔记里**用 API 提交 `/prompt` 出的是静音**,而 2026-10-11 的诊断结论是原生节点缺 5Hz LM。两处记录有出入,
所以系统只把 ComfyUI 的 ACE-Step 用在纯器乐,人声一律走独立服务。检查是否有声的办法:

```bash
ffmpeg -hide_banner -i out.mp3 -af volumedetect -f null - 2>&1 | grep -E "mean_volume|max_volume"
# mean 在 -91 dB 附近 = 纯静音;文件大小不能当有声证据
```

## 常见问题

| 现象 | 原因和办法 |
|---|---|
| ComfyUI 出歌全是静音 | 缺 5Hz LM,换独立服务 |
| 启动卡住 | 用了 `start_api_server_macos.sh`,换成直接 `-m acestep.api_server` |
| 服务去下载 4B 并卡在 55% | 没写 `--lm-model-path` |
| `/query_result` 一直 status=0 | 只传了 id 前缀,要完整 UUID |
| 多批次互相灌队列 | 旧批次没杀干净;改参数前先 `pgrep -fl render.py` 清场 |
| 吐字糊 | 时长太短,上整首长度 |
| 想换成自己的声音 | ACE-Step 做不到,走 [Seed-VC](SEEDVC.md) |

## 你的实测笔记(Obsidian)

- [[本地 AI 出歌实战（ACE-Step 1.5 + 换声纹）]]
- [[ACE-Step XL SFT 人声经验总结]]
- [[ACE-Step 1.5 XL SFT ComfyUI 工作流]]
- [[音视频工具矩阵]]

(这些链接在知识库里指向同步进来的笔记;没同步笔记时只显示名字。)
