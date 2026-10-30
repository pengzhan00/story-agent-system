# 多模态控制中心实施核验（2026-10-29）

Verdict: partial
Validation: pass-with-warnings（自动化与隔离浏览器；真实模型任务未运行）
Git baseline: main@b5df9e3 + 明确保留的已有工作树修改 + 本次未提交增量
Release readiness: not ready

Claude CLI 两次请求返回 OAuth 401，未产生代码；按用户授权由 Codex 直接实施。Hermes 的 b5df9e3 保留，本次扩展在该提交上继续。没有 push、merge、升级生产库或重启 8765 服务。

## 实现与真实消费者

| 领域 | 本次实现 | 消费入口与验证边界 |
|---|---|---|
| 能力目录 | 从现有模块及模型配置生成类型端口、provider/model/version、绑定摘要；Comfy 模板内容纳入摘要 | `/api/capabilities`；状态是 configured，不能解读为真实模型已验收 |
| 持久编排 | 多依赖 DAG、预演摘要、请求幂等、模板新版本、审核、取消、局部重试、外部任务对账、事件记录 | `/control` → control_runs/nodes/attempts/events → 原 media worker；不新增第二套 adapter 执行器 |
| 资源与恢复 | 冻结绑定复核、未知内存保护、加速任务互斥、Phosphene/Comfy 外部活动探测；不确定远端提交要求对账 | worker；已有 legacy 路径兼容保留，并非所有旧入口已迁移 |
| 项目耦合 | 明确选择 script；按镜头 t2v/i2v/a2v 编译成可编辑图；保留当前视频规格提示词/参数；从资产采用选择取关键帧 | 项目 → 管线；编译和预演不发起 GPU 任务；浏览器验证了采用 → 项目图 → 预演 |
| a2v | 每首输入共享 stem 节点及内容缓存；≤5 s 分片；真实面板闭口锚点推进；独立 ffmpeg 拼接，原音只混一次 | 真实 ffmpeg 小媒体测试 + 模拟面板故障/恢复；没有真实模型口型质量结论 |
| 音乐 a2a | ACE-Step 源音频与参考音频分开，任务按 Turbo/Base 校验；YuE2 cover/variation 映射及 cover 包就绪校验 | 适配器 HTTP 契约回归；未生成真实歌曲 |
| ComfyUI | 图/音/视频具名多输入、具名多输出、导入映射与模板摘要；拒绝危险端口、缺 main/非法声明 | 连接器回归；具体模型/自定义节点仍由实际工作流决定，不能笼统承诺所有转换可跑 |
| 资产 | 血缘关系、显式采用及 revision 并发校验；已有 shot 生产/成片消费者读取采用版本；新候选不覆盖采用 | `/library` 与 selections/relations API；隐藏项目的采用关系不从共享资产泄露 |
| 人工发布 | 平台/账号/栏目、内容分类/用途/系列/集数、标签、多资产、不可变修订、计划/实际时间、链接/证据、撤回历史、外部 ID 去重 | `/publications`，资产/项目反查；浏览器登记完全在隔离库，没有向外发帖 |
| i18n/API | zh-CN/en 文案、稳定错误码、OpenAPI 与生成类型 | 类型/lint/语言包检查、前端测试 |

## 验证证据

Python 环境：`/tmp/storyagent-review-20261026-MECrFZ/venv/bin/python`；所有测试使用隔离数据目录。

- 后端全量：938 passed / 2 deselected（319.32 s），末次新增两项测试与 API 可见性收口后另做定向回归；`python -m pytest`，live 模型测试默认排除。
- 最后边界回归：16 passed；涵盖缓存复用与产物被物理删除后的 cache miss、坏检查点隔离、采用权限、编译、发布、迁移。不要把模拟 adapter 成功算作真实模型产物。
- `ruff check storyagent tests`、`mypy storyagent`、`storyagent i18n check`。
- 前端 typecheck/lint、33 文件 108 测试、生产构建通过；主 JS 超过 500 kB 的打包提示仍存在。
- 迁移：全新 0→25、重复 25→25、旧库 24→25，保留原项目，FK/integrity 检查；升级前自动备份可恢复 schema 24。生产库没有应用 025。
- 打包：使用 setuptools backend 构建 wheel，并检查新 control 模块、路由、025 SQL、utility runner 均已包含。dev venv 无 pip，改用既有 setuptools，没有安装依赖。
- Browser 插件不可用，使用本机 Playwright/Chromium；端口 18769、独立数据家目录，无模型 worker。验证管线预演/提交、人工发布登记、资产采用、项目图预演；页面异常 0。截图与简要结果见本目录 evidence。

临时详细日志：`/tmp/storyagent-control-full-frozen.log`、`/tmp/storyagent-control-final-boundaries.log`、`/tmp/storyagent-control-web-frozen.log`、`/tmp/storyagent-control-build-frozen.log`、`/tmp/storyagent-control-migration-verified.log`。长期摘要在 [validation.json](evidence/2026-10-29-control-center/validation.json)。

## 按严重程度的未闭合项

1. **P1：真实模型闭环尚未验收。** Phosphene 状态报告 YuE2 与 cover 包 ready，ComfyUI HTTP 200；ACE-Step 当前连接失败。该检查只读，不等同于歌/视频生成成功。下一步用短素材逐一验证 a2v、cover、DT 备选视频和一个 Comfy 图，再做人工口型/音质验收。
2. **P1：完整蓝图仍有后续范围。** 当前实现可用核心对象及消费者；尚未建完整 ModelPackage/Binding/ValidationEvidence 独立治理 CRUD、通用时间线版本编辑器、通用发布图节点。旧 workshop/produce 兼容路径仍存在，不能称为所有历史流程已统一迁移。
3. **P1：Git/生产交付未闭合。** 仓库存在多来源既有修改（启动恢复、预览、H3 调参等），本次没有混提。新源码/迁移/测试都在仓库目录内且本报告列明，但未形成独立提交，不能视为发布基线。部署前逐项分离增量、提交、备份，再升级生产。
4. **P2：扩展性。** 可见性筛选在应用层扫描记录，发布修订读取有 N+1；数据量增大前需要基于对象可见域下推 SQL。资源探测不能覆盖所有外部 CLI 进程，不是操作系统全局 GPU 锁。
5. **P2：资源与回滚。** 已验证备份恢复，不支持原地 downgrade；新版本写入后回滚需先导出新增台账再用完整升级前备份。没有执行生产恢复或真实模型故障演练。
6. **P3：前端。** 初版采用步骤编辑表单，缺通用拖拽图；JS 大包需后续按路由拆分。

## 下一步顺序

先完成本次增量的独立 Git 归属与基线；再在隔离数据家目录接真实模型短任务；补齐上述 registry/时间线/旧入口迁移工作后，重新执行完整 software-system-review。当前结论 partial，不能称“一次全部实施到位”。


<!-- live-followup-20261030 -->
## 后续实测（2026-10-30）

本页“真实模型未运行/ACE-Step离线”是实施阶段的历史快照。后续已启动实际模型测试并修复连接器契约，以 [逐项实测报告](2026-10-30-live-capabilities.md) 为当前运行证据；不改变本页 Git/完整蓝图尚未闭合的 partial 发布结论。
