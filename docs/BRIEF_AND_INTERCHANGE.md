# 立项(目标制定)与素材/剪辑交换 —— 缺口与设计记录(2026-10-22,**已定方案,尚未实现**)

用户在试播验证后指出三个缺口:

1. 现在"一句话就开做",但**做短剧、广告还是电影没有选**,**分季分集没安排**,**时长没计划**——目标制定这一段不完整。
2. **素材的批量导入/导出**缺失(现在只有单个文件上传、逐个入库)。
3. **剪辑有时必须导出到专业工具里做**(很多功能在剪辑软件里才有),所以要能导出工程、再把成果导回来。

## 现状核对(代码实测)

| 项 | 现在有什么 | 缺什么 |
|---|---|---|
| 立项 | `POST /projects`:名称/类型(genre)/简介/内容语言/画幅(交付档位) | 没有"作品类型";集数/季数靠运行 `outline` 时再填 `planned_episodes`;时长只有 `target_sec`(单集)可选,没有总时长/总预算 |
| 季集 | `seasons`(计划集数、目标时长覆盖)、`outline` 阶段、系列看板 | 没有立项时的"总体安排"一步,季集计划是事后才有 |
| 素材导入 | 单文件 `asset import` / `POST /projects/{id}/uploads` / 素材库 `library add` | 没有整包/整目录批量导入,没有清单(manifest)、去重、命名映射 |
| 素材导出 | `export episode` 只导**一集成片包**(mp4、srt、配音与配乐分轨、provenance、manifest) | 没有项目级整包(全部素材+规格+故事数据)、没有按镜头的素材包 |
| 剪辑交换 | 时间轴 EDL 是内部 JSON(`compose/timeline.py`) | 没有 FCPXML/EDL/OTIO 导出,没有成片工程回导 |

## 一、立项向导(Brief)—— 目标制定

**核心:立项时一次定下"作品类型 + 结构 + 时长",之后各阶段都据此取值。** 它与 `docs/PIPELINES.md` 的管线方案是同一件事的两半:
作品类型选定 → 推荐(并锁定)一整套管线方案(画幅、模型、质量档、视频占比、剪辑项);立项再补上方案没有的**结构与时长**。

### 立项要定的内容(存进项目,可随时改,有版本)

| 项 | 字段 | 说明 |
|---|---|---|
| 作品类型 | `projects.kind`:`drama_series` 连续短剧 / `drama_single` 单本短剧 / `tv_series` 电视剧 / `advert` 广告 / `feature_film` 电影 / `custom` | 决定默认管线方案、集长范围、是否分季分集、是否有片头片尾卡。选了类型就带出推荐方案(用户已定"选模型带出管线") |
| 结构 | `seasons`、每季 `planned_episodes`(立项时填,建 `seasons` 行) | 广告/单本/电影 = 1 季 1 集,界面不显示季集;连续剧/电视剧必填 |
| 时长 | 单集目标 `target_sec`(已有)+ 全片总时长 `total_target_sec`(可算:集数×单集) | 每集时长可由季/集覆盖(已有优先级:运行参数 > 集 > 季 > 项目 > 档位);给出"这个规模大约要多少算力/几天"的估算(用台账实测耗时) |
| 预算 | `video_budget_hours`(已有)+ 项目总算力预算 | 立项就显示估算与预算对比,超了就提示降视频占比或缩集数 |
| 成片平台 | 画幅(由管线方案定)+ 是否需要 AIGC 标识(已有合规开关) | |

### 流程

1. **立项向导**(网页/CLI/API 同一个入口):一句创意 → 选作品类型(带出推荐管线方案与默认集数/时长)→ 确认集数、季数、单集时长 → 看到规模估算(镜头数、各类素材数、算力小时)→ 创建。
2. 创建时**写好 `seasons` 行和计划集数**,系列看板立刻显示全剧的空集位。
3. 大纲阶段按立项的结构写(不再事后问集数);改结构(加一季/加几集/改时长)走"修改立项"并提示哪些阶段因此过期(沿用 `DOWNSTREAM`)。
4. `analyze` 阶段的提示词带上作品类型(广告要卖点和号召、电影要三幕结构、短剧要钩子)——提示词在 TOML 里按类型分段,不写在代码里。

