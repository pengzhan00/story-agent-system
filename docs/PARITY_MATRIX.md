# Legacy → v2 功能对照表(Parity Matrix)

> 目的:重写不能静默丢功能。R7 删除旧代码的**前置门禁**:本表每一行都必须是 ✅(已在 v2 实现并有测试)或 ⛔(明确决定放弃,附理由)。
> 状态:⬜ 未开始 · 🔄 进行中 · ✅ 完成 · ⛔ 放弃(需理由)· 🔀 合并/重构
> 来源:旧 `ui/app.py`、`core/*`、`agents/*`、`pipelines/*` 实测盘点(2026-10-20)。

## A. 创作流程

| # | 旧功能(位置) | v2 去向 | 里程碑 | 状态 |
|---|---|---|---|---|
| A1 | 一键全流程 `run_pipeline_generator`(orchestrator) | `workflow/` 阶段 DAG,可恢复 | R3 | ⬜ |
| A2 | 分阶段运行 `run_stage_*`(story/chars/scenes/art-music-sfx/shots) | workflow 单阶段重跑 | R3 | ⬜ |
| A3 | 导演分析 / 编剧 / 角色 / 场景 / 美术 / 作曲 / 音效 agent 提示词 | `agents/specs`(统一 AgentSpec) | R3 | ⬜ |
| A4 | 概念发现 `concept_finder`(含 Bing 抓取) + 批量概念队列 | `agents/specs` + 批量队列;**联网抓取默认关闭**(离线原则) | R3 | ⬜ |
| A5 | 类型/基调多选、`_home_type_defaults` 类型预设 | 配置化预设 + i18n 标签 | R4 | ⬜ |
| A6 | 各阶段模型选择(`resolve_model_profile`) | `llm/` 阶段模型档 + `models.toml` | R3 | ⬜ |
| A7 | `reviewer` agent(剧本/角色/场景/项目审查) — **旧流水线从未调用** | 决定:接入 QC 阶段(语义审查)或放弃 | R3 | ⬜ 待决 |
| A8 | `voice_actor` agent(台词计划)— 旧流水线未调用 | 并入 `audio.tts` 配音计划 | R3 | ⬜ |
| A9 | `render_scheduler` agent / `task_queue` 的 agent 间通信 — 旧流水线未用 | ⛔ 由 `media_jobs` + worker 取代 | R1 | ⛔ |
| A10 | 每个 agent 的 CLI(`agents/*/main.py` ×9) | 统一 `storyagent agent run <name> --action …` | R3 | ⬜ |
| A11 | AI 辅助编辑 + 变更清单 + 回滚(`edit_agent`、`change_manifest`、`edit_panel`) | `workflow` 编辑操作 + 台账版本回滚 | R4 | ⬜ |
| A12 | Prompt 模板库(`prompt_templates` 表) | 并入 AgentSpec / 模型 prompt 方言 | R3 | ⬜ |

## B. 分镜与审核

| # | 旧功能 | v2 去向 | 里程碑 | 状态 |
|---|---|---|---|---|
| B1 | 分镜表 / 表单编辑 / JSON 编辑(`build_shot_table`、`save_shot_form`…) | UI「分镜」页 | R4 | ⬜ |
| B2 | 分镜审核状态机:approve / reject / rework / rerender / lock / unlock(`review_shot_action`) + `shot_reviews` 表 | `shot_reviews` + 候选版本「采用」 | R3/R4 | ⬜ |
| B3 | 分镜工坊单镜头编辑 + 参考图/视频上传(`ui/workshop.py`) | UI「分镜」页内联;上传走校验 | R4 | ⬜ |
| B4 | 字幕修订 `subtitle_revisions` + 字幕编辑 Tab | 字幕版本表 + UI | R4/R5 | ⬜ |
| B5 | 项目删除 / 重命名 | 软删除 + 回收站 + 审计 | R4 | ⬜ |

## C. 媒体生产

