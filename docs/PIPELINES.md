# 管线方案(Pipeline Plan)—— 设计记录与实现状态(2026-10-22)

> **状态:已实现**(迁移 013、`pipelines.toml`/`kinds.toml`、`Settings.for_pipeline`、立项向导 API/CLI/网页、按方案取模型、按模型判过期、按方案体检、工坊按方案)。
> 与下面设计稿的差异:`recipes.toml` 保留(不再扩展),方案的 `steps` 没有做;没有方案的老项目按全局配置运行(`projects.pipeline` 为空);
> 方案里的 `requires` 校验并用于按方案体检。配置说明见 `docs/CONFIGURATION.md`,计划见 `docs/IMPLEMENTATION_PLAN.md`。

## 要解决的问题
现在"用哪个模型"散在三处:`models.toml` 里每种任务的默认模型 `[defaults]`、项目设置里按模型的覆盖(`projects.settings.models`)、
工坊里每种任务单独保存的预设;`recipes.toml` 只被读入校验,**从未用于执行**。结果是任何模型都能混搭(图片一套、视频另一套、
画幅各管各),容易出错(例:视频曾按横屏 1024×576 做、成片是竖屏)。

用户的要求(2026-10-22):**模型决定管线,不能随意混搭;要改就整套改。** 所以选择单位是"管线方案",不是单个模型。

## 方案
**管线方案 = 一整套**:每种任务用哪个模型(含档位/参数覆盖)、步骤顺序、画幅(交付档位)、时长范围、默认质量档、视频占比与剪辑选项(片头/片尾卡、淡入淡出)、
所需服务/工具。项目**立项时选一个**(和画幅同时决定),项目里所有任务的模型都从它来,项目里不能再单独换某一个模型。

### 配置放在哪里
- `storyagent/config/defaults/pipelines.toml`(程序自带的默认方案) < `$STORY_AGENT_HOME/config/pipelines.toml`(用户覆盖/新增)。
  **取代 `recipes.toml`**(配方 = 方案里的 `steps`)。`[default] pipeline = "..."` 是新项目的默认方案。
- 项目里只存方案名:`projects.pipeline`(迁移 013,空 = 默认方案)。画幅来自方案的 `delivery_profile`;项目自己的 `delivery_profile` 仍可在**同方向**内微调(已有规则)。
- 想改某一个模型 = 复制一份方案另存,整套改(不做"项目内单点覆盖")。

### 文件结构(草案)
```toml
[default]
pipeline = "vertical_drama"

[pipelines.vertical_drama]
label_key = "pipeline.vertical_drama"       # i18n
note_key  = "pipeline.vertical_drama.note"
delivery_profile = "series_vertical_720"    # 画幅/方向/时长范围
tier = "preview"                            # produce 默认质量档
[pipelines.vertical_drama.models]           # 方案里每种任务用的模型(必须齐全且 kind 对得上)
"image.generate" = "flux2-klein-base-4b"
"image.edit"     = "flux2-klein-base-4b-edit"
"video.generate" = "ltx-2.5-video"
"video.av"       = "ltx-2.5-av"
"audio.tts"      = "cosyvoice3-0.5b"
"audio.music"    = "acestep-1.5-turbo"
"audio.sfx"      = "stable-audio-3-medium"
"compose.assemble" = "ffmpeg-assemble"
[pipelines.vertical_drama.model_params."ltx-2.5-video"]   # 可选:该方案下的参数覆盖
[pipelines.vertical_drama.workflow]         # 合并进 [workflow]:video_share、keyframe_from_references、...
video_share = 0.5
[pipelines.vertical_drama.edit]             # 片头/片尾卡、淡入淡出
title_card = true
[[pipelines.vertical_drama.steps]]          # 步骤顺序(原 recipe)
kind = "image.generate"
```
加载时整套校验:模型存在、kind 对得上、交付档位存在、所需服务/工具齐全才算"可用";不合格的方案拒绝加载并给出原因。

### 要做的几套(都用本机已验证的模型栈,差别在画幅/时长/质量档/视频占比/剪辑)
| 方案 | 画幅 | 一集/一条长度 | 默认档 | 说明 |
|---|---|---|---|---|
| `vertical_drama` 竖屏短剧/连续剧 | 9:16 720p | 90–180 s | preview | 本次试播验证过的这套(FLUX 合成关键帧 + LTX + CosyVoice + ACE-Step + Stable Audio + ffmpeg) |
| `landscape_drama` 横屏短剧/网剧 | 16:9 720p | 3–10 min | preview | 同模型栈,方向换横屏 |
| `tv_series` 电视剧 | 16:9 1080p | 20–45 min | preview | 长集:镜头多,视频占比低/按预算;片头片尾卡、前情 |
| `advert` 广告 | 16:9 1080p(可复制成竖屏) | 15–60 s | final | 每镜都出视频,质量优先,无片头卡 |
| `feature_film` 电影 | 2.35:1 1080p | 90–180 min | final | 超长:分段生产,预算/视频占比由项目定 |
(预留:云端视频方案,接口已有,缺厂商。)具体数字只是起点,可在 `pipelines.toml` 里改。需要新增交付档位:`landscape_1080`、`tv_landscape_1080`、`advert_1080`、`film_scope_1080`(1920×816)等。

