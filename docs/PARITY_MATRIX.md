# Legacy → v2 功能对照表(Parity Matrix)

> 目的:重写不能静默丢功能。旧代码已删除(见文末),本表记录每一项旧功能在 v2 的结局,供查证。
> 状态:✅ 已在 v2 实现并有测试 · 🔀 合并/重构(能力保留,形态变了)· ⛔ 明确放弃(附理由)
> 来源:旧 `ui/app.py`、`core/*`、`agents/*`、`pipelines/*` 实测盘点(2026-10-20);核对日期 2026-10-21。
> 旧代码完整保留在 git tag `v1-final`(`git show v1-final:path`)。

## A. 创作流程

| # | 旧功能(位置) | v2 去向 | 状态 |
|---|---|---|---|
| A1 | 一键全流程 `run_pipeline_generator` | `workflow/` 九阶段(策划→角色→剧本→场景→配乐→分镜→规格→素材→成片),可恢复,实时进度条 + ETA(Web「制作流程」、`storyagent workflow run`) | ✅ |
| A2 | 分阶段运行 `run_stage_*` | 每个阶段「只做这一步」(Web / `--stage`);下游自动标记过期 | ✅ |
| A3 | 导演/编剧/角色/场景/美术/作曲/音效 agent 提示词 | `agents/specs/*.toml`(统一 AgentSpec,提示词在 TOML,可在配置目录覆盖) | ✅ |
| A4 | 概念发现 `concept_finder`(Bing 抓取)+ 批量概念队列 | 联网抓取违背离线原则;改为用户直接写前提或导入已有文本(`--param source_text=…` → `screenwriter.adapt`) | ⛔ |
| A5 | 类型/基调多选、`_home_type_defaults` 预设 | 导演 agent 从前提推断类型/基调/风格;不再维护预设表 | 🔀 |
| A6 | 各阶段模型选择 | `llm.toml` 按角色指定后端与模型(ollama / openai 兼容 / llama.cpp),环境变量与项目级可覆盖 | ✅ |
| A7 | `reviewer` agent(旧流水线从未调用) | 按需调用:Web「审查剧本」、`workflow review`、`POST /api/projects/{id}/review`;不改任何数据 | ✅ |
| A8 | `voice_actor` agent(旧流水线未调用) | 台词→`dialogue.line` 规格(含情绪)→ TTS 作业;不再需要独立 agent | 🔀 |
| A9 | `render_scheduler` agent / `task_queue` agent 间通信(未用) | `media_jobs` 台账 + worker 取代 | ⛔ |
| A10 | 每个 agent 的 CLI(`agents/*/main.py` ×9) | 阶段级 CLI `workflow run --stage`;不再提供单 agent CLI | ⛔ |
| A11 | AI 辅助编辑 + 变更清单 + 回滚 | 人工编辑(Web「故事」「创作」)+ 规格版本回退/锁定/diff;AI 改写 + 变更清单未携带 | 🔀 |
| A12 | Prompt 模板库 `prompt_templates` 表 | AgentSpec TOML 即模板;模型提示词方言在 `models.toml prompt_profile` | 🔀 |

## B. 分镜与审核

| # | 旧功能 | v2 去向 | 状态 |
|---|---|---|---|
| B1 | 分镜表 / 表单 / JSON 编辑 | Web「故事」页:逐镜头编辑对白、画面、镜头语言;已编辑的台词无需 force 即进入规格 | ✅ |
| B2 | 分镜审核状态机 approve/reject/rework/lock + `shot_reviews` | 锁定/解锁(锁定的分镜重排时不动)+ 候选版本「采用」;不再有单独的审核状态表 | 🔀 |
| B3 | 单镜头工坊 + 参考图/视频上传 | 创作页单镜头规格编辑与候选;上传:`POST /api/projects/{id}/uploads`、`/api/library/upload`、CLI `asset import`、Web 角色声音样本上传;参考图 Web 上传入口未做(API/CLI 可用) | 🔀 |
| B4 | 字幕修订表 + 字幕 Tab | 字幕来自对白文本,改台词即改字幕(规格有版本);SRT 随成片交付;无独立修订表 | 🔀 |
| B5 | 项目删除 / 重命名 | Web 重命名 + 回收站(软删除/恢复,运行中的项目不能删);文件与历史保留 | ✅ |

## C. 媒体生产

