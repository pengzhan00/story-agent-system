# 交接手册:真相源、数据结构、架构、如何接着做

> 给"换一个人(或换一个 AI 会话)接着做"用。读完这一份,加上 `CLAUDE.md`,就能继续。
> 最后更新:2026-10-22,P0 收口(分支 `claude/init-127186`,见 `docs/reviews/2026-10-22_baseline-review.md`)。数值/行为以代码为准;这里写的是**在哪里找真相**。

## 0. 一句话

本地离线的"创意 → 短剧 MP4"系统(v2):语言模型写故事,媒体适配器(图/音/乐/视频/拼接)把**可编辑规格**变成**带溯源的资产**,
最后用 ffmpeg 成片、质检、导出。Python 3.12 + SQLite + FastAPI(SSE)+ React/Vite。目标平台 macOS / Apple Silicon。
终极目标:能做出**一部连续的竖屏短剧**(也可能是广告、微电影、大电影:目标时长 10 秒到 3 小时,不锁死)。见 `docs/SERIES.md`。

## 1. 真相源(哪里说了算)

| 要回答的问题 | 真相源 | 备注 |
|---|---|---|
| 代码是什么版本 | Git `origin/main`(单分支,不开 PR/不开一堆分支) | v1 只在 tag `v1-final`(ca223f5);工作树 `.claude/worktrees/init-127186` 分支 `claude/init-127186` 上游就是 `origin/main` |
| 故事内容(人物、剧本、镜头…) | SQLite `storyagent.db` 的 story 表 | `projects/characters/scripts/episodes/shots/scenes/music_themes/sound_effects` |
| 生成什么(提示词、参数、种子) | `gen_specs`(只增不改,编辑=新版本) | 规格才是创作者反复编辑的对象;`gen_specs_current` 视图给最新版 |
| 一次生成做了什么 | `media_jobs`(冻结快照:解析后的 inputs/params/seed) | 幂等键防重复;租约+心跳防丢;状态机见 MEDIA_CONTRACT |
| 产物是什么 | `media_assets`(带 sha256、实测时长/分辨率、provenance) | **台账是权威,磁盘只做校验**;每 (project, shot, kind, slot) 至多一个 `is_current` |
| "完成"的定义 | 产物文件存在且 ffprobe 读得出 | 不是"服务说完成了" |
| 跨项目命名素材 | `library_items`(`library:<role>/<name>`) | 角色:voice portrait lora music sfx reference video story script dialogue image |
| 工作流进度/队列 | `workflow_stages`(按项目×阶段)、`workflow_runs`(队列)、`workflow_data`(plan/style 等小结果) | **目前不按集记录阶段**(见 SERIES.md S3) |
| 工坊模块的起点 | `workshop_presets`(每个 kind 的模型/档位/参数) | 在 models.toml 之上 |
| AI 调用记录 | `generation_logs`(提示词/回复/耗时) | 网页"AI 调用"可看全文 |
| 配置 | 分层 TOML,见 §4 | 代码里不许有写死的路径/端口/中文 |
| 用户可见文案 | `storyagent/locales/{zh-CN,en}.json` | `storyagent i18n check` 必须绿;网页目录来自 `/api/i18n/{locale}` |
| 提示词 | `storyagent/agents/specs/*.toml`(用户覆盖:`$HOME/config/agents/`) | 网页「AI 提示词」可改,下一次调用即生效(worker 不用重启) |
| 数据库结构 | `storyagent/db/migrations/00N_*.sql`(带校验和,当前 6) | 迁移前自动快照 `*.bak-vN` |
| API 契约 | OpenAPI(`web/openapi.json` 由 `storyagent api openapi` 生成;TS 类型 `web/src/api/schema.d.ts` 由它生成) | 改了接口要重新生成并提交 |

**活数据不进 Git**:`data/`(默认数据家目录,在工作树里)和 `$STORY_AGENT_HOME`:数据库、媒体、`config/`(本机覆盖)、`logs/`、`run/`、`work/`。
`.gitignore` 已盖住。本机覆盖例:`data/config/services.toml` 里 `[tools.ffmpeg]` 指向 ffmpeg-full。

## 2. 进程与运行方式

两个互不调用的进程,只通过 SQLite(WAL)通信:

- `storyagent serve [--port 8765]`:FastAPI + 构建好的网页(`web/dist`)+ SSE 事件流。
- `storyagent worker`:执行**媒体作业**(`media_jobs`)和**工作流运行**(`workflow_runs`),线程池,租约+心跳,重启后接管/重投。
- `scripts/start.sh`:一起起这两个(Ctrl-C 只停这两个)。
- 外部服务(Ollama、ACE-Step、Phosphene/Pinokio)**系统不替人管**:网页「启动台」/`storyagent ops launch` 只启动 `[launch.<服务>]` 里列出且被要求的,**从不停止/杀任何进程**。禁止 `pkill`,只用明确的 PID。
- 改了 Python 代码必须重启 worker 和 server(长驻进程只加载一次)。**重启 worker 会打断正在跑的重任务**(租约过期后会被重投)。

## 3. 架构(读代码的地图)

```
storyagent/
  config/      分层配置加载(Settings),defaults/*.toml 是出厂值
  domain/      枚举:MediaKind(<输出域>.<操作>)、JobStatus、QualityTier、SubjectType、ErrorCode
  db/          连接、迁移、(可选)旧库导入
  i18n/        t(key, **kw)、目录加载
  llm/         后端客户端(ollama / openai 兼容 / llamacpp)、LLMRouter(角色→模型)、JSON 修复、记 generation_logs
  agents/      AgentSpec 注册表(prompt+schema)+ AgentRunner;specs/*.toml、specs/schemas/*.json
  story.py     StoryStore:故事表的读写
  projects.py  ProjectStore:项目(含 settings:compliance/models/allow_restricted_licenses/target_sec)
  workflow/    阶段 analyze→cast→script→scenes→music→shotplan→specs→produce→compose;state.py 状态/队列;
               engine.py 执行;stages.py/media.py/seed.py/shotplan.py 各阶段;progress.py 进度/ETA;flow.py "流程说明"
  media/       contracts(Adapter 协议)、ledger(作业/资产台账)、specs(规格存储)、worker、broker(并发/内存/互斥)、
               assetrefs(asset:/job:/library: 解析)、library、compliance、healthcheck、registry、params(参数分层)
    adapters/  acestep comfy mflux phosphene cosyvoice seedvc whisper h3 longcat lora_train llm assemble remote_video fake(+_http/_subprocess 公共件)
    runners/   assemble_episode.py(仅标准库,子进程跑 ffmpeg)
  compose/     timeline.py(纯函数 EDL)、pacing.py(目标时长→结构/镜头时长/QC窗口)、qc.py、export.py(交付包)
  workshop.py  工坊:模块(story script image episode lora song voice video transcript)= 单独可用,或串成流水线
  launcher.py  启动台逻辑
  api/         routes_*.py + schemas.py(Strict 模型)+ errors.py(统一错误信封,码→ api.error.<code>)
  cli.py       `python -m storyagent …`(所有命令的入口)
web/           React+TS+Vite;pages/(Launch Projects Project Workshop Library Queue Prompts System)
tests/         pytest(默认不含 live);tests/test_guards.py 是红线
docs/          API CONFIGURATION MEDIA_CONTRACT RUNBOOK WORKFLOW USER_GUIDE PARITY_MATRIX SERIES HANDOFF(本文);reviews/ 审查报告
```

### 3.1 核心数据流(必须记住)

```
故事表 ──(seed 阶段)──► gen_specs(可编辑、只增、可锁)
                              │ SpecStore.build_job:冻结快照
                              ▼
                        media_jobs(排队→租约→适配器 validate/submit/poll/fetch)
                              │ 成功 = 文件存在 + ffprobe 读得出
                              ▼
                        media_assets(is_current 每槽位至多一个;其余是候选)
                              │ compose:先量台词时长 → 排时间轴(EDL)→ ffmpeg → QC → 交付包
                              ▼
                        episode.mp4 + srt + provenance
```

