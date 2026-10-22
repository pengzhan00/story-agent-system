# 缺口填补实施计划(2026-10-22)

来源:`docs/reviews/2026-10-22_director-review.md`(导演视角审查)、`docs/PIPELINES.md`、`docs/BRIEF_AND_INTERCHANGE.md`。
用户指示:**先做 P0,做成完整计划,再按计划整体完成**。P1–P3 只列阶段与验收,P0 之后再逐阶段细化与开工。

## 总体原则
- 一切可配置、可编辑、可版本化;用户可见文案全走 i18n 键;代码里没有中文字面量、绝对路径、本机地址。
- 每个阶段:代码 + 测试 + i18n + 文档,全量门禁(pytest、ruff、mypy、i18n check、web typecheck/lint/test/build)通过才算完成,逐阶段提交。
- 数据库迁移向前兼容:旧项目(无 `kind/pipeline/brief`)按默认管线方案运行,结果与迁移前一致(默认方案 = 现在的默认模型栈)。
- 重型生成(视频)走线性队列,一次一个;实验只做必要的几条。

## P0 范围与分解

### P0-A 管线方案(Pipeline Plan)—— 模型成套,不混搭
| 步 | 内容 | 落点 |
|---|---|---|
| A1 | 配置:`defaults/pipelines.toml`(取代 `recipes.toml`)。5 套方案:`vertical_drama`、`landscape_drama`、`tv_series`、`advert`、`feature_film`;每套含 `models`(每种任务的模型)、`model_params`(该方案下的参数/档位覆盖)、`workflow`(合并进 `[workflow]`)、`edit`、`delivery_profile`、`tier`、`requires`、`steps`。`[default] pipeline = "vertical_drama"` | `config/defaults/`、`config/settings.py` |
| A2 | 校验:核心任务(image.generate/video.generate/audio.tts/audio.music/compose.assemble)必须给齐;模型存在且 kind 对得上;交付档位存在;`requires` 项存在;步骤的 kind 合法。不合格 → `ConfigError`(含方案名与原因) | `config/settings.py` |
| A3 | `Settings.for_pipeline(name)`:返回带方案的有效设置(覆盖 `default_models`、合并 `model.params/tiers`、`workflow`、`delivery.edit`、默认交付档位) | `config/settings.py` |
| A4 | 新交付档位:`landscape_720`(已有 `movie_landscape_720` 改名保留别名)、`landscape_1080`、`tv_landscape_1080`、`advert_1080`、`film_scope_1080`(1920×816) | `defaults/delivery.toml` |
| A5 | 迁移 013:`projects.kind`、`projects.pipeline`、`projects.brief`(JSON) | `db/migrations/013_*.sql` |
| A6 | `ProjectStore.create(kind, pipeline, brief)`:立项即定方案与画幅(显式 > 类型推荐 > 默认);`update(pipeline=)` 换方案规则(已有图片后不能跨方向;换方案同步画幅);`PATCHABLE` 加 `kind`/`brief` | `projects.py` |
| A7 | 接线:`Ledger.settings_for(project_id)`(取项目方案的有效设置);`enqueue`、`frame_of`、`SpecStore.build_job`、工作流 `Ctx`/`WorkflowEngine`、`plan.py`、`flow.py`、`series.py`、`routes_workflow`、`workshop`(先选方案)、`healthcheck`(按方案体检)、CLI `config show` | 见各文件 |
| A8 | 素材"按模型判过期":素材所用作业的 `model_ref` 不在当前方案允许的模型内 → 标过期(导入素材不判);`up_to_date`/生产计划/流程图一致 | `workflow/media.py`、`plan.py` |
| A9 | 工坊保留"单独试用某个模型"入口,标为"试用,不进项目"(沿用用户倾向);选方案用该方案的模型 | `workshop.py`、web |

### P0-B 立项向导(Brief)—— 作品类型 + 结构 + 时长 + 估算
| 步 | 内容 | 落点 |
|---|---|---|
| B1 | 配置:`defaults/kinds.toml`(**可扩展**,用户目录可覆盖/新增):连续短剧、单本短剧、电视剧、广告、电影、自定义;每个含推荐方案、是否分季分集、集数范围/默认、单集时长范围/默认、立项要问的字段、给 `analyze` 的类型指引段落(英文提示文本放配置里) | `config/defaults/kinds.toml` |
| B2 | `storyagent/brief.py`:`resolve_brief(settings, kind, overrides)`(默认值 + 范围校验 + 派生总时长)、`estimate(settings, ledger, brief, pipeline)`(按平均镜长算镜头数,按任务类型与历史/兜底耗时估算各类素材数与算力小时,对照 `video_budget_hours`) | `brief.py` |
| B3 | 创建项目一步完成:写 `kind/pipeline/brief`、画幅、`settings.target_sec`,建 `seasons` 行(计划集数);大纲/剧本阶段读它 | `projects.py`、`story.py` |
| B4 | `analyze` 提示词带类型指引(`$guidance`) | `agents/specs/director.analyze.toml`、`workflow/stages.py` |
| B5 | API:`GET /api/meta` 增加 `kinds`、`pipelines`;`POST /api/projects/estimate`;创建体增加 `kind/pipeline/seasons/episodes_per_season/episode_sec/audience/platform`;项目输出带 `kind/pipeline/brief` | `api/` |
| B6 | CLI:`project create --kind --pipeline --seasons --episodes --episode-sec`、`project estimate`、`config show` 列方案/类型 | `cli.py` |
| B7 | Web:新建项目改为向导(创意 → 类型 → 方案(自动带出,可改) → 结构与时长 → 规模估算 → 创建);项目页显示类型/方案/结构;换方案入口(带方向规则提示) | `web/src/...` |
| B8 | i18n:`brief.kind.*`、`pipeline.*`、`brief.estimate.*`、API 错误码键、CLI 文案、web 文案 | `locales/`、`web` |

