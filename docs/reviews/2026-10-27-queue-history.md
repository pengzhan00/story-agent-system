# 队列历史批量删除

Verdict: pass-with-warnings（本功能；整个产品发布状态仍受既有重启问题约束）
Validation: pass
Release readiness: not assessed（无安装包发布验收）
Git baseline: 43a4c72；本轮工作树变更有意保留，尚未提交。

工坊与项目共享的媒体作业列表增加逐项勾选、本页已结束任务全选、确认后批量删除。
切换筛选或翻页清空选择；删除后刷新当前列表，后续页被删空时返回上一页。
运行中和排队中任务不可选择。操作仅移除历史列表项，不删除生成素材和溯源。

数据库迁移 23 增加 history_removed_at；API 在分页前过滤。内部任务查询仍保留全部记录，避免干扰
工作流依赖、重试、job: 引用和幂等。写操作沿用角色权限，并记录操作人、所选编号与删除数量。
无效编号 / 活动作业导致整批回滚。历史删除重复请求幂等。

验证：83 项 API / ledger / migration / auth 相关测试通过；队列 UI 5 项通过，包括删除最后一页的回退；
前端全量 98 项通过，typecheck / lint / build 通过；Ruff、mypy、双语 i18n 检查通过。
全量后端 914 passed / 2 deselected（280.54 秒）。
真实库副本 22→23 / 23→23 成功，任务、素材、事件数量保持，integrity_check=ok，启动及列表接口成功。

Browser plugin not available；使用 bundled Playwright + 本机已安装的 Chromium。
真实浏览器访问隔离 Vite / FastAPI / SQLite，103 项终态记录 + 1 项排队任务：全选本页 99 项、
确认删除、列表补页、排队任务保留、取消删除和取消选择均验证；桌面 1440×1000、移动 390×844 截图已检查。
没有页面运行错误；控制台仅现有 React Router future flag 警告。移动端沿用现有横向表格与导航布局。
截图和脚本：/tmp/storyagent-history-browser/，未操作用户真实历史。

架构、权限、事务、数据幂等、分页计数、真实入口、i18n、可观察性均沿用共享边界验证；不新增外部服务。
迁移前标准数据库快照用于回退；代码回退不会丢掉历史数据。未进行安装包发布验收，不作整个产品 release-ready 声明。

本机已应用迁移 23（自动快照 data/storyagent.db.bak-v22），加载已验证构建并重启 worker/server。
遇到既有 API 关闭连接阻塞，确认 worker 停止且无活动任务后只终止核实身份的旧 API 子进程，
由原 supervisor 拉起。API 已恢复，新历史端点及前端构建均经 HTTP 验证；用户真实历史未删除。
原构建备份 /tmp/storyagent-history-web-dist-before。当前环境未开放 Chrome 控制，生产登录态页面未直接点选；
交互验证使用隔离完整服务，生产仅验证公开契约和静态构建。
