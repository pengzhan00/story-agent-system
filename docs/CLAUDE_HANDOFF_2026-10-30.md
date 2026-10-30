# Claude 接手：Story Agent 多模态控制中心

交接日期：2026-10-30。本文是接手索引，具体实现和证据以当前源码、下列报告及 Git 状态为准。

## 后续 Git 收尾（用户已授权，覆盖下文历史“未提交”状态）

- 用户追加指令：“该提交合并推送的就完成，收手”。Claude 已收到停止操作通知并确认停手，Git 由 Codex 单独收尾。
- `358a26a`：从开工前保存的基线 diff 单独提交重启依赖排空与上传预览修复（含原已暂存的对应报告和测试）。
- `5a072e5`：提交多模态控制中心、适配器实测修复、双语界面、schema025 和验收证据；原 H3 配置/说明改动未混入。
- 当前就在 main，远端 main 是本地祖先，无需制造合并提交。计划普通 push 至 origin/main，远端完整 SHA 以收尾命令回执为准；本文不预先宣称推送成功。
- 保留本地 H3 两文件差异和原有 `docs/剧本与分镜质量审查_20261028.md` 未跟踪文件，不清理他人工作。收尾仅核对 diff、证据哈希与 Git，不重跑生成或测试，不部署、不迁移生产库。
- 口型人工质量未通过、原因未定、暂停重测等约束继续有效；仍为 partial，未作发布基线或 release-ready 判定。
- Claude 只读复查提到 say-macos 台词选项边界待办，本轮不扩展修复或测试。

## 先读：当前指令和边界

用户目标是一站式多模态 AI 资产生成控制中心与自动化流水线：统一工坊和项目工作流，覆盖多种输入输出、版本采用、溯源和人工发布分类记录。

用户最新反馈：**“口型还是不太对”**。当前结论是**技术执行通过，a2v 口型人工质量未通过**。“中文支持度不足”只是待验证假设，不能作为已查明原因。

用户明确要求：**先不重测，不派发后台测试，不下载新模型**。本交接不构成重新测试或重启服务的指令。以后明确要求测试时，由协调方下发小范围任务，其他执行工具承担长任务、日志与指标；不要让协调方持续生成和轮询浪费 tokens。

保留默认：Draw Things 主出图，Phosphene 主视频，ComfyUI 按需可选，mflux 不作默认。a2v 在文档中列为实验可选；没有因本轮质量反馈修改运行默认值。

## 仓库与所有权

- 路径：`/Users/pengzhan/myworkspace/projects/story-agent-system`。
- 本次交接已核对：分支 `main`，HEAD `b5df9e3`；**工作树有大量未提交和部分已暂存改动**。
- `b5df9e3` 是 Hermes 的三文件提交：`media/adapters/phosphene.py`、`media/lipsync.py`、`workflow/seed.py`。后来 Codex 又修改了相关文件，不能用 HEAD 代替最终实现状态。
- 开工前已有约35项他人改动。原始基线：`data/run/control-center-implementation-20261029/baseline.diff`、`baseline-files.json`。必须逐项区分所有权，禁止直接 `git add .`、reset、stash 或混合提交。
- 本轮控制中心和真实测试修复**尚未提交、推送、部署**。没有在生产库应用 schema025，没有重启生产8765，没有往生产库写测试资产。
- `data/config/services.toml` 是被忽略的本机运行配置；不能假设会随 Git 发布。
- 旧 `docs/HANDOFF.md` 含早期工作树、服务和“未实测”描述；请优先用本交接及10月30日实测报告核对，不照搬旧状态。

## 已实现范围

1. `storyagent/control/`：配置驱动能力目录、绑定摘要、持久化 DAG 编译与执行、类型检查、幂等提交、取消/重试/审核/恢复、缓存和资源等待。
2. `025_control_center.sql`：模板、图运行/节点/尝试/事件/缓存、资产关系和采用版本、发布目标/分类/记录/版本/条目/事件。
3. `api/routes_control.py`：能力、预演、运行、模板、项目编译、资产采用/关系、人工发布台账；含权限和隐藏项目反查隔离。
4. 工坊与统一图共享能力契约。项目编译使用明确剧本、采用的资产版本和当前视频规格，支持混合 t2v/i2v/a2v。
5. a2v：共享人声分离 → 有界切片渲染 → 实际闭口锚点 → 后片续接 → 原音精确拼接。沿用已有 media worker，不另建派发 worker。
6. 资产明确采用后，被工作流和装配真实消费；新候选不覆盖用户选择。人工发布可记录平台、分类、精确版本、状态/历史及资产反查，未接实际平台发帖。
7. 前端控制中心、发布页、采用到镜头，以及工坊/项目/资产入口；zh/en locale、OpenAPI 和类型同步。
8. ACE-Step/Phosphene/Comfy 多输入输出适配，控制工具 runner；保留旧入口兼容。

**剩余范围**：完整 registry 治理、通用时间线版本、全部旧入口迁移等尚未完成。不得宣称全设计已交付。

## 实测与修复

