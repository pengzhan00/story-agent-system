# Phosphene:LTX-2.5 的面板和 HTTP 接口

Phosphene 是装在 Pinokio 里的一个 Mac 本地视频生成面板,里面跑 LTX-2.5(和可选的 Hailuo H3 引擎)。它自己管模型和队列,
对外有一个 **HTTP 接口**(默认 `http://127.0.0.1:8198`),系统的 `phosphene` 适配器和你手写的 `ltxshot` 脚本都是调它。
模型层面的东西(帧数规则、配方、提示词、对白、角色)在 [LTX-2.5](LTX.md);这一页讲面板、接口和排障。

相关页面:[工具总览](../TOOLS.md) · [LTX-2.5](LTX.md) · [h3.c](H3C.md) · 你的笔记《LTX-2.5 长场景分段出片(Phosphene One Shot)》

## 它是什么,装在哪

| 项目 | 内容 |
|---|---|
| 位置 | `~/pinokio/api/phosphene`(版本见该目录的 `VERSION`,本机读到 4.19.11),仓库 `mrbizarro/Phosphene` |
| 启动 | 打开 Pinokio 应用:`open -a Pinokio`(系统的启动页 `[launch.phosphene]` 就是这条),面板在 `http://127.0.0.1:8198` |
| 权重 | 约 109 GB:q4_25(基础渲染)、gemma4_25(文本编码)、q8_25 与 hq_25(高质量/角色档) |
| 接口 | 无鉴权,只监听本机;POST 体是表单编码(`--data-urlencode`),响应是 HTTP/1.0,每次请求后连接关闭 |
| 端口 | 正常安装 8198;开发配置是 8199(`LTX_PORT` 可改)。请求没反应,先怀疑端口 |
| 官方文档 | 该目录下 `docs/API.md`、`PROMPTING.md`、`LTX_DIALOGUE.md`、`PANEL_REFERENCE.md`、`H3_ENGINE.md` |

面板顶部的标签:Video、One Shot、Images、Storyboard、Editor、Audio、Train Character。

## 常用接口

```bash
# 看状态:正在跑的、排队的、历史
curl -s http://127.0.0.1:8198/status | python3 -m json.tool | head -40

# 提交一条 5 秒带对白的镜头(表单编码)
curl -s -X POST http://127.0.0.1:8198/queue/add \
  --data-urlencode "mode=t2v" \
  --data-urlencode "prompt=… AUDIO: wind and steel. DIALOGUE in Mandarin: 就这点本事?" \
  --data-urlencode "width=1536" --data-urlencode "height=832" \
  --data-urlencode "frames=121" --data-urlencode "quality=high" \
  --data-urlencode "stage1_steps=16" --data-urlencode "stage2_steps=5" \
  --data-urlencode "upscale=fit_1080p" --data-urlencode "upscale_method=pipersr" \
  --data-urlencode "enhance=false" --data-urlencode "seed=42"
# 返回 {"ok": true, "id": "j-…"},用 /status 的 history 里该 id 的 status 和 output_path 取结果
```

| 接口 | 作用 |
|---|---|
| `POST /queue/add`(别名 `/run`) | 提交渲染。`mode`:`t2v`、`i2v`、`extend`、`keyframe`、`a2v`、`v2a`(仅 H3)、`image` |
| `GET /status` | 全部状态:`current`(含进度 `pct`、阶段、ETA)、`queue`、`history`(`status`、`elapsed_sec`、`output_path`、`error`)、日志 |
| `POST /stop?id=` | 停止正在跑的任务(`id` 不是当前任务时返回 409,什么都不停) |
| `POST /stop?mode=early` | 在下一个前向边界提前停,任务变 `stopped`(不是失败,但没有输出) |
| `POST /queue/remove` | 移除排队中的任务(`id`);正在跑的不行,要 `/stop` |
| `POST /queue/retry` | 按历史任务的参数重新排一次 |
| `POST /queue/clear` | 清空队列;返回的 `undo_token` 60 秒内可 `POST /queue/restore` 撤销 |
| `POST /queue/pause` / `/queue/resume` | 暂停/恢复派发(不影响正在跑的) |
| `POST /queue/batch` | 批量提交 |
| `GET /take/estimate`、`POST /oneshot/plan` | 只估算、只看切法,不渲染 |
| `POST /oneshot`、`GET /oneshot/status` | 长场景分段出片,见下 |
| `POST /upload` | 上传本地图片(One Shot 的起始帧必须先上传) |
| `GET /loras`、`/characters` | LoRA 和角色列表 |
| `GET /licences`、`POST /licences/accept` | 需要明示同意的许可(见下) |