- **引用**:任何作业输入里,`asset:<32位hex>` / `job:<id>`(已完成作业的主产物)/ `library:<role>/<name>` 由 worker 在执行前解析成文件路径,并把 sha256 记进作业事件。工坊流水线里 `"@prev"` = 上一步的 `job:<id>`(并写入 `media_jobs.depends_on`,上一步失败则本步 `dependency_failed`)。
- **工坊作业无项目**:`project_id IS NULL`,产物在 `projects/_unassigned/media/…`。
- **适配器缺模型/缺工具 → `missing_*` 错误,绝不静默降级**。
- **合规**默认开(AIGC 标识、授权、受限许可闸),可全局/项目/环境变量关;**溯源始终记录**。

### 3.2 表(001-016)

| 表 | 关键列 |
|---|---|
| projects | id slug name description genre format aspect_ratio delivery_profile content_language status **settings(JSON)** **kind pipeline brief(JSON,迁移 013)** |
| scripts | project_id episode_no title synopsis acts(JSON) total_scenes |
| character_looks / props(迁移 016:造型按人物×(集,幕,场)起生效、道具固定描述;镜头载荷 `subject.characters[].costume`、`continuity.props`) | character_id label costume from_episode from_act from_scene ; name description introduced_episode |
| review_notes(迁移 015:批注,状态 open→redoing→resolved/dismissed,redo_job_ids) | project_id subject_type subject_id slot shot_id asset_id body author status resolution redo_spec_version |
| users / user_sessions / audit_log(迁移 014:账号、会话令牌只存 SHA-256、只增审计) | username role password_hash(scrypt) active failed_logins locked_until ; token_hash expires_at revoked_at ; actor action path project_id status detail |
| characters(+introduced_episode, locked:迁移 011) | project_id name role appearance personality voice_profile voice_ref_audio/text voice_ref_consent lora_ref ref_images |
| scenes(场景设计) / music_themes / sound_effects | 各自属于 project;music_themes 有 kind(bgm/theme) lyrics duration_sec scene_ref |
| seasons | project_id number title summary planned_episodes target_sec status(每个项目都有第 1 季) |
| episodes | project_id number(全剧连续编号,各处的键) season number_in_season title summary hook cliffhanger status,UNIQUE(project,number) |
| shots | project_id episode_id act/scene/shot_number dialogue(JSON) narration render_payload(shot.v3 JSON) locked status **treatment(auto/video/still,迁移 010)** |
| gen_specs / gen_spec_locks | subject_type subject_id slot version kind model_ref prompt negative_prompt text_payload params seed refs depends_on author |
| media_jobs | kind provider model_ref tier status inputs params seed idempotency_key heavy est_memory_gib lease_* external_id progress error_* attempt **depends_on pipeline_id pipeline_step** |
| media_assets | job_id project_id shot_id spec_id/version kind slot path sha256 duration_sec width height fps has_audio metadata provenance version is_current trashed_at |
| job_events | job_id ts level event data |
| library_items | role name asset_id description tags meta source_asset_id |
| workflow_stages | 主键 (project, stage, **season, episode**):`analyze`/`cast` 记在 (0,0)(全剧共用),`outline` 记在 (季,0),其余阶段按集各记一份;全剧阶段重跑使所有集的下游 stale,某集阶段只影响该集 |
| workflow_runs / workflow_data | 见 §1 |
| workshop_presets | kind model_ref tier params |
| generation_logs | project_id agent model prompt response tokens duration_ms system label |

### 3.3 配置分层(低→高,后者覆盖)

出厂 `storyagent/config/defaults/*.toml` < `$STORY_AGENT_HOME/config/*.toml` < 环境变量 < `projects.settings` < 作业/规格参数。
文件:`services`(服务地址、工具路径、`[launch.*]`、`[runtime]`、`[api]`、`[healthcheck]`)、`models`(模型注册表+`[defaults]`+参数分档)、
`delivery`(交付档位、`[pacing]`、`[mix]`、`[subtitles]`、`[qc]`、`[estimate]`)、`compliance`、`recipes`(未被工作流使用)、`llm`(后端与角色)、
**`pipelines`(管线方案:一整套模型+设置,项目立项时选一个,不能混搭)**、**`kinds`(作品类型,可扩展)**。项目的有效设置 = `Settings.for_pipeline(projects.pipeline)`(`Ledger.settings_for`);无方案的老项目用全局配置。
参数优先级:适配器内置 < `models.X.params` < 档位 `tiers.draft|preview|final` < `projects.settings.models.X` < 作业 `params`。
**换模型只改配置。**

