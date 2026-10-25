# YuE2:出歌(整首带人声、带编曲)

YuE2-3B 是 Multimodal Art Projection 的音乐基础模型:给一段**风格描述**加**歌词**,写出整首歌(人声和编曲一起)。本机用的是 vanch007 的 Apple Silicon 原生 MLX 移植(仓库 `vanch007/mlx-Yue`),
**装在 Phosphene 面板里**当作和 LTX、H3 平级的"音乐引擎"(面板 Audio → Compose)。系统里对应的模型族是 `yue2`,模型是 `yue2-3b`(`audio.music`),
走的适配器是 `phosphene`(`storyagent/media/adapters/phosphene.py`)。默认出歌模型仍是 [ACE-Step](ACESTEP.md);YuE2 是可选项,在项目或规格里选。

相关页面:[Phosphene 面板](PHOSPHENE.md)(接口和字段对照) · [ACE-Step](ACESTEP.md)(默认出歌) · [Seed-VC 换声](SEEDVC.md)(歌声换音色) · [工具总览](../TOOLS.md)

## 一句话结论

要**整首歌、有真人感的人声和完整编曲**,而且接受非商用许可,选 YuE2;要快、要商用、要 REST 服务,选 ACE-Step。YuE2 的歌长随歌词走,`duration` 只是上限。

## 装在哪

| 项目 | 内容 |
|---|---|
| 引擎 | `~/pinokio/api/phosphene/yue2-mlx`(独立 Python 3.12 venv,mlx 0.32.2;LTX 那边是 3.11 / mlx 0.31.1,所以必须分开) |
| 权重 | `~/pinokio/api/phosphene/mlx_models/yue2`,约 10.5–11 GB;生成器来自 `vanch007/mlx-Yue2-3B`(BF16 与官方 `m-a-p/YuE2-3B` 字节一致),解码器来自 `m-a-p/YuE2-Vae` |
| 运行脚本 | 面板内 `scripts/music/yue2_run.py`,引擎提交号写在 `scripts/music/engine_pin.txt` |
| 输出 | `mlx_outputs/music_<时间>_<名字>.wav` 和旁边的 `.wav.json`(引擎版本、种子、纯器乐做法);每首歌的中间产物(曲谱、语义 token、潜变量、噪声)在 `state/music/<任务>/` |
| 安装 | 面板里点"Install music engine (YuE2, ~11 GB)",或 Pinokio 侧栏的"Install the music engine";可续传,磁盘要有 14 GB 空闲 |
| 本机状态 | `GET http://127.0.0.1:8198/status` 的 `music.available = true`(2026-10-25 读到);翻唱模型(2.8 GB)和器乐 LoRA(约 300 MB)**没装** |
| 残留目录 | `yue2-mlx.broken-20261024` 是更早一次坏掉的检出,在用的是 `yue2-mlx`,可以确认后删除 |

内存下限 24 GB 统一内存(`MUSIC_MIN_RAM_GB`),实测峰值约 11 GB 进程内存。本机 128 GB,绰绰有余,但它和 LTX 共用面板的 GPU 锁,**一次只跑一个**。

## 怎么用(系统里)

`audio.music` 作业的输入沿用出歌的约定:`prompt` = 风格,`text` = 歌词。

- **风格**用一段话写:曲风、乐器、歌手、语言、速度,例如 `melancholic piano ballad, female voice, Mandarin, 70 bpm`。
- **歌词**用 `[Verse]`、`[Chorus]`、`[Pre-Chorus]`、`[Bridge]`、`[Outro]` 标签,标签单独成行,标签下面是要唱的词,段与段之间空一行。**标签下面没有词就是纯器乐段。**
- 没写歌词时系统默认当纯器乐(`instrumental = true`);要人声就给歌词,或显式设 `instrumental = false`。
- 参数和面板字段的对照见 [Phosphene 面板](PHOSPHENE.md) 的"音乐引擎 YuE2"一节。