### 数据与接线

- 迁移 013(与管线方案同批):`projects.kind`、`projects.pipeline`、`projects.brief`(JSON:总时长、受众、平台等自由补充)。
- `storyagent/brief.py`:`kinds`(从 `pipelines.toml` 的 `kind` 字段读,不在代码里列)、`estimate(settings, brief, pipeline)`(镜头数≈时长/平均镜长,各类任务数×台账估时)。
- API:`GET /api/meta` 增加作品类型列表(含推荐方案、默认集数/时长范围)、`POST /projects/estimate`(不建项目先估算)、项目创建体增加 `kind/seasons/episodes_per_season/target_sec`。
- i18n 键:`brief.kind.*`(+`.note`)、`brief.estimate.*`、`brief.error.*`、`cli.project.created_with_plan`。

## 二、素材批量导入/导出(Asset Pack)

**统一格式:素材包 = 一个目录(或 zip),根目录有 `pack.json` 清单。** 导出和导入用同一个格式,所以能备份、能在两台机器间搬、也能给外部工具喂料。

```
pack/
  pack.json            # schema=storyagent.pack.v1;项目名、类型、画幅、导出时间、内容范围、每个文件的 sha256/bytes/类型
  assets/<kind>/<镜头或槽位>/<文件>     # 只含 current 版本(可选含历史候选)
  specs/*.json         # 规格(提示词、参数、种子;含版本链)
  story/*.json         # 季/集/人物/场景/镜头/台词/音乐(文字数据,便于外部编辑)
  library/             # 引用到的素材库条目(声线、参考图)
  provenance.json      # 同交付包:来源与许可(去掉提示词和路径)
```

### 导出
- `storyagent pack export --project N [--scope all|assets|story] [--episodes 1-3] [--include-history] --out DIR|file.zip`;API `POST /projects/{id}/pack`(后台作业,大包不阻塞)。
- 范围可选:只导素材、只导文字数据、按集、按镜头。导出前校验文件存在且 sha256 对得上,缺失的列入清单警告。
- 导出**不含**数据库文件和 `$STORY_AGENT_HOME` 绝对路径。

### 导入
- `storyagent pack import DIR|zip [--into PROJECT] [--dry-run] [--on-conflict skip|new_version|replace]`;API 先 `dry-run` 出报告(新增几个、重复几个、冲突几个、不识别几个)再确认。
- **目录/散文件批量导入**:没有 `pack.json` 的普通文件夹也能导,按文件名规则映射(`shot_003.mp4` → 镜头 3 的 `video.generate` 槽、`角色名.png` → 角色形象、`bgm*.wav` → 配乐),规则在 `config` 里可改;映射不了的进"待归类"列表而不是丢弃。
- **去重**:按 sha256 查已有素材,同文件不重复入库;入库一律走现有 `import_file`(ffprobe 实测、登记来源 `imported`)。
- 导入的素材 provenance 记为"外部导入",合规标识规则照常(声音需授权说明)。

### 与现有功能的关系
导入的素材和生成的素材是同一种 `media_assets`(每个槽只一个 current,旧的留作候选),所以导入一个镜头视频等于"采用一个候选";上游改了会按依赖时间判过期(沿用)。

## 三、剪辑交换(外部剪辑软件往返)

**目的:自动化做到"可看的初剪",精修在专业工具里做,再把成片或工程成果带回来。**

### 导出给剪辑软件
- `storyagent export timeline --project N --episode E --format fcpxml|edl|otio|premiere_xml --out DIR`:
  按已有的时间轴(EDL JSON,含镜头顺序、入出点、台词/配乐/音效分轨、字幕)生成工程文件,**媒体用相对路径引用同目录的素材副本**(`media/` 下的视频、静图、配音、配乐、音效),打开即用,不用重新链接。