### 3.4 目标时长(不锁死)

`target_sec`(运行参数 > 项目设置 > 交付档位)→ `compose/pacing.py`:决定幕/场景/镜头数、镜头计划时长(配音更长时自动延长)、QC 的时长窗口(±`[pacing] tolerance`)。
从 10 秒到 3 小时都接受(只防笔误:1 秒–1 天)。档位 `series_vertical_720`:默认 120 秒、范围 90–180,只是默认值,**不是平台要求**。

## 4. 红线(测试强制)

`tests/test_guards.py`:`storyagent/` 里不许出现 `/Users/`、`~/myworkspace`、`127.0.0.1`/`localhost` 字面量、`pkill`/`shell=True`/`os.kill(`、中文字符串字面量。
服务/工具/模型一律来自 `Settings`;用户可见文案一律 i18n key(网页有 `react/jsx-no-literals`)。
**不碰别人的进程**;**不重新下载大模型**(下载需用户批准);**不装 Playwright**(网页验证用内置浏览器)。

## 5. 怎么验证(门禁)

```bash
.venv/bin/pytest                      # 约 400+ 测试,默认不含 live
.venv/bin/ruff check storyagent tests && .venv/bin/mypy
(cd web && pnpm typecheck && pnpm lint && pnpm test && pnpm build)
.venv/bin/python -m storyagent i18n check
.venv/bin/python -m storyagent api openapi --out web/openapi.json && (cd web && pnpm gen:api)   # 改了接口后
```

全局规则:任何"基线化/发布候选/可交付"结论之前,必须先跑 `software-system-review` 技能(全量验证全绿,否则只能是 partial/fail)。上一份报告:`docs/reviews/2026-10-21_v2-release-candidate.md`(verdict partial)。
提交署名:`Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`。只推 `origin/main`,不开 PR、不留分支。

## 6. 本机环境事实(2026-10-21)

- **两套环境(别混)**
  | | 生产 | 开发 |
  |---|---|---|
  | 目录 | 主检出 `story-agent-system/`(分支 `main`,必须干净) | 工作树 `.claude/worktrees/init-127186`(分支 `claude/init-127186`) |
  | 虚拟环境 | `story-agent-system/.venv`(`uv pip install -e .`) | 工作树 `.venv`(带 `[dev]`) |
  | 数据 | `story-agent-system/data/`(数据库、媒体、`config/`);**不进 Git,要备份** | 工作树 `data/`(沙盒,随时可丢) |
  | 启动 | `scripts/start.sh`(端口 8765) | `scripts/start.sh --port 8766` |
  | 更新 | `scripts/deploy.sh`(快进+安装+构建网页+迁移,**不重启**),再重启 start.sh | 直接改代码、测试 |
  `data/config/services.toml` 覆盖 ffmpeg-full 路径(两套各一份)。网页:生产 `http://localhost:8765`。
  2026-10-21 已把数据库(含 LongCat/故事/剧本/图片的结果)和 `config/` 迁到生产 `data/`,生产已从主检出启动。
- Ollama 0.40.6;已装 `gemma4:31b`(默认角色)、`gpt-oss-20b`(结构化 JSON 不稳,已退回)、`nomic-embed-text`。`qwen3.8:27b` 已放弃。
- ACE-Step 8001(启动台可拉起);Phosphene 在 Pinokio(8198)。
- ffmpeg-full:`/opt/homebrew/opt/ffmpeg-full/bin`(PATH 里的 ffmpeg 没有 libass,烧字幕/标识必须用它)。
- LongCat(已实测通过):`~/myworkspace/longcat-avatar-mlx`(自带 py3.11 venv,权重 `LongCat-Video-Avatar-1.5-q4-dmd-merged`);
  它脚本要从 HF 缓存读 whisper 特征配置(`openai/whisper-large-v3`),陈旧的 `.locks` 文件曾卡死;适配器设了离线环境变量+`PYTHONUNBUFFERED`。
  **实测:一条 draft(832×480、93 帧 = 3.1 秒、30fps、无音轨)用了 54 分钟,口型和表情会动,首帧有些过饱和/变形**——本机数字人视频极慢,是整个规划的约束(见 SERIES.md S4)。