状态轮询:每 15–30 秒 `GET /status`。任务进入 `done`、`failed`、`cancelled`、`stopped`、`error` 之一就是结束,在此之前是 `queued` 或 `running`。
`/status` 的日志缓冲里可能夹着控制字符,解析 JSON 前先去掉(`re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', ' ', raw)`,`json.loads(raw, strict=False)`)。

## One Shot:一场戏分多段出

要出 30 秒以上的一场戏,用 One Shot 而不是自己拼。它把总时长按 **5 秒一个节拍(beat)** 切,LTX 引擎每 **10 秒一段(part,241 帧)**,
渲完再拼接;h3 引擎每段 15 秒。总时长只能是 **30 / 45 / 60 / 90 / 120** 秒。

```bash
# 1) 先看切法,不渲染
curl -s -X POST http://127.0.0.1:8198/oneshot/plan -H 'Content-Type: application/json' \
  -d '{"prompt":"整场戏一句话总述(场景/服装/光线基调)","seconds":30,"engine":"ltx","quality":"high"}'

# 2) 提交:beats 每句 5 秒、每句只写一个动作
curl -s -X POST http://127.0.0.1:8198/oneshot -H 'Content-Type: application/json' -d '{
  "prompt": "整场戏总述", "seconds": 30, "engine": "ltx", "quality": "high",
  "beats": ["第一段:一个动作 + 机位景别", "第二段:…", "第三段:…", "第四段:…", "第五段:…", "第六段:收束"],
  "handoff": "last", "camera": "缓慢推近", "light_lock": "on", "retake": "on"}'
```

| 字段 | 说明 |
|---|---|
| `handoff` | `last`(默认)用上一段末帧交接;有角色时默认 `speech`,在嘴还在动时接上 |
| `camera` | 全片统一的一句运镜,直接放在每段开头 |
| `light_lock` | 锁光线,防止段间色温跳变(默认开) |
| `retake` | 漂移或口型不同步的段自动重做(默认开) |
| `image` | 起始帧路径,必须先 `POST /upload` |
| `enhance` | One Shot 内部强制关闭(否则 Gemma 改写会毁掉触发词和对白) |

更细的粒度(5 秒一镜)就自己切,每段单独 `queue/add`,用上一段末帧做 `i2v` 的首帧;续写用 `mode=extend`(`extend_frames` 的单位是 latent,**1 latent = 8 个视频帧**),
只重做某几秒用 `retake_start_sec`。面板预估的 ETA 为准,不要用单条 i2v 的耗时去外推(见 [LTX-2.5](LTX.md))。

## 许可闸门(HTTP 451)

下载或运行需要明示同意的模型(Hailuo H3 的 MiniMax H3 Community License、Ideogram 4、YuE2、FLUX.1 Krea [dev]、Bria FIBO)时,
相关接口返回 **HTTP 451** 和 `{"code": "licence_required", "licence": …, "url": …}`,直到在这台 Mac 上接受一次:
`GET /licences` 读条款,`POST /licences/accept` 接受,或者在面板里点一次。这是面板自己的闸门,和系统的合规闸门是两层。

## 系统怎么用它

- 模型:`ltx-2.5-video`(`video.generate`,画面,声音会被去掉)、`ltx-2.5-av`(`video.av`,画面加声音)、`ltx-2.5-lipsync`(`video.lipsync`,人像加语音音频,嘴跟着音频动)。适配器 `storyagent/media/adapters/phosphene.py`。
- 流程:`POST /queue/add` → 轮询 `GET /status` → 读 `output_path`(开了放大会有 `*_1080p` 同名文件)。取消:运行中的 `POST /stop?id=`,排队中的 `POST /queue/remove`。重启 worker 后可以按任务 id 接回还在渲的任务。
- 提交前会检查:`frames % 8 == 1`、宽高是 32 的倍数、`i2v` 要有图、`a2v` 要有图和音频;超过 121 帧而不是 `quality=high` 时记警告(会丢角色身份)。
- 面板会**悄悄把过大的分辨率压小**;适配器把实际尺寸写进素材元数据并记警告,出片后还是用 `ffprobe` 核一遍。
- 默认参数(`models.toml`):1536×832(竖屏 832×1536)、121 帧、24 fps、`quality=high`、16+5 步、`cfg_scale 3.0`、`upscale=fit_1080p` + `pipersr`、`enhance=false`,并带一份视频常见瑕疵的默认负面词。草稿档 576×1024、`balanced`、不放大;预览档 704×1280。
- 体检看 `GET /status`;面板暂停时系统报“降级”,没起来报“未运行”。

