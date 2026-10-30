# CODEX 任务书：把 a2v（对口型）管线接进 storyagent

> 交付时间：明天由 Codex 执行。本文件是任务定义 + 验收判据，不是建议。
> 目标是让 storyagent 能**自己派发唱歌/对口型镜头**，而不是靠外部脚本手搓。

---

## 一、现状（已核实，含文件行号）

`storyagent/media/adapters/phosphene.py` 里 a2v 只接了一半：

| 位置 | 已实现 | 说明 |
|---|---|---|
| `mode_for()` L143–150 | ✅ | `MediaKind.VIDEO_LIPSYNC` → `mode="a2v"`；也可由 `job.params["mode"]` 显式指定 |
| `_FORM` L57 | 部分 | 已透传 `audio_start_time`、`audio_conditioning_scale` |
| `validate()` L184 | ✅ | `a2v` 要求 `image` 与 `audio` 两个引用都在 |
| `_files()` L152–160 | ✅ | 按 `refs` 的 role 映射成表单字段 `image` / `audio` |
| `_form()` L228–243 | ✅ | 组装 `POST /queue/add` 的表单 |
| `submit()` L245–250 | ✅ | 提交并取 `external_id` |
| `poll()` L265–273 | ✅ | 用 `GET /status` 的 `current/queue/history` 判进度 |

**所以"提交一条 a2v"这条路是通的**，缺的是下面这些决定成败的细节。

---

## 二、缺口清单（每条都要做到）

### G1. 人声分离没接（影响最大）
面板自带 htdemucs，前端表单里的开关是 `audio_stem_auto=on`
（见 `~/pinokio/api/phosphene/webapp/js/characters.js` L1484）。
适配器目前**不传这个字段**，于是 a2v 拿整首混音驱动嘴型——人声被伴奏盖住，模型只能猜节拍，口型必然飘。

要求：
- `_FORM` 增加 `audio_stem_auto`（bool，默认 `True` for lipsync job）
- 或显式传提前分离好的 vocal stem 路径（见 G2），二者取一，并在 spec 里记录用了哪种

### G2. vocal stem 的资产化
要求新增一种可挂在 job 上的中间产物：`project.vocal_stem`（或 `song.vocal_stem`），
由 `POST /a2v/separator` 系列接口产出（面板 `GET /a2v/separator` 返回 `ready:true` 表示可用），
分离结果落盘后记进 `media_assets`，后续所有 a2v 镜头共享同一份，不要每个镜头重复分离。

### G3. `audio_start_time` 没有来源
适配器只是"如果 params 里有就透传"。**没有任何代码把它算出来。**
一条 a2v 镜头需要知道自己对应歌曲的哪一段。

要求：从 shot 的时间轴推导——
`audio_start = 该 shot 在成片中的起始秒`（`shots.render_payload.seconds` 累加，或直接用本机已入库的 `render_payload.audio_start`），
并在派发前校验它与歌曲段落边界的关系（允许落在段内，不允许跨段错位）。

### G4. 长唱段必须拆段
面板单次 a2v 的可靠窗口是 5 秒级（121 帧）；本机实测 241 帧/10 秒也跑得出来，
但前端自身的 Lip-sync 是**按 5 秒切段 + 用上一段闭口帧做锚点**（`characters.js` L1450–1489，注释 LIPSYNC-4）。

要求：
- 适配器支持 `max_part_seconds`（默认 5.0）与 `anchor_overlap`（默认 0.05）
- 一段 10 秒的唱段 → 提交 2 条 a2v job，第 2 条以第 1 条末帧为 `image`
- 拼接由 film 阶段做，不要在主渲染里做

### G5. 画质档与 a2v 的关系要说清
面板前端对 a2v **硬写 `quality=high`**（`characters.js` L1485，注释 "A2V is always pipeline-class"），
而 storyagent 有自己的 tier 体系。

要求：映射表写进适配器而不是散在调用处：