| # | 旧功能 | v2 去向 | 里程碑 | 状态 |
|---|---|---|---|---|
| C1 | LTX t2v / i2v / t2v_only 管线(`render_pipeline`) | `comfy` 适配器 + API 格式工作流 | R2 | ⬜ |
| C2 | 预览/冒烟渲染(`smoke`、`preview_mode`、`quality_tier`) | `QualityTier` draft/preview/final | R1/R2 | ⬜ |
| C3 | 管线回退链 + `allow_fallback` + stub 兜底 | broker 显式降级策略(默认不静默降级) | R1 | ⬜ |
| C4 | Wan2.* 管线类(6 个,配置未启用) | ⛔ 放弃(未在配置中,未验证);需要时按适配器新增 | — | ⛔ |
| C5 | TTS 链 ChatTTS→edge→bark→kokoro→pyttsx3 | `audio.tts` provider 回退链;CosyVoice 为首选 | R2 | ⬜ |
| C6 | ACE-Step BGM/主题曲(直连 + ComfyUI 回退 + ffmpeg 兜底) | `acestep` 适配器;ffmpeg 兜底保留为 `audio.music` 回退 | R2 | ⬜ |
| C7 | SFX(仅 ffmpeg 合成) | `audio.sfx`:先保留 ffmpeg 合成,再评估模型(ComfyUI 已有 stable_audio) | R2+ | ⬜ |
| C8 | LoRA 注入(角色/场景 `lora_ref`)、ControlNet、InstantID | 角色一致性配置 + comfy 工作流参数 | R5 | ⬜ |
| C9 | RIFE 补帧(`interpolate_fps_rife`)16→24fps | `video.enhance` 适配器 | R5 | ⬜ |
| C10 | 快速做视频(`quick_video`,首页 Tab 1) | UI 首页「快速成片」:单提示词→draft→final | R4 | ⬜ |
| C11 | 批量渲染 `BatchRenderer`(重试、按场景) | broker 队列 + 单点重试 | R1 | ⬜ |
| C12 | 停止渲染 / 强杀 ComfyUI(`stop_rendering_now`、`force_kill_comfyui`) | `cancel(job_id)`(ComfyUI `/interrupt`);**不再强杀进程** | R1 | 🔀 |
| C13 | 渲染后 QC(黑帧/冻结/分辨率/音轨) | `compose/qc.py` + `analyze.qc` | R5 | ⬜ |
| C14 | 能力矩阵 / 管线就绪检查(`inspect_pipeline_capability`、模型审计 `MODEL_AUDIT_SPECS`) | `media.healthcheck` | R1 | ⬜ |
| C15 | ComfyUI 模型浏览/下载(`model_manager`、`cm_*`) | healthcheck 提示缺失;**下载需用户批准,不自动执行** | R1 | 🔀 |

## D. 成片与交付

| # | 旧功能 | v2 去向 | 里程碑 | 状态 |
|---|---|---|---|---|
| D1 | `compose_shot`(TTS+BGM+SFX 混音 + 字幕烧录) | `compose/`(ffprobe 实测时长、闪避、loudnorm) | R5 | ⬜ |
| D2 | `compose_episode`(crossfade / concat) | `compose/` | R5 | ⬜ |
| D3 | 导出清单 `export_manifests`、`delivery_packages` | 交付包(成片 + 分轨 + SRT + 清单 + 溯源) | R5 | ⬜ |
| D4 | 断点续跑(`pipeline_state.resume_pipeline`) | worker 作业恢复 + workflow 恢复 | R1/R3 | ⬜ |
| D5 | 资产版本 `asset_versions` | `media_assets` 版本 + `current` 指针 | R1 | ⬜ |

## E. 运维与入口

| # | 旧功能 | v2 去向 | 里程碑 | 状态 |
|---|---|---|---|---|
| E1 | `start.sh` 启动/停止/重启/环境诊断 | `storyagent ops …`(只探测与显式启动,**不 pkill**) | R1 | ⬜ |
| E2 | `setup.sh` 环境安装 | `pyproject.toml` + 文档 | R0 | 🔄 |
| E3 | `main.py --render / --workshop / --cli / --demo / --check` | `storyagent ui|worker|…`;`--check` → healthcheck | R1/R4 | ⬜ |
| E4 | 独立渲染服务 UI(`render_app.py`,有陈旧 API bug) | ⛔ 并入「媒体生产」页 + worker | R4 | ⛔ |
| E5 | 系统 Tab:状态、日志、TTS/BGM 试听、工业化控制台 SOP | UI「系统」页 | R4 | ⬜ |
| E6 | 旧 DB 数据 | `db import-legacy`(只读) — 注意:旧 `data/story_agents.db` 主文件已缺失,仅余 WAL | R0 | ✅(工具)/ ⚠ 数据 |

## 汇总门禁

- R7 删除旧代码前:A–E 全部 ✅ 或 ⛔(附理由);`⬜ 待决` 项必须由用户裁决。
- 删除前打 tag `v1-final`(指向 `ca223f5`),保证可回滚。