## 常见问题

| 现象 | 原因和办法 |
|---|---|
| 面板起不来,日志 `FileExistsError` | `~/pinokio/api/phosphene/mlx_outputs` 是符号链接,目标目录被删成死链。补建目标目录(只建目录,不动链接),不要改代码 |
| 请求没有任何响应 | 端口不对(8198 / 8199),或 Pinokio 里的 Phosphene 没点启动 |
| 提交返回 451 | 许可还没接受,见上 |
| `image not found` / `start frame not found` | `i2v` 没给图,或 One Shot 的起始帧没先 `POST /upload` |
| 出片尺寸比提交的小 | 面板静默压分辨率,出片后 `ffprobe` 核对;1536×864 实渲 1536×832 |
| 长片里角色身份丢了 | `quality=balanced` 在超过 121 帧时静默换成 Q4 蒸馏权重,长片一律 `high` |
| `lora` 触发词被吃掉 | `enhance=true` 的改写会删触发词,带 LoRA 一律 `enhance=false` |
| 冷启动很慢 | 首次要加载模型并编译着色器,热态才是正常耗时 |

## 长任务的纪律

整批任务用 `caffeinate -i` 包住(睡眠会冻死批次);成品不要放 scratch 目录(24 小时清);出片后必核实际分辨率。

## 你的实测笔记(Obsidian)

- [[LTX-2.5 长场景分段出片（Phosphene One Shot）]]
- [[音视频资产全景_MEDIA]]
- [[音视频工具矩阵]]

(这些链接在知识库里指向同步进来的笔记;没同步笔记时只显示名字。)

## 音乐引擎 YuE2(Audio → Compose)

详细的安装、用法、用时和许可见 [YuE2](YUE2.md);这里只列系统和面板的字段对照。

面板里的音乐引擎 YuE2-3B(MLX 移植,Python 3.12 独立 venv,权重约 11 GB,峰值 11 GB 内存,至少 24 GB 统一内存)按风格描述加歌词写一整首带人声和编曲的歌。
装好后 `GET /status` 的 `music.available` 为 `true`。它和 LTX 共用面板的队列和 GPU 锁,所以在系统里也是重作业(一次一个)。

系统里的模型是 `yue2-3b`(`audio.music`,`provider = "phosphene"`),**不是默认**:默认还是 `acestep-1.5-turbo`,要用 YuE2 就在项目/规格里选它。

| 系统里的字段 | 面板字段 | 说明 |
|---|---|---|
| 提示词 `prompt` | `music_style` | 风格用一段话写:曲风、乐器、歌手、语言、速度 |
| 歌词 `text` | `music_lyrics` | `[Verse]` / `[Chorus]` 标签单独成行;标签下面没词 = 纯器乐段 |
| `instrumental` | `music_instrumental` | 没写歌词时默认 `true`(纯器乐) |
| `duration`(秒,8–360) | `music_max_seconds` | 只是上限:歌随歌词结束,不会被拉长 |
| `music_quality` = `draft`/`final` | `music_quality` | 8 步 / 32 步;`draft` 档位默认用 draft |
| `score_mode` = `full`/`melody`/`off` | `music_mode` | 先写曲谱再合成;`off` = 不写曲谱 |
| `precision` = `bf16`/`8bit`/`4bit` | `music_precision` | 默认 8bit |
| `cfg_scale`、`title`、`seed` | `music_cfg_scale`、`music_title`、`music_seed` | |

交付物是 `mlx_outputs/music_<时间>_<名字>.wav`,旁边有 `.wav.json`(记录引擎版本、种子、纯器乐做法),系统把摘要写进资产元数据(`yue2_*`)。
估计用时(面板在 M4 Max 上的实测拟合):final 约 0.93× 歌长,draft 约 0.43×,加载约 12 秒。

**许可**:YuE2 是 CC BY-NC 4.0 加"个人创作者"许可,首次使用要在面板里同意一次(未同意时面板对排队请求返回 HTTP 451)。
系统里 `commercial_ok = false`,商用交付会被许可门拦下。纯器乐如果装了面板的可选器乐 LoRA(`music/lora/fetch`)效果更干净,没装时用"空段标签"做法。
提交前系统会先问面板引擎是否就绪(`/status` 的 `music`),没装好就在校验阶段报缺模型,不会排进队列。
