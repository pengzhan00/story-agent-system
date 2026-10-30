# Claude 实施任务包：多模态资产控制中心 V1

状态：核心链路已在工作树实施，完整契约尚未全部验收。2026-10-29。Claude CLI OAuth 401 后由 Codex 直接接手；详见 [实施核验](../reviews/2026-10-29-multimodal-implementation.md)。此文保留为目标契约，不能把其条目视为已完成清单。

## 1. 先读与基线保护

读取 `CLAUDE.md`、`docs/HANDOFF.md`、`docs/MULTIMODAL_CONTROL_CENTER_DESIGN.md`（特别是 §13）、`docs/reviews/2026-10-29-multimodal-design-mece.md`。现有代码优先于旧文档中的数量/状态。

准备任务包时仓库为 main@6d01896，存在 restart drain、预览修复、H3 调参、初步 a2v 等已暂存/未暂存/未跟踪改动。不得 reset、stash 掉他人改动或全量 add/commit。先重新记录 branch/HEAD/status/diff 与文件哈希，划清已有内容和本次增量。基础修复需合并时逐项核对并明确归属；没有可复现基线时先做隔离快照，不从干净 HEAD 丢失已有修复。

允许任务内分段提交，不能把“连续完成”解释成一个巨大提交。Git 仍按当前仓库规则执行；没有本次明确授权不 push、merge、部署或重启生产。共享生产 data/ 不作测试目录；数据库升级前用一致性备份，默认先在隔离数据家目录验证。迁移序号按执行时仓库分配，禁止改已有 migration 校验和。

## 2. 完成范围

本任务连续完成设计 P0–P3：注册能力/绑定/证据、持久化多依赖编排、工坊与试点项目复用、混合 t2v/i2v/a2v、音乐 a2a 接口、资产血缘与采用、人工发布分类台账，以及相应 API/UI/i18n/测试。

已有工具和模型复用，不重新安装一套基础设施。ComfyUI 作为通用执行后端，支持版本化 API 图导入、具名多输入输出与依赖检查；具体模型/节点缺失时显示原因。ACE-Step 与 YuE2 音乐 a2a 按本机版本、模型变体和附加包能力分别实现，缺附加模型可明确 blocked，不偷偷下载或假称可用。

不在本任务要求：通用拖拽画布、分布式 worker、任意脚本节点、定时引擎、自动对外发布、通知服务、播放数据抓取、下载全部候选模型、重新实现所有训练工具。保留扩展接口，不展示没有执行路径的可点击占位功能。

## 3. 六个连续实施包

### W0 基线与可执行契约

- 产出本轮可追踪基线清单和 capability/binding manifest，逐项记录真实验证级别，保留本机默认。
- 确定 registry schemas、API 错误与 locale keys、ERD、状态转换表。schema 文件进 Git，不以自由 JSON 代替输入约束。
- 补充事实：现有 MediaKind、单依赖、slot/is_current、auth、计数、媒体验证、模型参数解析路径如何兼容。验证 packaged defaults 与本机 override，不能只改出厂值而让运行时继续旧路。
- 退出条件：每项 V1 能力均有稳定 ID、输入输出、legacy kind、模型/服务依赖、状态及验证方式；新增模式不改变旧项目默认。

### W1 持久化图与统一执行服务

- 使用 capability_id/schema_version 与 binding_version 引用；冻结模板/模型/参数/连接器摘要，包含 effective settings。
- 最小持久对象：template/version、execution_plan、pipeline_run、node_run、node_dependencies、node_input_bindings、node_attempt/job link、expansion checkpoint、outbox。可合并物理表，但不得合并这些语义或复制 media_jobs 执行权威。
- PlanCompiler 验 DAG/端口/数量/资源/访问权，预演无外部副作用。GraphExecutor 只提交现有 media job；事务与唯一约束保护本地派发，恢复可对账。
- 支持具名多输出引用，依赖按资产端口而不是“上一步最后一个产物”解析；兼容 @prev 旧链。
- 支持条件跳过、required 上游失败阻断、批量 map、人工等待、节点限次重试、取消传播和有限动态展开。等待不占线程/资源。
- 新模式通过配置开关与适配路径启用；工坊单节点与试点项目调用相同应用服务，同一对象不能同时被旧 produce 派发。
- 退出条件：真实数据库集成验证多依赖 fan-in、崩溃恢复、重复事件、提交不确定、取消晚到结果。不能只写纯函数测试或两套状态“最后同步”。