## 质量档和用时

| 档位 | 合成步数 | 用时(面板在 M4 Max 64 GB 的实测拟合) |
|---|---|---|
| draft | 8 | 约 0.43 × 歌长 + 12 秒 |
| final | 32 | 约 0.93 × 歌长 + 12 秒 |

实测:final 60 秒歌用 58.5 秒,120 秒用 113.8 秒,177.8 秒用 180.2 秒;draft 120 秒用 64.2 秒。冷启动第一次加载不在这个拟合里。mlx-Yue 的 8 步快速模式在作者的 M3 Max 上快于实时(RTF < 1)。
8 位量化 AR 比 BF16 省约 40% 内存(4.33 → 2.66 GB),生成快约 40%(59 → 83 token/s)。这些数字来自面板文档和 mlx-Yue 自述,不是本机 M5 Max 的测量;系统首次出歌后会把真实用时记进成本账本。

## 五种生成方式(mlx-Yue)

1. 完整歌曲 + 自动写曲谱(`score_mode = full`,面板默认"Melody + chords")
2. 完整歌曲 + 自己提供 ABC 曲谱
3. 旋律 + 自动写曲谱(`score_mode = melody`)
4. 旋律 + 自己提供 ABC 曲谱
5. 不写曲谱,直接按风格和歌词生成(`score_mode = off`;可以带人声,不是"纯器乐"开关)

## 面板里的音乐工作室(系统暂未接)

每首歌都会保存中间产物,所以可以回头改:**New take**(同曲谱同词,换一次演绎)、**Re-roll the sound**(同演绎,重新录一遍,约三分之一首歌的用时)、
**New style on this score**(同曲谱换词或换风格)、**Cover it**(把一段录音转成曲谱,再按新风格重新演绎;需要 2.8 GB 翻唱模型 SheetSage2 + MERT2)。
还可以编辑 ABC 曲谱后重新渲染。这些目前只能在面板界面里做,系统的适配器只接了"写一首歌"。

## 纯器乐的两种做法

`--instrumental` 看面板里有没有器乐 LoRA(`mlx_models/yue2-loras/`,`ar_lora_inst_v3abc` 等,约 300 MB,CC BY-NC 4.0,不在安装步骤里,按需下载):
有就用 Maestro 的做法(器乐 AR 适配器强度 1.0,歌词换成 `[instrumental]`);没有就用"只留空段标签"的做法。**排队的歌不会因为没装这个可选下载而失败**,面板的 `.wav.json` 里 `instrumental_recipe` 记着实际用了哪种。
对"器乐风格"的提示词,基础模型本来就不会唱,两种做法几乎没差别;要在"点名歌手"的风格下压掉人声,才需要 LoRA。

## 许可

**CC BY-NC 4.0,外加"个人创作者"许可**:首次使用要在面板里同意一次;没同意时面板对排队请求返回 HTTP 451。系统里 `yue2-3b` 标成 `commercial_ok = false`,商用交付会被许可门拦下;
署名建议:Generated with YuE2 by Multimodal Art Projection(MLX 移植:vanch007)。具体能不能用于你的发布场景,以许可原文为准,这里不下法律结论。

## 排障

| 现象 | 原因和处理 |
|---|---|
| 作业在校验阶段报 `missing_model` | 面板里音乐引擎没装好或需要修复:面板 Audio → Compose 点 Install / Repair(会跳过已校验的权重,几分钟,不是重下 11 GB) |
| 排队请求返回 451 | 没在面板里同意 YuE2 许可 |
| 提示"GPU 锁" | 面板只读 `/tmp/phosphene_gpu.lock` 等命令行任务的锁,存在时拒绝出歌,先结束那个命令行任务;面板自己从不创建或删除别人的锁 |
| 歌被截断 | `duration`(最大长度)比歌词需要的短;它只截断,不会拉长 |
| 点了 Stop 什么都没留下 | 正常:停止在写歌途中会清掉该任务的文件 |