- 生产更新流程:在开发工作树改+测+推送 `origin/main` → 主检出 `scripts/deploy.sh` → 确认没有在跑的重任务 → Ctrl-C 停 `scripts/start.sh` 再启动。需要开机自启/崩溃自拉起时再加 launchd(会改系统设置,须先问用户)。
- 重启服务:server 偶尔忽略 SIGTERM,用明确 PID 先 `kill` 再必要时 `kill -9`(从不 pkill)。

## 7. 已做 / 未做(诚实清单)

**已做且有测试**:故事全流程(analyze…compose)、规格/作业/资产台账与 worker、全部适配器(acestep comfy(音效,Stable Audio 3) mflux phosphene cosyvoice seedvc whisper h3 longcat lora_train llm assemble remote_video)、
合规/溯源、API+SSE、网页(启动台/项目/工坊/资产库/队列/AI 提示词/系统)、工坊九个模块(故事 剧本 图片 成片 LoRA 歌曲 配音 视频 转写)+流水线、
启动台、目标时长/交付档位、**生产计划(先列清单、确认后才生成)**、**从文字建项目**。
链路:剧本/故事 → 人物与场景 → 人物像/场景图 → 镜头关键帧(`image.edit`,以已做好的人物像+场景图为参考,`[workflow] keyframe_from_references`)→ 镜头视频。
`GET /api/projects/{id}/produce/plan` 列出每个待做任务(规格、模型、阶段、新/过期/已有/阻塞、估时);运行时用 `params.only=[规格id]` 只做勾选的;`workflow/plan.py`。
`POST /api/projects/from-text` 把资产库/工坊里的故事或剧本变成项目(网页「用它新建项目」)。

**没在真实模型上验证过**:LoRA 训练、h3、多数工坊模块的真实流水线、项目页的新控件、立项向导的多步表单(只有 vitest)、蒸馏关键帧在多镜头上的人脸一致性。
**系列层已做**(季/集、按集状态、批量、季大纲、系列看板、人物锁定与后续集加人、镜头分层与视频预算、剪辑卡片/淡入淡出/字幕样式),见 `docs/SERIES.md`。
**没做**:接具体云厂商(接口已留好,只缺厂商与密钥)、场景/画风锁定(S1 余下)、交叉叠化、音效合成、一集多首配乐、口型头像步骤、RIFE 补帧、EDL/FCPXML、
网页上传项目参考图、AI 改写+变更清单。**团队底座 T1 已做(2026-10-22)**:账号/角色/令牌与会话登录/接口鉴权(`auth.toml`,默认 off)/审计表(迁移 014)/CLI `user`+`audit`/网页登录、团队、审计页,见 `docs/API.md`「Team access」。**T2 审片回路已做**:镜头批注→改规格重做→前后对比→采用即解决(迁移 015,网页「审片」页),见 `docs/API.md`「Review loop」。**P1-1 造型/道具账本已做**(迁移 016,`workflow/continuity.py`,网页「造型与道具」页),见 `docs/API.md`「Continuity」。**P2-1 素材包已做**:`media/pack.py`(目录/zip 导出导入、按稳定键重映射 id、sha256 去重、预演、散文件按文件名映射、zip 路径穿越与体积限制),CLI `pack export|import|import-folder`,API `/api/projects/{id}/pack`、`/api/projects/import-pack`,网页项目页「素材包」,见 `docs/API.md`「Asset packs」。**P1-2 镜头语法已做(检查与修复,2026-10-22)**:`grammar.toml` 规则库 + `workflow/grammar.py` 纯函数校验(开场景别、跳切、景别跨度、覆盖、对白近景、说话人在画、越轴、一镜一动作、节奏)、一键修复、镜内调度 `staging` 字段、CLI `grammar`、API `/grammar`、网页「分镜校验」页;**没做**:用视觉模型看关键帧让视频提示词从真实姿态起步。**P2-2 Final Cut Pro 往返已做**(`compose/interchange.py`:FCPXML 1.10 导出,用 FCP 自带 DTD 校验通过;镜头/对白/音效/配乐分轨带 FCP 角色;改后工程与导出对比;成片回导为本集新版本),CLI `fcp export|diff|final`,API `/episodes/{n}/fcp[/diff|/final]`,网页「素材包」页下半。**未在 FCP 里实际导入验证(需要操作员打开 FCP 试一次)**。**P2-4 一部分已做:条纹/闪烁质检**(`compose/artifacts.py`,advisory,阈值保守未校准,见 `docs/WORKFLOW.md`;CLI `qc artifacts`)。**P2-3/P2-4 声音与剪辑语法已做**:环境底噪、一集多段配乐(按场景分段+交叉淡化)、无台词处配乐抬升、J/L 切、换场叠化(`xfade`),均可配置,见 `docs/WORKFLOW.md`「Sound and edit grammar」;网页「工作流」页剪辑选项里有叠化/J/L 秒数。**P3-1 多平台交付已做**:`platforms.toml`(竖屏信息流/竖屏短剧 App/横屏流媒体/电视播出,画幅、帧率、响度 I/TP/LRA、crf 或体积上限、最长时长、封面秒、预告秒;数值是常见约定不是任何平台的规范)+ `compose/delivery.py`(响度两遍线性归一、横竖屏复用 crop/pad/模糊背景、封面、预告、旁挂字幕与多语翻译,超限只提示不裁剪,携带容器 AIGC 元数据,裁切可能切掉标识/烧录字幕时提示)、新 agent `subtitle.translate`(角色 `translator`)、CLI `delivery platforms|make`(CLI 直接同步执行)、API `/api/platforms`、`POST /projects/{id}/episodes/{n}/delivery`(排队成 `compose.deliver` 后台作业,适配器 `media/adapters/delivery.py`)、`GET /projects/{id}/deliveries`、网页「交付」页(进度/取消/重试/下载),见 `docs/API.md`「Delivery per platform」。**P3-2 受限内容管理已做**(迁移 017;`content.toml` 分级策略、`content.py`、保险库 `vault/`、硬闸门、遮掩/解锁会话/审计、导出排除与确认、模型调用日志只留摘要、网页分级选择/解锁条/遮掩磁贴/成年确认),实现与未做项见 `docs/RESTRICTED_CONTENT.md`「实现状态」,运维见 `docs/RUNBOOK.md`。

