# 可选能力接入复核（2026-10-27）

Verdict: partial
Validation: pass-with-warnings
Git baseline: cc17dcd（本轮变更未提交）
Release readiness: not ready（本轮为能力接入，未进行版本发布验收）

## 范围与变更

恢复工作时工作树干净，Demucs、DT 视频编码、CLI 多产物及后期模块已在当前源码中；沿用现有实现。
本轮增加 DT 异常返回类型校验、视频闭环和配置回归测试，保存本机可选配置示例，纠正备用模型文档。
本机运行配置补齐 LTX-2.3 的 8 steps / guidance 1.0，指定帧率与模型；不替换既有项目设置。
临时添加的 Illustrious / Pony 注册因当前底模缺失已撤回。没有删除权重或历史资产。

## 系统审查

| 范畴 | 结论与证据 |
|---|---|
| 业务闭环与入口 | 工坊按模型显式选择；两项真实任务从提交、正常调度到资产入库均完成 |
| 架构与配置 | provider / model 分开；新增模板不修改全局 defaults；本机配置沿用 DT 默认、Comfy 编辑可选 |
| 代码与异常 | 错误 images 类型、错误编码、返回帧数不足明确失败；多参考图拒绝，避免静默丢失 |
| 测试 | 真 ffmpeg 编码后以 ffprobe 验证帧数、帧率、时长及无音轨；配置示例验证不改 defaults |
| 权限与审计 | 沿用已有提交、合规、资产权限和 job_events；未修改认证或拓宽网络监听 |
| 数据与幂等 | 沿用 ledger 入队、状态机、幂等和资产登记；未新增数据库结构或计数接口 |
| UX / 工程使用 | 已登记能力可在工坊选择；未安装能力不伪装就绪；记录服务在线与质量验收的区别 |
| i18n | 本轮无新增 UI 文案；错误经现有错误码路径显示，说明文档无需 locale key |
| 运维与回退 | 本机 models 配置修改前备份 /tmp/storyagent-models-before-resume-20261027.toml；可移除本轮模型覆盖回退 |
| 发布 | 本轮不作 release-ready 声明；未测试打包安装、5 秒以上视频质量或所有备选的真实生成 |

## 真实推理证据

- `fdf9d80750944b2c86688309ca5f6bb7`：DT LTX-2.3 / 512×320 / 9 帧 / 24 fps / 8 steps，52.29 秒完成。
  产物资产 `94d0bc48cbf340ae9b295b3b142a8f5c`；ffprobe 独立确认 H.264、9 帧、0.375 秒、无音轨。
- `277dcff623c04f4098cce95d32c45780`：DT FLUX.2 / 512×512 / 4 steps，8.10 秒完成。
  产物资产 `512c4f7cba7c46f0992c6eaa35bc1293`；已查看图片，陶瓷茶壶 / 木桌 / 窗边光符合提示词。
- 不将短测闭环作为长片、角色一致性或运动质量验收。Juggernaut 本轮只检查权重与配置，不新增生成任务。

## 剩余事项

- P1：真实重启发现 wait / 在途工作流依赖互等；API 在关闭连接阶段未退出。本机已恢复，API 200，worker/server 已重新启动，原队列继续。持久修复已列入 IMPLEMENTATION_PLAN，见 [恢复规格](../specs/restart-recovery.md)。因此整体审查仍为 partial，不作发布判断。

- P2：Illustrious / Pony 底模已不在当前 DT 目录；需要恢复底模并实际生成后才可启用。
- P2：MRT2 运行环境缺失，InstantID / IC-LoRA 尚无本轮完整流程实测；不宣称已接通。
- P3：工坊现有 available 字段是提供者健康状态，不能替代逐模型权重和质量验收。
- 原有前端 React Router future flag / act 警告仍存在，本轮未改相关页面。

## 验证证据

- 启动 / import：TestClient lifespan 成功，OpenAPI 200。
- 数据库：全新 0→22、重复迁移 22→22，integrity_check=ok。
- 专项：51 项适配器 / 工坊 / CLI 测试通过；最终 DT 专项 22 项通过（含新增配置回归）。
- 静态检查：Ruff 全部通过；mypy 172 源文件通过。
- 前端：typecheck、lint、95 tests / 32 files、build 全部通过；构建产物放 /tmp，不覆盖运行目录。
- 后端全量：911 passed、2 deselected，377.05 秒；新增配置回归另行通过。

本轮证据日志：`/tmp/storyagent-resume-*.log`；Obsidian 记录已写入并经 wiki sync 同步。
