# 独立启动入口与退出控制的验证

2026-11-27，基于 main 9a697f1；这是功能定向验证，没有执行发布级全量审查。

## 行为与边界

`scripts/start.sh` 默认启动常驻 loopback 入口，原 API/worker 监管循环以 `--backend-only` 在私有端口运行。
主后端退出或启动失败时，公共地址和深链接继续返回双语启动页，按钮启动固定的本项目脚本；不会接收任意命令。
启动可能恢复数据库里已排队的工作。模型服务不由该入口启动或停止。

系统页可进入“后端启动与退出”。退出通过原 API 管理员校验和 wait 重启握手，等待当前工作完成后，
监管脚本识别退出标记并结束，不再启动第二份后端。轻量入口保留。取消退出/重启会清除该标记。

“登录时保留启动入口”只准备当前用户的 LaunchAgent，下次登录启动轻量入口，后端仍等待点击。
本次没有在真实用户目录安装 LaunchAgent，没有重启/停止当前生产进程，也没有执行真实模型任务。

## 验证证据

- tests/test_gateway.py：真实 HTTP、只属于测试的轻量子进程，验证深链接、后端失败与重试、重复启动、
  Host/Origin/token 检查、管理员退出权限、等待工作完成、登录安装权限、临时目录 plist 的 argv。
- tests/test_update.py、tests/test_restart_runtime.py：保留原热重启与排空协议，验证退出后不再重启，以及取消退出。
- tests/test_i18n.py、API 模块 import、Ruff/Mypy（196 文件）通过。
- 网页 SystemGateway 和 UpdatePanel 测试、typecheck、lint、build 通过；Vite 大包提示仍存在。
- Browser plugin not available，使用已有 Playwright 与系统 Chrome，在隔离随机端口验证，没有安装浏览器依赖。
- 桌面/375px 手机宽度、按钮禁用、中英文、保留原深链接、后端失败恢复、等待退出和再次启动通过。
  测试安装登录项只写入 `/tmp/storyagent-gateway-qa-20261127-exit/agents`，未写真实 LaunchAgents。
- 浏览器 pageerror 为 0，HTTP 测试确认 401/cookie 不被绕过、大请求体正常透传、SSE 第一条消息先于流结束到达。
- 截图 stopped.png、mobile.png、controls.png 为隔离启动页/控制页。临时 API 和入口已清理。

## 未运行

没有以本机实际队列做退出实验，也没有实际注销/登录测试 launchd；没有后端全量或完整软件系统审查。
不据此声称 release-ready。直接运行 `storyagent serve` 仍是原有 API 直连模式，不提供独立启动入口。