## 8. 下一步(按这个顺序做)

**P0 已完成(2026-10-22)**:管线方案(`pipelines.toml`:竖屏短剧、竖屏短剧·快速关键帧、横屏短剧、电视剧、广告、电影)、作品类型(`kinds.toml`,可扩展)、立项向导+规模估算(API/CLI/网页)、素材按所用模型判过期、工坊按方案选模型、视频 preview 档回到 16+5 步与干净负向词、ComfyUI 做关键帧合成(蒸馏版 56 s vs Base 575 s)。设计与实测见 `docs/PIPELINES.md`、`docs/IMPLEMENTATION_PLAN.md`、`docs/reviews/2026-10-22_*.md`。

**接下来(用户逐阶段叫开工;细节见 `docs/IMPLEMENTATION_PLAN.md`)**:
- 收口余项:多挑 2–3 个镜头复测蒸馏关键帧的人脸一致性与视频新默认;决定 `recipes.toml`(声明但不执行,删或并入方案);Playwright 冒烟。
- P1:造型/道具/连续性账本、镜头语法层、审片回路。 P2:素材包导入导出、FCPXML(Final Cut Pro)往返、声音补齐、剪辑语法与自动质检(条纹/闪烁)。 P3:多平台规格、受限内容管理(`docs/RESTRICTED_CONTENT.md`)。
- 用户界线:受限内容只做管理(分级/隔离/加密/遮掩/审计/AIGC 标注),不写露骨提示词、不设计镜头动作、不调相关 LoRA 参数。

0. (已完成)多集改造步骤 A(`docs/SERIES.md`):迁移 007、季表、按集的阶段状态;`workflow run --param episode=2` 能接着做第 2 集而不动第 1 集。步骤 B 也已做(配乐按集,迁移 008)。步骤 C 也已做(批量跑多集/一季,进度按集)。步骤 D 季大纲也已做(`outline` 阶段,迁移 009)。系列看板(E)也已做(网页「全剧」页)。圣经的人物部分也已做(锁定 + 后续集新角色自动加入,迁移 011)。镜头分层与预算(S4)也已做:`produce` 默认只给被选中的镜头做视频,其余是关键帧+推拉(`[workflow] video_share/video_min_per_episode/video_budget_hours`,项目可覆盖,镜头可单独指定)。下一步:试播集实测、场景/画风锁定、交叉叠化/音效合成/一集多首配乐。剪辑增强(场景淡入淡出、前情/片名/未完待续卡、字幕样式)已做。
0. (已完成)「剧本→人物/场景图→关键帧」断链修复 + 生产计划确认队列 + 从文字建项目。项目级默认声音已做(`projects.settings.default_voice`,制作流程页可选)。仍未覆盖:口型头像步骤不在项目流程内、每集只用一首配乐、工坊成片模块要手工拼镜头。