### W2 模态绑定与 a2v / 音乐 a2a

- 工坊显式模式 t2v/i2v/a2v；项目 spec seed、生产计划、单镜重做、模型过期判断和 compose 均感知模式。t2v 不要求关键帧，a2v 不被 video_with_sound 改写成普通视频。
- 具名 audio/portrait/stem 输入不被 job_preparer 覆盖；沿用 Resolver 校验文件、权限与来源。单参考 DT 不冒充多参考编辑。
- 复用 Demucs，一首歌/版本分离一次。Phosphene 接 audio_stem 与 audio_stem_auto；是否分离写进 provenance。不把 separator readiness 接口当分离接口。
- a2v 子流程：源区间规划、受限分段、片段任务、闭口锚点、实际音频起点、overlap 裁切、末段处理、父子进度、局部恢复。每片段独立槽，最终镜头独立主产物。
- 帧数/尺寸/fps 按绑定约束验证；以 ffprobe 实测对账，音频采样与帧时基避免累计误差。分离/换声长度或偏移变化先纠正/阻断，不带错位进入口型。
- 映射 ACE-Step source_audio/reference_audio/repaint 区间等，按 Turbo/Base 任务集校验；YuE2 source/cover/variation 及 parent artifacts 分别校验，缺包明确报阻塞。
- ComfyUI 多模态 API 图绑定校验输入和输出语义、版本及节点依赖；对不能安全取消/内部局部重试的图显示真实边界。
- 退出条件：t2v、i2v、a2v 混合计划可执行；独立录音与歌曲短段有可登记产物；未实测音乐 a2a 保持未验证。完整 MV 与听感/口型 A/B 单列验收，不用 stub 代替。

### W3 资产采用、血缘与时间轴

- 增加 AssetRelation、AssetSelection（带 revision）、Representation 与 TimelineVersion/placement，复用实际 media_assets 行。
- 新旧采用状态同事务兼容，旧 current 可迁移，冲突显式列出；资产卡、镜头页、生产复用与装配统一读采用服务。
- 输入变更按血缘只使后继过期；缓存键包含完整版本/参数，重做新候选不被幂等缓存吞掉。
- 严格/预览装配策略可选；严格模式拒缺镜/文件/必需音轨。MV 原曲只混一次，a2v 禁止任意速度匹配，字幕不挤长固定音乐时间轴。
- 退出条件：旧项目资产可访问可采用；重新生成不覆盖已采用版本；混合模态引用可回溯；回收/物理清理尊重发布和任务引用。

### W4 发布中心与使用界面

- 平台/账号/栏目、可扩展内容分类、PublicationRecord、不可变 Revision/Items、Attempt/Event；非空 external ID 唯一、请求幂等与合法再次发布分别处理。
- 人工准备 → 外部人工发布 → 记录时间/链接/证据 → 确认发布；状态、权限、撤回/删除、素材替换和纠错事件完整。不得实现未经授权实际发帖。
- 发布中心列表/筛选/详情/编辑，资产详情和项目详情反查；一稿多平台、一个帖子多资产。无项目资产也可登记。
- 工坊、流水线模板编辑、运行详情、资源绑定目录和资产采用/血缘入口都可发现；预演解释阻塞，加载/空态/错误/权限/i18n 完整。
- 发布可作为独立关联流程或人工节点；scheduled 只是计划。状态计数与钻取口径一致。
- 退出条件：真实 API + 浏览器完成“独立资产 → 采用 → 两个平台人工发布登记 → 反查 → 替换/撤回保留历史”，无需真的向外发布。

### W5 迁移、回归、交接

- 全新库升级、带旧 schema/资产的升级副本、幂等重复启动、FK/唯一约束、回填计数、备份恢复和新路径关停演练。
- 禁止在生产进行验收破坏或用 schema downgrade 删除新数据。新版本数据存在后旧二进制的兼容范围需实测；不能保证时使用升级前完整备份恢复隔离实例，并说明新写入数据处理。
- 更新 ARCHITECTURE/HANDOFF/IMPLEMENTATION_PLAN/RUNBOOK、API 生成类型、双语目录和 Obsidian 证据。现有不准确文档只按本次实证修正。
- 给出提交、diff、测试日志、迁移证据、接口/页面路径、真实任务/产物与待人工验收清单。交接最后不得只写“全量通过”而不附命令和结果。

## 4. 不可替代的验收场景

