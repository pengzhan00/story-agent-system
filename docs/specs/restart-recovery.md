# P1：重启收敛与工作流恢复

状态：2026-10-28 已实现并验证，待提交及正常部署。2026-10-27 实机发现。

症状：wait 重启停止领取媒体任务，但 workflow future 仍在等待这些媒体任务完成，busy 永不归零。
恢复时已先确认无 running media，再使用已有 now 恢复协议；API 子进程随后卡在长连接关闭阶段，
SIGINT 未结束，最终只终止核实身份的 API 子进程，现有 supervisor 重启成功。未删除队列或资产。
API 已返回 200，worker/server PID 已更换，原出图队列继续领取，工作流重新处于 running。

根因证据：WorkerGate.poll 先 worker.pause，再等待 worker.busy==0；Worker.pause 同时停止媒体和工作流领取，
busy 包含工作流 future。serve 的 uvicorn.Config 未设置 graceful shutdown 超时。
注意：尚未用确定性回归证明 API 挂起仅由 SSE 引起，不将这一推断当作唯一根因。

不变量：等待重启时，不得冻结在途工作流完成所依赖的任务；服务关闭必须有期限。禁止通过清库、
取消用户任务或直接将 running 改成 completed 绕过。

实现范围：restart.py、Worker 的暂停协议、workflow engine 的边界退出、API shutdown 和 supervisor。
先用 fake adapter 建立“在途工作流等待 queued media”重现，再选择可恢复检查点让出或只执行在途依赖的策略。
禁止为等重启无限排空整个用户队列。API 设置有界退出并验证 SSE/client disconnect、日志和退出码。

验证：无任务、仅媒体、工作流等待媒体、运行中的远程请求、活跃 SSE；wait/now/cancel 三种路径；
故障后租约恢复、无重复资产；使用真实子进程和有界时钟证明 restart 两端退出、supervisor 仅重启自己的子进程。
变更与可选模型配置分开提交，运行 focused update / workflow / worker 测试，再完整软件系统审查。
本机当前已恢复，不为验证再次打断生成队列。

## 2026-10-28 实现结果

采用“只执行在途依赖”策略：工作流等待上下文登记具体作业编号，调度器在暂停时仅领取这些编号及 depends_on 链，
在 LIMIT 前过滤；新工作流和无关任务保持排队。暂停与 claim/future 登记使用同一锁，避免忙闲状态漏算。
API 增加 shutdown_timeout_seconds 默认 5 秒，实际流式连接子进程正反控制验证有效。
已验证 60 个无关高优先级任务不会被领取、在途工作流完整结束、等待登记释放、依赖循环有界。
后端全量 918 项通过、2 项未选，额外并发回归 1 项通过；前端 98 项、构建、Ruff、mypy、i18n、启动迁移均通过。
报告：[重启修复审查](../reviews/2026-10-28-restart-drain.md)。当前未在用户生产服务执行重启试验。