```
draft   → quality=quick   注意：面板可能对 a2v 忽略此项，需实测确认
final   → quality=high
```

实测锚点（本机 M5 Max 128GB，640×448）：
- `i2v` 6s/145 帧 quick ≈ **1.0–1.4 分钟**
- `i2v` 8s/193 帧 quick ≈ **1.9 分钟**
- `a2v` 10s/241 帧 20 步 ≈ **10–11 分钟**（是 i2v 的 6–8 倍，排产必须分开算）
- 同时跑 ComfyUI 出图会把它拖到 20 分钟以上（统一内存竞争），派发前必须查内存

### G6. 产物回写与拼片
要求：
- a2v 产物写 `media_assets`（`kind=video`、`slot=main`、带 `duration_sec` 与 `audio_start` 以便对账）
- 拼整片走 `/music/video/film` 那类通道或 storyagent 自己的 film 阶段，**不占 GPU**
- 拼片前校验：所有 shot `status==done` 且产物文件真实存在，缺镜就拒拼并报镜号
- 硬验收：整片 `duration` == 曲长（本曲 **241.666667 s**）

### G7. 旁白/对白是否发声，要显式开关
本项目当前口径：**旁白只做字幕，不发声**（用户原话「纯文字讲解剧情的旁白，不发声」）。
但系统里 `dialogue` / `narration` 默认会走 `audio.tts`（项目已有 26 条 tts spec）。

要求：给项目加显式开关（如 `settings.narration_voiced=false`），默认关；
开启时才生成 `audio.tts` spec。**不要默认发声**。

---

## 三、验收判据（缺一条都算没做完）

1. 用 script_id=13、shot_number=201 那条（车内中景 10 秒，`audio_start=36.0`）跑通一次真实派发：
   - 面板 `/queue/add` 收到的表单里同时包含 `mode=a2v`、`image`、`audio`、`audio_start_time=36.000`、`audio_stem_auto=on`
   - 产物落盘、`media_assets` 有记录、`media_jobs` 状态走到 `done`
2. 口型比"整首混音驱动"明显改善（同一镜 A/B 各出一版，人眼可辨即可，不要求逐音素对齐）
3. 10 秒唱段按 `max_part_seconds=5` 自动拆成 2 条，第 2 条 `image` 是第 1 条末帧
4. 派发前打印内存占用；已有一个占 20GB+ 的渲染进程时**拒绝开始**并报原因
5. `narration_voiced` 默认 `false`，跑完不产生新的 `audio.tts` spec
6. 整片拼好后 `duration` 与 241.666667 s 的差 < 0.1 s

---

## 四、不要做的事

- 不改 `mode_for`/`validate` 已有的 i2v、keyframe、t2v 行为
- 不改已发布的画布档位（`scripts/assert_registry.py` 的规则：新画布 = 新键，不得重定义）
- 不动 `scripts` id=12 与它那 32 镜（那是旧版，留作对照）
- 不在 a2v 里默认开启 TTS 旁白
- 不为了"跑通"把 a2v 降级成 i2v（那就退回到"嘴在动但不是跟人声"的老问题）

---

## 五、参考：面板 a2v 表单的完整字段（实测抄自前端）

```
mode=a2v
prompt=<镜头提示词>
audio=<歌曲或人声 stem 的绝对路径>
image=<首帧/角色图绝对路径>
image_crop_focus=0.5
width=640  height=448  frames=<(seconds*24)+1，须 %8==1>
seed=-1
audio_conditioning_scale=<float，可省>
audio_start_time=<秒，保留 3 位小数>
audio_stem_auto=on          ← G1 的关键，当前适配器没传
quality=high                ← 前端硬写；quick 是否生效需实测
accel=off
enhance=off
preset_label=<自定义标签，便于在产物里认人>
```

提交：`POST /queue/add`（form-urlencoded）→ 返回 `{ok:true, id:"j-…"}`
进度：`GET /status` → `current` / `queue` / `history`
