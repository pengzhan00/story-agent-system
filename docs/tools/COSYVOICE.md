# CosyVoice:配音与声音复刻(含 AISHELL-3 声线库)

CosyVoice 是本机的**配音/声音克隆**模型:给一段 3–20 秒的参考录音,用这个人的声音念新台词。全本地、免费。
系统里的模型是 `cosyvoice3-0.5b`(默认,Fun-CosyVoice3-0.5B-2512)和 `cosyvoice2-0.5b`(备用,更稳),
适配器 `storyagent/media/adapters/cosyvoice.py`,每个任务**一次加载模型念完全部台词**(子进程,不起常驻服务)。

相关页面:[工具总览](../TOOLS.md) · [Seed-VC 换声](SEEDVC.md) · [后期与校验工具](POST.md)(whisper 反查)

## 装在哪

| 项目 | 内容 |
|---|---|
| 环境 | `~/cosyvoice`(自带 venv;`torch` 故意停在 2.5.1,CosyVoice 代码依赖旧接口,**不要升级**) |
| v2 模型 | `~/cosyvoice/CosyVoice/pretrained_models/CosyVoice2-0.5B` |
| v3 模型 | `~/models/cosyvoice3/Fun-CosyVoice3-0.5B` |
| 你的包装命令 | `~/.local/bin/clonetts`(调 `clone_tts.py`(v2)或 `clone_tts3.py`(v3)) |
| 许可 | Apache-2.0,可商用;但**声音的人格权不随之转让**,克隆真人声音要对方同意(系统的合规闸门要求勾选授权) |

## 命令(`clonetts`)

```bash
# 把一句话用某人的声音念出来(参考文本不给就用 whisper 自动转写)
clonetts --ref 录音.m4a --text "要念的话" --out 输出.mp3

# 参考文本已知时更快
clonetts --ref 录音.m4a --ref-text "录音里念的那句话" --text "…" --out out.mp3

# CosyVoice 3:3 秒就能克隆,可调语速
clonetts --engine cv3 --ref 录音.m4a --text "…" --speed 1.1 --out out.mp3

# 批量旁白(narration.json)
clonetts --ref 孩子录音.m4a --narration 工程/narration.json --out-dir 工程/voice_clone
```

| 选项 | 含义 |
|---|---|
| `--ref` | 参考音频。3–10 秒最合适(10–20 秒也行),安静、无背景音、普通话、正常语速 |
| `--ref-text` / `--ref-text-file` | 参考音频里说的原话。不给就用本地 whisper 转写;`--no-transcribe` 关掉转写 |
| `--text` / `--narration` | 要念的话 / 批量旁白 JSON |
| `--engine cv2\|cv3` | v2 稳定;v3 3 秒即可克隆、支持拼音级发音修正、可调语速 |
| `--speed N` | 语速倍率(仅 v3,默认 1.0) |
| `--out` / `--out-dir` | 输出,24 kHz wav,`--out` 给 mp3 时自动转码 |

v3 的提示文本必须含 `<|endofprompt|>`(系统的 runner 自动补);v3 比 v2 快(同一句 18 字:3.64 秒对 5.00 秒)。

## 三种合成模式(系统的 `mode`)

| 模式 | 需要什么 | 效果 |
|---|---|---|
| `zero_shot`(默认) | 参考音频 + **参考文本** | 复刻音色,念新台词;语气主要跟参考音频走 |
| `cross_lingual` | 参考音频 | 跨语言复刻,不需要参考文本 |
| `instruct` | 参考音频 + 一句**风格指令** | 保留音色,语气由指令控制(“用愤怒的语气说”) |

**台词带情绪时系统自动走 `instruct`**:每句台词的“情绪”(暴怒、犹豫……)会按模板 `emotion_prompt`
(`models.toml` 里可改,默认“请用非常{情绪}、情绪饱满强烈、语气起伏明显的语气说”)变成风格指令。参数 `emotion = false` 可关掉回到纯零样本。
实测:同一声线、同一种子,`instruct` 比零样本明显更有情绪;但参考音频本身很平静时,情绪的上限也受限制——要更强,就换一段情绪更足的参考录音。

## 参数

| 参数 | 含义 |
|---|---|
| `mode` | 见上 |
| `speed` | 语速 0.5–2.0(仅 v3 生效) |
| `output_format` | `wav` / `mp3` |
| `ref_min_sec` / `ref_max_sec` | 参考音频的长度窗口,默认 3–20 秒 |
| `auto_transcribe`、`language` | 没给参考文本时是否用 whisper 转写、转写语言 |
| `emotion`、`emotion_prompt` | 是否把情绪变成风格指令、指令模板 |

输入:`text`(一句)或 `lines`(`[{"id","text"}]` 一次多句,每句一个素材);`voice_ref`:`{"audio","text","consent"}`;`instruct`(风格指令);`prompt`(这句的情绪)。
输出的真实时长由 ffprobe 测量,**时间轴按实际念出来的长度排**,不是按字数估。

## 声线库:AISHELL-3

`~/myworkspace/datasets/aishell3/`:OpenSLR SLR93,北京希尔贝壳 2020 年发布,**Apache-2.0**,约 85 小时、88,035 条、218 位说话人(男 43、女 175),
44.1 kHz / 16 bit 单声道,安静室内高保真。`spk-info.txt` 记录每人的年龄组、性别、口音。当前只解压了 `test/`,`train/` 还没解(压缩包 18 GB,`gzip -t` 通过)。

挑音色参考的做法:
1. 按 `spk-info.txt` 过滤性别、年龄组、口音,挑一个说话人;
2. 在他的目录里挑 **5–15 秒、单句完整、无长停顿**的片段,从 `content.txt` 取对应文本(zero-shot 的参考文本必须和音频严格对应);
3. CosyVoice v2 吃 16 kHz,44.1 kHz 要先降采样;
4. 一次只用一个人,参考音频里混进第二个声音会污染音色;切完检查首尾有没有爆音。

系统里的三个角色声线(苏苏、小明、雷刚)就是这样挑出来入库的(按元数据挑,没有逐条试听)。数据许可是 Apache-2.0,但本库的定位是“音色参考 / 研究 / 内部试做”,**别当成商用配音的授权来源**——真实说话人的人格权是另一层。

## 常见问题

| 现象 | 原因和办法 |
|---|---|
| 台词平淡没感情 | 零样本跟参考音频走;让台词带情绪(系统会走 `instruct`),或换情绪更足的参考 |
| `reference voice must be 3-20 s` | 参考音频长度不在窗口内,截一段 |
| `zero_shot needs the transcript` | 没给参考文本且没有 whisper 可用 |
| 念得太快/太慢 | v3 用 `speed`;v2 无此参数 |
| 生僻字读错 | 换同音字(只被念不被显示),或 v3 的拼音修正 |
| 想要“像孩子/本人” | 用本人录音当参考;跨性别/跨年龄不是 CosyVoice 的强项 |

## 你的实测笔记(Obsidian)

- [[语音库_AISHELL3_使用说明]]
- [[音视频工具矩阵]]
- [[音视频资产全景_MEDIA]]

(这些链接在知识库里指向同步进来的笔记;没同步笔记时只显示名字。)