### 换方案的规则
- 方向相同(竖→竖)可换;**已有图片后不能在竖屏/横屏之间切换**(已有的画幅规则,沿用)。
- 换方案后,素材的"过期"由**模型是否一致**判断:素材所用作业的 `model_ref` 与当前方案对该任务给出的模型不同 → 标为"过期"(仍在用,直到被新素材替换),不会新旧混着悄悄用。
- 工坊(单独试用模块)**先选方案再用其中的模型**;另保留"单独试用某个模型"的入口,但明确标为"试用,不进项目"(待用户确认,见下)。

### 需要接线的地方(已查清,实现时逐个改成"取项目的有效设置")
有效设置 = 全局设置 + 项目所选方案(`Settings.for_pipeline(name)`:覆盖 `default_models`、合并 `workflow`、默认交付档位):
`media/ledger.py enqueue`(模型、`frame_of`)、`media/specs.py:287`(build_job 的默认模型)、`workflow/stages.py Ctx`(engine 里按项目构造)、
`workflow/media.py`(默认声音、keyframe 合成、视频挑选、`tier`、成片档位)、`workflow/plan.py`、`workflow/flow.py`、`workflow/series.py`、
`api/routes_workflow.py`(计划/启动)、`api/routes_system.py`(`/api/meta` 增加方案列表)、`workshop.py`、`media/healthcheck.py`(按方案体检)、CLI `config show`、创建项目的 API/CLI/网页(选方案代替选画幅)。
测试:加载校验(缺模型/错 kind/未知档位)、项目建立时锁定方案、换方案的方向规则、素材按模型判过期、各方案的尺寸方向。

### 待用户确认
工坊里是否保留"单独试用某个模型"的入口(倾向保留,标为"试用,不进项目")。

## 与本次试播验证的关系
试播验证期间发现的缺陷已修:横屏视频尺寸(现在由项目画幅决定方向)、上游素材重做后下游不判过期(`up_to_date` 比较依赖素材的时间)、
调度器不看整机内存(现在先看 `vm_stat`)、音效模型(ACE-Step 出噪音,改 Stable Audio 经 ComfyUI)。真实验证结论见 `docs/reviews/`。

## 竖屏视频的横纹与"走路飘"(用户反馈 2026-10-22;**实验已做,默认已改**)
实验与数据见 `docs/reviews/2026-10-22_pilot-and-p0.md`。要点:主因是**视频提示词与关键帧姿态不一致**(关键帧面朝镜头、提示词要人物走远 → 先转身、重影),其次是 12+4 步在走动场景里背景拖影。
已改:`preview` 档回到 16+5 步;模型负向词去掉与本剧无关的武侠词和"dark",加运动/渲染毛病词;`shot.visual` 要求视频提示词从关键帧姿态起步、一个连续动作。横条纹本轮没看到明显证据,自动质检留到 P2-4。

## 关键帧合成:快速候选(2026-10-22)

关键帧用 `image.edit` 由人物像 + 场景图合成,Base 4B(mflux,30 步)实测约 528 s/张,是整集成本的大头。
已登记候选模型 `flux2-klein-4b-edit-comfy`:FLUX.2 klein 4B **蒸馏版**经 ComfyUI(模板 `flux2_klein_edit`,4 步、无 CFG,Apache-2.0,权重本机已有:checkpoints/flux-2-klein-4b、text_encoders/qwen_3_4b、vae/flux2-vae)。
**实测(2026-10-22,项目 1 第 8 镜头,同提示词、同 3 张参考图、同种子 11722323,新代码 worker)**:
| 模型 | 尺寸/步数 | 耗时 | 看图 |
|---|---|---|---|
| Base 4B(mflux,原) | 720×1280 / 30 | 575 s | 基准 |
| 蒸馏 4B(ComfyUI) | 720×1280 / 4(preview 档) | **56 s**(约 10 倍快) | 两人、场景、蓝工服都对;人物更远、脸与基准有差异、表情更夸张,纸的姿态不同 |
| 蒸馏 4B(ComfyUI) | 864×1536 / 6(final 档) | 337 s(含冷启动模型加载,单次) | 同上,更清晰 |
测试时系统可用内存最低约 57 GiB,没有拖垮机器。单镜头单种子,是方向性结论;"脸像不像"主观,需要再多几个镜头确认。
结果好 → 在 `pipelines.toml` 加"快速关键帧"方案(`image.edit` 指向该模型),Base 4B 留作"精品"方案;方案内不混搭。
其他候选(需下载或未验证,暂不接):klein 9B-KV(非商用)、SDXL+InstantID、Qwen-Image-Edit-2511。