### P0-C 竖屏视频横纹/走路飘 —— 参数设计
| 步 | 内容 |
|---|---|
| C1 | **单因素对比实验**(同镜头、同关键帧、同种子,只改一个因素,项目外的作业,不污染项目素材):A 现状 12+4 步、B 16+5 步、C 16+5 + `fit_720p`、D 清理负向词 + 步态/落脚/镜头描述、E 73 帧。线性队列逐条跑,抽帧对比 |
| C2 | 依结果改:`models.toml` 的档位与默认、各方案的 `model_params`、负向提示词(去掉武侠残留,加条纹/闪烁/穿帮项)、`shot.visual` 提示词里的动作/步态/机位写法、单镜帧数上限 |
| C3 | 记录:结论与数据写入 `docs/reviews/`,并更新 `PIPELINES.md` 的待调参一节 |
| C4 | (若实验显示后处理有效)评估去条纹滤镜作为 compose 可选项;否则不做 |

### P0 验收
1. 新项目一次向导即定:类型、方案、画幅、集数/季数、单集时长,并显示规模估算;创建后系列看板已有空集位。
2. 同一项目所有任务的模型都来自它的方案;改方案 = 整套换;素材所用模型与方案不符 → 标过期,不静默混用。
3. 旧项目(生产库 project 1/2)迁移后行为不变(用默认方案),测试覆盖。
4. 5 套方案 + 类型都能加载,加载校验有负面测试;用户目录可新增类型和方案。
5. 视频实验有对比数据与结论,默认参数按结论改并记录。
6. 全量门禁通过;提交;生产库迁移到 013。

## 后续阶段(P0 之后,逐阶段细化再开工)
| 阶段 | 内容 | 验收要点 |
|---|---|---|
| P1-1 | 造型/道具/连续性账本:服装表(按集/场)、道具实体、上一镜状态传递;场景/画风锁定 | 同一人物跨镜服装一致,可改可锁;镜头提示词自动带入 |
| P1-2 | 镜头语法层:覆盖规则(建立→中→特写→反应)、景别跳切规则、轴线/屏幕方向、镜内调度字段(起止姿态/步态/机位/焦距) | 分镜校验报告;违例列出并可修 |
| P1-3 | 审片回路:镜头批注 → 改写提示词/参数 → 重做 → 前后对比 | 失败镜头 ≤2 次点击进入重做 |
| P2-1 | 素材包导入/导出(目录+zip、dry-run、去重、散文件映射) | 导出再导入往返一致 |
| P2-2 | FCPXML 往返(FCP 角色分轨、相对媒体、成片回导) | 在本机 FCP 实际导入验证 |
| P2-3 | 声音补齐:环境底噪、一集多段配乐、场景混音 | |
| P2-4 | 剪辑语法(J/L cut、叠化)、成片调色、自动质检扩展(条纹/闪烁) | |
| P3-1 | 多平台响度规格、封面/预告、横竖屏复用、多语字幕 | |
| P3-2 | 受限内容(成人向等分级)的资源管理:分级、隔离存放、加密、遮掩、解锁会话、导出排除、硬闸门、审计(设计见 `docs/RESTRICTED_CONTENT.md`) | 未解锁取不到原文件;导出默认排除;日志无提示词原文;未成年/无同意被拒绝 |

## 风险与对策
- 接线面广(8+ 处用到默认模型/工作流设置):统一经 `Ledger.settings_for`/`Settings.for_pipeline`,不在各处复制逻辑;用"项目换方案 → 各处取值变化"的测试逐处覆盖。
- 迁移影响生产库:先在生产库副本上迁移验证,再迁移生产库;`db migrate` 本身会先做快照。
- 视频实验占用算力:线性队列、内存护栏已在;同时不再开其他重任务。
- 估算不准:用台账实测耗时(中位数),无历史用兜底并在界面标"粗估"。
