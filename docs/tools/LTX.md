# LTX-2.5:本机的音画一体视频模型

LTX-2.5 是本机**首选的视频模型**:一次出画面、对白和音效,5 秒最快 108 秒。它跑在 [Phosphene 面板](PHOSPHENE.md)里,
系统里的模型是 `ltx-2.5-video` / `ltx-2.5-av` / `ltx-2.5-lipsync`。这一页讲怎么写参数、怎么写提示词、有什么边界;
面板和接口的命令在 [Phosphene](PHOSPHENE.md)。数字来自你的 Obsidian 笔记(《音视频工具矩阵》《音视频资产全景》《LTX-2.5 长场景分段出片》)里的本机实测。

## 一屏记住

```
1536×832 原生 · quality=high · stage1=16 / stage2=5 · cfg 3.0 · 放大 fit_1080p + pipersr · enhance=false
帧数 %8==1(121=5 秒,169=7 秒,193=8 秒,241=10 秒) · 宽高是 32 的倍数
提示词要写 well lit / correctly exposed / crisp;不要写 misty / dawn / fog
长片(>121 帧)一律 quality=high
出片后必须 ffprobe 核实际分辨率
```

## 任务模式(`mode`)

| 模式 | 输入 | 用途 |
|---|---|---|
| `t2v` | 提示词 | 文生视频 |
| `i2v` | 提示词 + 一张图(绝对路径,必填) | 图生视频,图当首帧 |
| `extend` | 已有片段 + 提示词 | 接着往后(或往前)续;`extend_frames` 的单位是 latent,**1 latent = 8 个视频帧** |
| `keyframe` | 首帧(可加末帧或多个关键帧) | 在关键帧之间插值 |
| `a2v` | 一张人像 + 一段音频 | **口型对话(Lip-sync)**:嘴跟着音轨走,系统里的 `video.lipsync`,单段约 5 秒 |

## 参数的意思

| 参数 | 含义 / 规则 |
|---|---|
| `width` `height` | 必须是 32 的倍数,真 16:9 是 1920×1088 而不是 1080。面板会静默压小过大的尺寸,以 `ffprobe` 的实际值为准 |
| `frames` | **必须满足 `frames % 8 == 1`**。24 fps:121=5 s,169=7 s,193=8 s,241=10 s |
| `frame_rate` | 24。LTX 就是按 24 fps 训练的,改了会变差 |
| `quality` | `quick` / `balanced` / `standard` / `high`。`balanced` 最快,但**超过 121 帧会静默换成 Q4 蒸馏权重,角色身份丢失**;`high` 才是长片和角色的档。只有 `high`、`extend`、`keyframe`、`a2v` 的路径使用负面词,蒸馏档会忽略它 |
| `stage1_steps` `stage2_steps` | 两阶段高质量管线的步数,推荐 16 / 5(面板默认 10 / 3) |
| `cfg_scale` | 文本贴合程度,3.0 |
| `upscale` | `off` / `fit_720p` / `fit_1080p` |
| `upscale_method` | `lanczos`(普通)或 `pipersr`(超分,清晰得多) |
| `enhance` | 为 `true` 时让 Gemma 先改写提示词。**一律关**:它会删掉角色触发词、改掉对白 |
| `seed` | -1 随机;复现用固定值 |
| `character_strength` / `character_voice_strength` | 角色脸 LoRA 和声音 LoRA 的强度,默认都是 1.0 |
| `loras` | JSON 数组 `[{path, strength}]`;脸和声音是两个文件(`<trigger>.safetensors` 与 `<trigger>.audio.safetensors`) |
| `temporal_mode` | `native` / `fps12_interp24` / `windows`(用若干窗口接成长片) |
| `audio_start_time` `audio_conditioning_scale` | 口型模式下音频从哪里开始用、音频对画面的约束强度 |

## 速度实测(M5 Max 128 GB,单次、冷启动口径)

| 配置 | 耗时 |
|---|---|
| 5 s · 1024×576 · balanced(Q4,16 步) | **108 秒**(最快) |
| 8 s · 1024×576 · high(10+3)+ 720p | 457 秒 |
| 5 s · 1280×704 · high(12+4)+ pipersr 1080p | 480–600 秒 |
| 5 s · 1536×832 · high(16+5)+ 1080p | 约 590 秒 |
| 一单 i2v(5 秒级)你的实测 | 约 887 秒 |
| One Shot 30 s(3 段 × 10 s,High) | 面板估算约 11 分钟;每段 Quick ~3 / Balanced ~4 / Standard ~5 / High ~6 分钟 |

One Shot 的估算和单条 i2v 完全不同,**以面板显示的 ETA 为准**,别用单条耗时外推。One Shot 出的是 720p。

## 最佳配方为什么是 1536×832

同提示词同种子,统一缩到 1080p 后量:

| 配置 | 亮度 | 清晰度(Laplacian) |
|---|---|---|
| 1280×704 + lanczos → 1080p | 53.6 | 32.2 |
| 1536×832 原生 + pipersr | 103.2 | **210.5** |
| 1920×1088 原生 | 108.8 | 66.9(还更慢) |

所以定为 1536×832 原生出,再用 pipersr 放大到 1080p。

## 提示词怎么写

**够用的四件套**(缺一就容易不出片):主体(谁/什么)+ **一个**动作 + 机位景别(close-up、wide、slow push-in)+ 光线。
长度大约 25–45 个英文词、只含一个动作最稳。太短会空镜或失败;太长、塞多个动作会跳跃式实现。

| 写 | 不写 |
|---|---|
| `well lit`、`correctly exposed`、`crisp` | `misty`、`dawn`、`fog`(画面发糊、压暗) |
| 负面词:`underexposed, dark, blurry, deformed hands, extra limbs` | 不要写“fast forward / time-lapse / montage”(模型会硬切) |

带声音和对白,在提示词里按段写:

```
(画面描述)… AUDIO: wind and steel, distant drums. DIALOGUE in Mandarin: 就这点本事?
```

对白经验(你的 `LTX_DIALOGUE` 研究和实测):
- 中文对白可用(4/4 条用 whisper 反查都转出了原话),但 t2v 的口型只是“在动”,要逐音素对齐用 **Lip-sync 模式**(图 + 音轨)。
- **一句话里只让说话人的脸在画面里。** 两个人同框对话,口型会乱(同时念同一句、答非所问),这是 LTX 目前没解决的问题。要对话,写成“正反打”:同一条提示词里分镜头,A 说话的中景、*硬切*到 B 回答的中景,同一张桌子同一种光,房间底噪持续,每次回到的人物用同样的外貌词再描述一遍。
- 语速约每秒 2.5 个词,所以 5 秒的节拍只放一句、最多七个词,话前留一口气,话后把沉默写出来;开头先留一个无声的对视再开口,第一帧就说话会让口型无从对齐。
- 一个角色场景里只放**一张**真人脸:角色 LoRA 会把画面里每张脸都画成那个角色。另一个人用机器人、动物、玩偶或画外音。
- 画面里不要要求出现文字(招牌文字会生成乱码)。

## 角色一致性的边界(实测,不是调参能救的)

| 问题 | 事实 | 办法 |
|---|---|---|
| 跨镜头“同一个人”不稳 | 同提示词 4 个种子,两两 ArcFace 相似度只有 **0.240**(同一人 0.7–0.9,不同人 0–0.1) | 训 **rank 32** 的角色 LoRA。rank 8/16 官方标 “identity ungraded”,实测等于没训 |
| 段间接缝停顿 | 面板默认只交接 1 帧,每次接缝像重启 | `handoff=last`,或手动用上一段**最后约 73 帧**当上下文 |
| 长片漂移 | 段长控制在 10–20 秒,**60 秒后必漂** | 90/120 秒的 take 接受后段漂移,或拆成两个 45 秒 |
| 身份突然全变 | `balanced` 在 >121 帧静默换 Q4 | 长片一律 `high` |
| 训练成本 | 角色 LoRA:50 张图约 3 小时 | 只在确定要跨镜头同一人时做 |

## 命令速查

```bash
# 你的包装脚本(配方已固化,出片后自动校验尺寸)
ltxshot "提示词… AUDIO: … DIALOGUE in Mandarin: …"
ltxshot "…" --seed 777 --frames 169 --out ~/Desktop/AI视频/项目名    # 7 秒
ltxshot shots.json                                                   # 批量
# 选项:--out --seed --frames --width --height --no-verify

# 出片后核对
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,nb_frames,r_frame_rate -of csv=p=0 out.mp4

# 量亮度(量成片是否发暗)
ffmpeg -hide_banner -ss <中点秒> -i x.mp4 -frames:v 1 -vf signalstats,metadata=print:key=lavfi.signalstats.YAVG -f null -
```

## 系统里怎么设

三条模型记录都在 `models.toml`,默认值就是上面的配方;草稿档(576×1024、`balanced`、不放大)用来看构图和运镜,预览档 704×1280,
最终档用最佳配方。系统在提交前检查帧数和尺寸规则,超过 121 帧而不是 `high` 会记一条身份风险警告。
竖屏 720×1280 的剧集,画面尺寸是 832×1536 这一档,后续再放大或裁到交付尺寸。

## 已知短板

- 单窗口 121 帧(5 秒):更长要靠 One Shot、`extend`,或 4 条 5 秒并出后拼。
- LTX 没有原生的多角色对话控制;LoRA 只支持单人。
- 竖屏视频可能出现横向条纹(banding),可以在负面词里加 `horizontal stripes, banding, flicker`(系统默认已带)并调参数。
- 速度:high 档每条 8–10 分钟,不适合大量试错;先用 `balanced` 草稿看动作,再出最终档。