| 编号 | 必测行为 | 证据边界 |
|---|---|---|
| G01 | 工坊与项目对同能力共用执行服务，多输入多输出端口正确 | 实际 HTTP/service + SQLite，不只 mock compiler |
| G02 | fan-out/fan-in、缺失必需输出、条件 skip、动态展开唯一与上限 | 持久化图运行到真实 job/资产 |
| G03 | 入队前/后崩溃、外部提交后断连、租约接管、重复事件 | 故障注入，不重复远端调用；不确定时可人工对账 |
| G04 | 等审核/资源/发布不占 worker；取消晚到产物不自动采用 | 运行时/并发验证；restart drain 回归 |
| G05 | t2v 无关键帧、i2v 有图、a2v 图+音频；三模式生产计划一致 | 测 plan、produce、手工 regenerate 与 compose |
| G06 | 共享 stem、音频起点、锚点 overlap、末段/帧时基、无重复混音 | 短真实媒体/ffmpeg；模型口型额外人工评价 |
| G07 | ACE-Step Turbo 不接受 Base 专属任务；YuE2 缺 cover 包拒派发 | 请求映射契约测试；真实能力实测另标状态 |
| G08 | ComfyUI 具名多模态映射、缺节点/模型、取消隔离、真实媒体验证 | 连接器测试 + 可用实际工作流单列冒烟 |
| G09 | 新候选/缓存区别、并发采用 revision、旧 current 兼容、过期范围 | 消费端读取与真实 DB 事务 |
| G10 | reader 禁写、writer 对对象访问权、admin 管绑定、受限血缘/缓存 | 真实 API 路径 401/403/内容隔离与审计 |
| G11 | 一素材多发布/一发布多素材、非空外部 ID 幂等、缺链接人工证据 | API/DB/UI 全链，历史修订保留 |
| G12 | 发布筛选与计数一致、撤回不丢历史、无项目发布、资产反查 | 分页/空态/删除后回页与业务计数 |
| G13 | 全新/旧库迁移、副本恢复、打包含新增 schema/模板 | 不接触生产副本以外的数据 |
| G14 | 双语页面、错误/空态、预览、长任务 UI 与 OpenAPI 一致 | 前端测试、实际浏览器与构建 |

先写最小能失败的回归，再实现。新增基础设施必须有真实消费者；不接受测试复制实现、源码字符串扫描、假成功 adapter 或空占位页面代替闭环。

## 5. 验证与长任务约定

实施期间按 W0–W5 运行定向门禁；发布判定前运行 software-system-review。以下是仓库确认过的命令形式，使用实际含 dev 依赖的 Python 环境，不为修环境改坏运行 venv：

```bash
python -c 'import storyagent.api.app'
python -m pytest
ruff check storyagent tests
mypy
python -m storyagent i18n check
python -m storyagent api openapi --out web/openapi.json
```

在 web/ 分别执行 npm run gen:api、typecheck、lint、test、build；数据库迁移仅对显式隔离 home 执行，具体环境变量/CLI 参数先从当前配置代码确认。保留命令、HEAD、数据夹、stdout/stderr、测试数量与耗时。不把旧测试结果算成本次。

长测试/生成由可恢复任务或后台日志运行，避免终端工具时间上限杀死前台任务。真实生成前先查内存/外部负载，不启动并发重渲染；不自动停他人服务、不下载大权重、不打断已有项目。

外部依赖或人工质量阻塞时继续独立工作，保留未验证状态，不将任何必要验收项悄悄移出范围。缺真实模型证据时最终结论 partial，不能 release-ready。

## 6. 可以直接发给 Claude 的启动提示

请在 Story Agent 仓库连续实施 `docs/specs/multimodal-control-center-implementation.md`，以 `docs/MULTIMODAL_CONTROL_CENTER_DESIGN.md` §13 为冻结约束。先读取真相并清点工作树，保护已有 restart/预览/H3/a2v 改动。按 W0–W5 自主推进、分段提交和验证，覆盖 G01–G14；保持已有默认与生产任务，缺依赖明确标阻塞，不静默降级。完成范围是 P0–P3，包含统一编排、三种视频模式、音乐 a2a 绑定、ComfyUI 多模态连接器、资产采用血缘及人工发布分类台账。不要擅自 push/merge/部署/下载/外部发布。交付可审查提交、迁移与测试原始证据、真实模型与人工验收的明确状态，并同步 Obsidian；实现后的独立复审通过前不要宣称可交付。