| # | 旧功能 | v2 去向 | 状态 |
|---|---|---|---|
| C1 | LTX t2v/i2v 管线(ComfyUI 工作流) | 视频由 `phosphene`(LTX 2.5 原生)、`h3`(视频+音频)、`longcat`(数字人)适配器提供;**不再有 ComfyUI 适配器**,旧 ComfyUI 工作流 JSON 留在 `v1-final` | 🔀 |
| C2 | 预览/冒烟渲染、`quality_tier` | `QualityTier` draft/preview/final,按模型分档参数 | ✅ |
| C3 | 管线回退链 + `allow_fallback` + stub 兜底 | 不静默降级:失败即报错并给出下一步;produce 报告「跳过/失败」原因 | ✅ |
| C4 | Wan2.* 管线类(配置未启用) | 未验证、未启用 | ⛔ |
| C5 | TTS 链 ChatTTS→edge→bark→kokoro→pyttsx3 | CosyVoice 声音克隆(+ Seed-VC 转换);edge-tts 需联网、其余未测,不携带 | 🔀 |
| C6 | ACE-Step BGM/主题曲(直连 + ComfyUI 回退 + ffmpeg 兜底) | `acestep` 适配器(turbo / XL-SFT);回退链不携带 | ✅ |
| C7 | SFX(ffmpeg 合成) | 无实测可用的音效模型:音效规格保存、produce 报告「跳过(无可用模型)」,成片无音效 | ⛔ |
| C8 | LoRA 注入、ControlNet、InstantID | LoRA:角色 `lora_ref` → 关键帧 `loras` 参数,`train.lora` 训练适配器 ✅;ControlNet / InstantID 未携带 | 🔀 |
| C9 | RIFE 补帧 16→24fps | 未实现(无模型登记);成片按配置帧率重编码 | ⛔ |
| C10 | 快速做视频 `quick_video` | Web「一键做完整部」:一句话 → 全部阶段 → 成片 | ✅ |
| C11 | 批量渲染 `BatchRenderer` | worker 队列 + 单点重试 + 取消 + 重启接管 | ✅ |
| C12 | 停止渲染 / 强杀 ComfyUI | `cancel`(取消作业/运行);**不再强杀进程**(`pkill` 被守卫测试禁止) | 🔀 |
| C13 | 渲染后 QC(黑帧/冻结/分辨率/音轨) | `compose/qc.py`:文件、时长、分辨率、画幅、音轨、音量、响度(ebur128)、黑屏、静止、单集时长(仅提示) | ✅ |
| C14 | 能力矩阵 / 管线就绪检查 | `storyagent media healthcheck`、Web「系统」页 | ✅ |
| C15 | ComfyUI 模型浏览/下载 | healthcheck 指出缺什么;**下载不自动执行** | 🔀 |

## D. 成片与交付

| # | 旧功能 | v2 去向 | 状态 |
|---|---|---|---|
| D1 | `compose_shot`(TTS+BGM+SFX 混音 + 字幕) | `compose/timeline.py`:先量语音再排时间轴;BGM 随人声闪避;loudnorm -16 LUFS | ✅ |
| D2 | `compose_episode`(crossfade / concat) | ffmpeg 逐镜规范化后硬切拼接(无转场);AIGC 显式标识/片尾卡、隐式元数据 | ✅ |
| D3 | `export_manifests`、`delivery_packages` | `storyagent export episode` / `GET /api/projects/{id}/episodes/{n}/package`:成片 + SRT + 分轨 + manifest + 脱敏溯源;EDL/FCPXML 未做 | 🔀 |
| D4 | 断点续跑 | worker 租约/心跳/重启接管;workflow 每阶段幂等、可续 | ✅ |
| D5 | 资产版本 `asset_versions` | `media_assets` 版本 + `current` 指针 + 候选「采用」 | ✅ |

## E. 运维与入口

| # | 旧功能 | v2 去向 | 状态 |
|---|---|---|---|
| E1 | `start.sh` 启动/停止/重启/诊断 | `scripts/start.sh`(只启动本项目的 serve + worker,Ctrl-C 只停自己的子进程)+ `media healthcheck`;**不 pkill**;外部服务只在用户于「启动台」点击(或 `ops launch`)时按 `[launch.*]` 配置启动,从不自动拉起、从不停止 | 🔀 |
| E2 | `setup.sh` | `pyproject.toml` + README「快速开始」 | ✅ |
| E3 | `main.py --render/--workshop/--cli/--check` | `storyagent serve | worker | workflow | media | export …` | ✅ |
| E4 | 独立渲染服务 UI(`render_app.py`) | 并入 Web「任务队列」+ worker | ⛔ |
| E5 | 系统 Tab(状态、日志、试听) | Web「系统」页 + 素材播放器 | ✅ |
| E6 | 旧 DB 数据 | 决策:不迁移,v2 库全新;`db import-legacy` 保留为可选只读工具 | ✅ |

## 已知缺口(诚实清单)

音效合成(C7)、RIFE 补帧(C9)、EDL/FCPXML(D3)、转场(D2)、参考图 Web 上传入口(B3)、AI 改写 + 变更清单(A11)、FastVideo 适配器(权重未下载、接口未核实)、longcat 尚未 live 验证(仅桩与契约测试;Phosphene、h3 视频已于 2026-10-21 实机验证)。
