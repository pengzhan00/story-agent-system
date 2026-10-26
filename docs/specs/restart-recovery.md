# P1：重启收敛与工作流恢复

状态：待实现，发布验收前处理；不阻塞当前媒体队列继续运行。2026-10-27 实机发现。

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