权威报告：[`reviews/2026-10-30-live-capabilities.md`](reviews/2026-10-30-live-capabilities.md)。14个真实操作小样通过技术执行：DT出图、CosyVoice配音、Comfy音效、Phosphene t2v/i2v/单片a2v、多片a2v、DT LTX2.3备选、YuE2写歌/cover、ACE-Step t2a/cover/repaint、Demucs拆轨。它们不是14种独立模型，也不是全部目录能力均已实测。

真实测试经 GraphStore → 原 worker → 实际服务，隔离数据目录 `data/run/live-control-20261029`。视频只有1–3.5秒、低分辨率 draft，不能推算正式产能。

实测闭环的五个问题：

- 共享工坊漏 `audio.sfx`，已补契约、双语和覆盖测试。
- DT参考图与最终请求尺寸不同引起422；最终参数合并后等比缩放补边，不改原图。
- ACE-Step拒绝客户端绝对路径；改为分别 multipart 上传源/参考音频，不放宽服务路径保护。
- Demucs旧目录格式不适用且嵌套extra未消费；兼容配置读取并预检`.th`，复用已有权重。
- a2v末段按8n+1向下取整导致视频短于音轨；改为向上覆盖、overlap在有界窗口内，并分别校验视频流/音频流时长。容器时长不能证明视频足长。

最终多片样例：3.500秒，84帧，音视频流均3.500秒，原音相关度约0.99935。**这些指标不能证明音素与嘴形同步。** 人工验收独立记录在 `docs/reviews/evidence/2026-10-30-live/human-acceptance.json`。

多片样例文件：`data/run/live-control-20261029/projects/_unassigned/media/compose.assemble/ab2d4516ea3d4e6e9aac8c0052651a84.mp4`。

如果将来明确授权定位口型，先只读核查输入音频与模型实际条件；最小对照建议同人像/参数中英文单片，然后单片对比拼接。候选原因包括语言/音素适配、固定AV偏移、音频起点、接缝、语速/停顿、渲染参数；**当前均未定因，本轮不要执行**。

## 当前服务与权重注意事项

以下是上轮结束时状态，本交接没有重新探测端口：

- Phosphene8198；DT7860；Comfy8188；ACE-Step8001。
- ACE-Step经已有 launcher 启动，保持在线。
- 用户已允许临时退出DT释放内存；该操作已经完成，**DT已重开、HTTP API已恢复200且仅127.0.0.1**。后来的重复授权回执不是新任务，不要再退出一次。
- 未关闭内存保护，未下载模型。Demucs使用已有约80MiB文件：`/Users/pengzhan/pinokio/api/phosphene/mlx_models/demucs/hub/checkpoints/955717e8-8726e21a.th`，SHA256前缀8726e21a。
- `/Users/pengzhan/models/demucs/955717e8-8726e21a.th` 是新增指向上述文件的符号链接；原safetensors保留。不要误判还需下载。
- 不复用长前台命令运行生产工作流：此前420秒终端超时杀进程导致running残留。生产长任务用既有异步提交/worker（如`--no-wait`），不直接改库掩盖故障。

## 验证状态及发布限制

- 最后相关后端回归：**141 passed，76.54秒**，日志 `/tmp/storyagent-live-final-focused.log`。
- 前端108测试、typecheck/lint/build通过；JS大包提示仍在。Ruff、Mypy183源文件、i18n通过。
- 较早后端全量938 passed/2 deselected是在后续修复之前，**不能当作最终代码全量结果**。
- schema0→25、24→25、重复迁移、外键/完整性与schema24备份恢复在隔离库验证；未生产迁移。
- 当前发布结论 **partial，非release-ready**。若进入发布/基线，须按AGENTS和`software-system-review`重做最终代码的完整门禁；本次仅交接，不启动该门禁。
- 实测脚本 `scripts/live_control_smoke.py` 可供以后执行方使用，**现在不要运行**。

## 文档与Obsidian入口

按需阅读，避免重复全量扫描：

1. `docs/MULTIMODAL_CONTROL_CENTER_DESIGN.md`
2. `docs/specs/multimodal-control-center-implementation.md`
3. `docs/reviews/2026-10-29-multimodal-implementation.md`
4. `docs/reviews/2026-10-30-live-capabilities.md` 和 `evidence/2026-10-30-live/`（result、stream/audio QC、缓存、consumer集成、DT恢复、人验记录）
5. `docs/specs/capability-runtime-verification.md`：治理约束和后续待办，未派发外部执行。

Obsidian目录 `/Users/pengzhan/Documents/Obsidian Vault/技能库/`：

- `Story Agent 多模态控制中心架构设计.md`
- `Story Agent 可选能力接入与实测记录.md`
- `Story Agent 多模态控制中心实施与验收记录.md`
- `Story Agent 本机能力真实实测 2026-10-30.md`

最新口型质量未通过、暂停重测已回填；后续不能被旧的“succeeded”报告生成器覆盖。用户允许更新Obsidian，没有授权修改Codex记忆。

## 接手后的第一步

先读根 `CLAUDE.md` 和适用AGENTS，再只读核对cwd、分支、HEAD、dirty diff及本文证据。向用户简短确认接手状态并等待具体后续任务。**不要因接手自行重测、生成、下载、重启、迁移、提交或部署。** 如需整理下一阶段任务，先明确技术完成、人工未通过、尚未实现三类边界。
