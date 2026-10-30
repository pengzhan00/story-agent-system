# Story Agent：重启等待与连接关闭

Verdict: pass-with-warnings（本功能）
Validation: pass
Git baseline: main / 6d01896；本轮修复未提交。
Release readiness: not assessed（本轮不发布安装包）

## 范围与复现

仅处理 Story Agent 的重启 P1。接手时已有 models.toml / h3.py 两处 H3 调参改动，完整保留，
不把本轮检查作为其生成质量验收。没有读取或修改其他项目。

原行为：WorkerGate 先 pause()，暂停所有队列领取，再等待 busy() 清零。produce / compose
中的在途工作流等待媒体任务，媒体任务因此永远不能运行。新增真实 workflow / ledger / worker 回归，
修复前稳定失败：`restart deadlocked while workflow waits for queued media`（16.69 秒），日志
`/tmp/storyagent-restart-before.log`。另在实际服务重启中观察到 API 等待连接关闭而不退出。

## 修复与不变量

- 工作流通过有生命周期的等待上下文登记具体 job ID；完成、取消、异常时自动撤销登记。
- wait 重启只领取这些 ID 及其 depends_on 传递依赖。SQL 在 LIMIT 前过滤，60 个更高优先级
  无关任务不能挡住依赖；循环依赖的遍历有界，不会无限递归。
- 新工作流和无关媒体留在队列，原工作流保持原来的 force / 参数 / 阶段语义，不通过取消或重建
  用户任务绕过互等；在途工作很长时 wait 仍可能较久，现有立即重启选项保留。
- 暂停与 claim → future 登记处于同一锁边界，busy() 不会漏看刚领取的工作。
- API 配置 `shutdown_timeout_seconds=5`，Uvicorn 超时后取消未关闭的连接任务；媒体超时不受影响。
- 更新卡片的中英文说明同步反映“只领取在途依赖”的实际行为。

症状 → 根因 → 不变量 → 禁止捷径 → 自动守卫：
重启互等 → 暂停阻断在途依赖 → 在途依赖仍可完成 → 禁止排空所有队列/清库/伪造 completed →
带 60 个无关高优先级作业的真实工作流回归。
关闭无界 → 没有连接关闭期限 → 长连接不能永久阻止服务退出 → 禁止靠生产 PID 信号作常规流程 →
真实流式 HTTP 子进程 + 关闭期限的正反控制测试。

## 验证

- 临近回归 78 项通过（update / workflow media / ledger / runtime）；之后增加暂停并发回归单独通过。
- API 子进程测试：保持流式连接，启用期限后以 75 退出；负控制移除期限后稳定复现超时，证明守卫有效。
- 工作流结束时必要媒体已完成，无关 60 项仍 queued，新工作流仍 queued，等待登记清空。
- Ruff / mypy 172 文件通过；双语 i18n 一致；前端 typecheck / lint / 98 tests / build 通过。
- 新库 0→24、重复迁移 24→24、integrity_check=ok、app lifespan / ready 成功；本轮无新迁移。
- 后端全量 918 passed / 2 deselected（327.44 秒）；全量收集后新增的暂停并发回归另 1 项通过。日志 `/tmp/storyagent-restart-*.log`。

## 系统审查

业务流程：沿用更新卡片与等待/立即重启/取消入口，依赖继续执行但无关工作不插入。
架构：等待上下文与 job 依赖链共用，不按项目或模型名猜测；不新增来源或重复任务台账。
数据/幂等：不改任务终态、资产、幂等键、权限与审计；不取消用户媒体来换取退出。
权限：既有 admin 更新权限和审计沿用，没有新增网络接口或扩权。
i18n：更新既有两组 locale key，中英同时维护。
运维：不修改重任务超时，不启停用户外部模型服务；回退本轮代码恢复旧调度，无数据迁移回退要求。
系统性范围：问题涉及 restart → scheduler → workflow wait 和 API exit 两个共享边界，已在共同边界修复并加守卫；
无需另行派发跨项目扫描。

本轮没有在生产服务上试错重启。代码需正常部署重启后生效；原两处 H3 改动保留待其所属工作审查。
