# 交接手册:真相源、数据结构、架构、如何接着做

> 给"换一个人(或换一个 AI 会话)接着做"用。读完这一份,加上 `CLAUDE.md`,就能继续。
> 最后更新:2026-10-21,main 分支 `f386a2d` 之后。数值/行为以代码为准;这里写的是**在哪里找真相**。

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
    adapters/  acestep mflux phosphene cosyvoice seedvc whisper h3 longcat lora_train llm assemble remote_video fake(+_http/_subprocess 公共件)
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

### 3.2 表(001-006)

| 表 | 关键列 |
|---|---|
| projects | id slug name description genre format aspect_ratio delivery_profile content_language status **settings(JSON)** |
| scripts | project_id episode_no title synopsis acts(JSON) total_scenes |
| characters | project_id name role appearance personality voice_profile voice_ref_audio/text voice_ref_consent lora_ref ref_images |
| scenes(场景设计) / music_themes / sound_effects | 各自属于 project;music_themes 有 kind(bgm/theme) lyrics duration_sec scene_ref |
| episodes | project_id number title summary status,UNIQUE(project,number) |
| shots | project_id episode_id act/scene/shot_number dialogue(JSON) narration render_payload(shot.v3 JSON) locked status |
| gen_specs / gen_spec_locks | subject_type subject_id slot version kind model_ref prompt negative_prompt text_payload params seed refs depends_on author |
| media_jobs | kind provider model_ref tier status inputs params seed idempotency_key heavy est_memory_gib lease_* external_id progress error_* attempt **depends_on pipeline_id pipeline_step** |
| media_assets | job_id project_id shot_id spec_id/version kind slot path sha256 duration_sec width height fps has_audio metadata provenance version is_current trashed_at |
| job_events | job_id ts level event data |
| library_items | role name asset_id description tags meta source_asset_id |
| workflow_stages / workflow_runs / workflow_data | 见 §1 |
| workshop_presets | kind model_ref tier params |
| generation_logs | project_id agent model prompt response tokens duration_ms system label |

### 3.3 配置分层(低→高,后者覆盖)

出厂 `storyagent/config/defaults/*.toml` < `$STORY_AGENT_HOME/config/*.toml` < 环境变量 < `projects.settings` < 作业/规格参数。
文件:`services`(服务地址、工具路径、`[launch.*]`、`[runtime]`、`[api]`、`[healthcheck]`)、`models`(模型注册表+`[defaults]`+参数分档)、
`delivery`(交付档位、`[pacing]`、`[mix]`、`[subtitles]`、`[qc]`)、`compliance`、`recipes`(未被工作流使用)、`llm`(后端与角色)。
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

**已做且有测试**:故事全流程(analyze…compose)、规格/作业/资产台账与 worker、全部适配器(acestep mflux phosphene cosyvoice seedvc whisper h3 longcat lora_train llm assemble remote_video)、
合规/溯源、API+SSE、网页(启动台/项目/工坊/资产库/队列/AI 提示词/系统)、工坊九个模块(故事 剧本 图片 成片 LoRA 歌曲 配音 视频 转写)+流水线、
启动台、目标时长/交付档位。

**没在真实模型上验证过**:LoRA 训练、h3、多数工坊模块的真实流水线、项目页的新控件。
**没做**:接具体云厂商(接口已留好,只缺厂商与密钥)、系列层(S1-S3,S7,S8)、镜头分层/预算(S4)、剪辑(转场/回顾/悬念卡/字幕样式/音效合成,S5)、RIFE 补帧、EDL/FCPXML、
网页上传项目参考图、AI 改写+变更清单、API 鉴权(目前仅本机回环)、审计表。

## 8. 下一步(按这个顺序做)

1. (已完成)LongCat 实测 54 分钟/3.1 秒;工坊「故事→剧本」用 gemma4 实测通过(故事 33 秒、剧本 17 秒)。
2. (已完成)生产环境与数据迁到主检出。
3. (已完成)**云端视频接口预留**:`media/adapters/remote_video.py` 配置驱动的通用 REST 异步作业适配器,说明与完整配置示例见 `docs/CLOUD.md`;云端模型 `remote=true`,数据离开本机要 `allow_remote`(默认关),密钥只读环境变量名(`api_key_env`,不入库不入日志),任务号即 `external_id`(重启不重复付费),不占本机重任务锁、并发上限 `[runtime] max_remote_jobs`(默认 4),结果下载只对厂商域名带密钥,填了 `price_per_second` 则资产元数据带估算费用;7 个测试(`tests/test_remote_video.py` 用本地假服务)覆盖提交/轮询/下载/许可/密钥/接管/并行。没接具体厂商(不知道用哪家):给出厂商后只需填一段 TOML,差异大的写个小子类。
4. 试播集(30 秒)在真实模型上端到端跑一遍,记各环节耗时画质 → 决定镜头分层策略。
5. 系列层:迁移 007(阶段状态加 episode 维度)→ 全剧圣经+分集大纲 → 批量跑多集 → 镜头分层/预算 → 剪辑增强 → 系列看板。
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