1. (已完成)LongCat 实测 54 分钟/3.1 秒;工坊「故事→剧本」用 gemma4 实测通过(故事 33 秒、剧本 17 秒)。
2. (已完成)生产环境与数据迁到主检出。2026-10-22 起生产跑新代码(迁移 013 已应用,数据未清);重启只用明确 PID `kill`,从不 pkill。
3. (已完成)**云端视频接口预留**:`media/adapters/remote_video.py` 配置驱动的通用 REST 异步作业适配器,说明与完整配置示例见 `docs/CLOUD.md`;云端模型 `remote=true`,数据离开本机要 `allow_remote`(默认关),密钥只读环境变量名(`api_key_env`,不入库不入日志),任务号即 `external_id`(重启不重复付费),不占本机重任务锁、并发上限 `[runtime] max_remote_jobs`(默认 4),结果下载只对厂商域名带密钥,填了 `price_per_second` 则资产元数据带估算费用;7 个测试(`tests/test_remote_video.py` 用本地假服务)覆盖提交/轮询/下载/许可/密钥/接管/并行。没接具体厂商(不知道用哪家):给出厂商后只需填一段 TOML,差异大的写个小子类。
4. 试播集(30 秒)在真实模型上端到端跑一遍,记各环节耗时画质 → 决定镜头分层策略。
5. (已完成)系列层:迁移 007-011、批量、季大纲、看板、人物圣经、镜头分层/预算、剪辑增强。余下见 §7 的"没做"和 `docs/SERIES.md`。
6. 每个阶段:测试、i18n、文档(本文件 §7/§8 同步更新)、最后跑 `software-system-review` 再谈"可交付"。

## 9. 接手时第一步

```bash
cd <工作树>                      # 当前:/Users/pengzhan/myworkspace/projects/story-agent-system/.claude/worktrees/init-127186
git status && git log --oneline -5 && git fetch && git status -sb
.venv/bin/python -m storyagent db status && .venv/bin/python -m storyagent config show | head
.venv/bin/python -m storyagent media jobs --status running          # 有没有在跑的重任务(别重启 worker 打断它)
```
记忆文件(Claude 自动记忆):`~/.claude/projects/-Users-pengzhan-myworkspace-projects-story-agent-system/memory/`(`MEMORY.md` 是索引)。
原始计划:`~/.claude/plans/recursive-yawning-dusk.md`(R0-R7,已大体完成)。

## 10. 文件夹整理(2026-10-21,已完成)

- 仓库里只留 v2:`storyagent/ web/ tests/ docs/ scripts/` + 根文档;v1 在 tag `v1-final`,旧文档在 `docs/archive/`(含 4 月的"红果短剧工业化"改造方案 `docs/archive/hermes-plans-2026-04-29/`:当时的目标是 80 集、每集 4-6 幕、800+ 镜头,Wan2 5B 约 7 分钟/镜头——和现在的算力约束同一个问题)。
- 已**删除**(用户确认不要;v2 不引用):v1 遗留的 ChatTTS 权重 `asset/`(579M;v2 的配音是 CosyVoice)、v1 数据库残留、`output/`、旧的空代码目录、v1 的 `.env`(只有 v1 的模型名/ComfyUI/Gradio 端口)、v1 的 `.venv`(844M)、本地分支 `worktree-agent-a38072bfa0deeba37`(v1 ComfyUI 的过时提交)和已合并的旧分支。
- 现在本地只有 `main` 与工作树分支 `claude/init-127186`(都等于 `origin/main`)。主检出目录里没有虚拟环境:要在那里跑,先 `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'`;日常开发用工作树里的 `.venv`。