- 先做 **FCPXML**(Final Cut Pro/DaVinci Resolve/Premiere 都能读)和 **CMX3600 EDL**(最通用,只含画面剪切),再做 **OTIO**(OpenTimelineIO,一个格式转多家);Premiere XML 按需。具体哪一个以用户实际使用的剪辑软件为准(**待用户确认**:你主要用哪个软件?)。
- 音频分轨:画面轨、对白轨(每句一个片段,带角色名)、配乐轨、音效轨;字幕轨导出 SRT 并随包带上。
- 导出包 = 现有交付包(成片、SRT、分轨 stems、provenance、manifest)+ 时间轴工程 + 素材副本。**时间轴里每个片段带 `asset:<id>` 标记**,回导时能对上。

### 从剪辑软件带回来
- 两种回导:
  1. **成片回导**:把精修后的 mp4 导入为该集的 `compose.assemble` 资产(来源 `external_edit`,记录"由哪一版时间轴导出"),之后交付包/质检/AIGC 标识处理照常对它做(标识可选择叠加到外部成片上)。
  2. **素材回导**:在软件里做的单个素材(调色后的镜头、修过的配音)按"采用候选"导回对应槽位(走素材包导入)。
- 工程文件本身不回读(格式太多、风险大);回的是**成果**,保持台账可溯源。

### 取舍
- 不做"在系统里重做一个剪辑软件",只做:自动初剪 + 干净的往返。
- 外部成片不再由系统控制:质检照做、结果标注"外部编辑",标识叠加可选。

## 四、实施顺序(等用户叫"开始"再做,先不动代码)

和管线方案放在同一批,按依赖排:

1. **迁移 013 + `pipelines.toml`**(`kind`/`pipeline`/`brief`;已在 `PIPELINES.md`)
2. **立项向导**(`brief.py`、估算、API/CLI/网页向导、大纲读立项结构)
3. **素材包**(`media/pack.py`:导出 → 导入 dry-run → 导入 → 散文件映射;CLI/API/网页)
4. **剪辑交换**(`compose/interchange.py`:FCPXML + EDL → 成片回导 → OTIO)
5. 每步:测试(含导出再导入往返一致性、sha256 去重、dry-run 不写库、时间轴导出的片段数/时长与 EDL 对账)、i18n、文档(`API.md`、`USER_GUIDE.md`、`RUNBOOK.md`)、过 `software-system-review`。

## 已定(用户 2026-10-22 回复)

1. **剪辑软件 = Final Cut Pro** → 剪辑往返**先做 FCPXML**(目标版本以本机 FCP 实际能导入的为准,实现时用样例工程验证);EDL 作通用备选,OTIO/Premiere XML 后排。导出时媒体帧率、时间码、音频分轨(对白/配乐/音效为 FCP 的角色 roles)按 FCP 约定写。
2. **素材包目录和 zip 都支持**:导出可选 `--format dir|zip`,导入自动识别(目录、zip、无清单的散文件夹)。zip 导入要防路径穿越(`..`、绝对路径)并限制解压大小。
3. **作品类型可扩展、可配置**:类型不写死在代码里,放进配置 `kinds`(随 `pipelines.toml` 或单独 `kinds.toml`,用户目录覆盖程序自带):
   每个类型是一条配置——`label_key`/`note_key`(i18n)、推荐管线方案、是否分季分集、默认集数范围、默认单集时长范围、是否带片头片尾卡、立项向导里要问哪些字段、`analyze` 提示词的类型段落(TOML 里)。新增类型 = 加一段配置 + 两条 i18n 键,不改代码;内置六种(连续短剧、单本短剧、电视剧、广告、电影)+ `custom`,用户可复制改名。加载时校验:引用的管线方案存在、范围合法。
