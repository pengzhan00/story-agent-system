# 红果短剧工业化生产系统改造方案

## 问题诊断

### 核心问题 1: 每场景只生成 1 个 shot（硬编码）
**位置**: `core/orchestrator.py:170 — shot_number=1`
**后果**: 无论 scene 多复杂，只产生 1 个镜头。80 集每集 10-20 个场景 = 800+ shot 才对，现在只有 scene 数 = shot 数。

### 核心问题 2: acts(幕数) 写死在 3 且不可调
**位置**:
- `agents/writer/core.py:16 — acts: int = 3`
- `agents/writer/core.py:25 — f"...with {acts} acts"`
- 短剧标准是 4-6 幕每集，但代码硬编码 3

### 核心问题 3: 单 shot 渲染，没有复用 ComfyUI render_pipeline 的多管线切换
**位置**: `core/orchestrator.py` render 阶段用的是 `BatchRenderer` + pip install comfy-cli 兜底
**后果**: 渲染 9 个 shot 就完了，不会利用已配置好的 flux_wan2_twostage / wan2_ti2v / animatediff 三管线

### 核心问题 4: 只有 1 集
**位置**: `core/orchestrator.py:139 — "number": 1, "title": "第1集"`
**后果**: 生成系统只产生第 1 集就停，没有多集流水线

### 核心问题 5: TTS/BGM 完全没接入管线
**后果**: 渲染完视频没有配音没有背景音乐

### 核心问题 6: workflow JSON 里的 prompt 是占位符
**位置**: `pipelines/flux_txt2img_workflow.json:41` — `"anime scene, cinematic composition"`
**后果**: 无论剧本怎么变，渲染出来的画面内容都一样

---

## 改造方案

### Phase 1（最优先 — 短剧生产能力）

#### 1.1 多场景多 shot 生成
- **修改** `agents/writer/core.py:generate_storyline()` — 参数扩增：
  - `acts=5`（默认 5 幕，红果短剧标准 4-6 幕）
  - `scenes_per_act=4`（每幕 4 个场景）
  - `shots_per_scene=3-5`（每个场景 3-5 个镜头）
- **修改** `core/orchestrator.py:_create_shot_plan_txn()` — shot_number 递增，每个 scene 生成多个 shot
- **修改** prompt 模板，让 LLM 生成包含分镜的完整 scene 定义

#### 1.2 多集连续生成
- `orchestrator.py:episode_number` 循环生成 5-10 集（或输入总集数）
- 每集独立剧情 + 连续角色

#### 1.3 LLM 生成带 shot 分解的 scene
- Writer agent prompt 输出格式扩增：每个 scene 包含 `["shots": [{camera_angle, characters, dialogue, mood, visual_prompt}, ...]]`
- `_create_shot_plan_txn` 据此创建多个 shot

### Phase 2（渲染管线正确使用）

#### 2.1 render 阶段调用 RenderDispatcher 而非 BatchRenderer
- `core/orchestrator.py` 中 render 阶段改为走 `pipelines/render_pipeline.py` 的 `RenderDispatcher`
- 根据当前 `active_pipeline` 选择管线
- prompt 从 shot.dialogue + shot.visual_prompt 拼接，写入 workflow JSON

#### 2.2 渲染多集任务队列
- `list_shots(status="rendered")` 改成 `list_shots(status="ready")`
- 按 episode → scene → shot 顺序串行渲染
- 渲染前动态修改 workflow JSON 的 prompt 字段

### Phase 3（音视频完整）

#### 3.1 TTS 批量接入
- 每个 shot 的 dialogue 逐条调用 ChatTTS
- 支持多角色（男声 seed=2, 女声 seed=42）
- 输出到 audio_assets 表

#### 3.2 BGM 接入
- MusicGen 单次生成 → 整集循环 loop

#### 3.3 Composer 合成
- 视频 + TTS + BGM → ffmpeg 合成最终剧集

---

## 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `agents/writer/core.py` | prompt 模板：多幕/多场景/多 shot，acts 默认=5, scenes_per_act=4 |
| `core/orchestrator.py` | shot_number 递增，多集循环，render→dispatcher, TTS/BGM/Compose |
| `core/database.py` | 可能需要 `list_shots_by_episode(episode_id)` |
| `ui/app.py` | 状态面板显示集/幕/场景/shot 真实数量，TTS/BGM 进度 |
| `pipelines/render_pipeline.py` | 加 `dynamic_prompt_workflow()` 函数注入 prompt |
| `pipelines/flux_txt2img_workflow.json` | prompt 节点标记为可替换占位符 |

---

## 验证标准

- [ ] 生成 5 集 x 5 幕 x 4 场景 x 3 shot = 300+ shot
- [ ] 渲染时 prompt 内容来自剧本（不是占位符）
- [ ] TTS 9/9 完成
- [ ] 合成输出可观看的 MP4 文件
- [ ] 三管线均可选

---

## 风险

- LLM context 限制：5 集剧情一次生成可能超 token 限制 → 每集独立生成
- ComfyUI 渲染 MPS 内存：300 shot 不能全部排队 → 逐集渲染，每集渲染完释放
- 渲染时间：Wan2 5B 约 7min/shot → 300 shot = 35h → 先只跑 5 集 smoke test
