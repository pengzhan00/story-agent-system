# 发布就绪审查（2026-10-30，基线 main@1692404 + 修复 566536b）

```text
Verdict: partial
Validation: pass-with-warnings
Git baseline: main@566536b（工作区干净；仅有 tag v1-final，本基线无版本 tag）
Release readiness: 本机生产部署 = ready（先备份数据库）；作为"发布基线/发布候选" = not ready（见下）
```

范围：整个 Story Agent System v2（`storyagent/` + `web/`）。验证全部在 `git archive` 导出的干净快照上进行，没有碰生产检出；迁移检查用生产库的 `.backup` 副本。

## 验证结果

| 项 | 结果 |
|---|---|
| 导入/启动 | 通过（快照 venv 里 `import storyagent`） |
| ruff / mypy（183 源文件） / `i18n check` | 通过 |
| 后端全量 pytest（HEAD 1692404） | 通过（exit 0） |
| 修复后全量 pytest | 1 个失败，已修（测试写死"最新迁移=025"），相关测试重跑通过 |
| 网页 typecheck / lint / test（108） / build | 通过；仅 JS 大包提示 |
| 全新库迁移 0→26，重复迁移，完整性 | 通过 |
| **生产库副本迁移（HEAD 1692404）** | **失败**：`migration 025_control_center was modified after being applied` |
| 生产库副本迁移（修复后） | 通过：25→26，与全新库结构逐表一致，完整性/外键 0 问题，300 资产 6 项目保留 |
| 密钥扫描（已提交文件） | 无密钥、无 db/.env 文件被提交 |

## 发现（按严重度）

### P0（已修）：生产库无法迁移，部署后系统起不来
- 证据：生产库 `schema_migrations` 里 025 记录于 2026-10-28 23:55，校验和 `ccb83b52…`，是一份**从未提交的草稿**；之后 `025_control_center.sql` 被改并提交（`b0cecd88…`）。`scripts/start.sh` 在 `set -e` 下先 `db migrate`，失败即退出；`deploy.sh`/`update` 的顺序是合并→安装→构建网页→迁移，迁移失败时代码和网页已换新、数据库没升。
- 影响：部署后的任何重启都会让 worker 和 server 都起不来。
- 修复（566536b）：025 还原为生产已应用的原文（校验和精确吻合），后加的 `control_cache` 表和 `publication_revisions.classification` 列放进 `026_control_cache_classification.sql`。`tests/test_migration_applied.py` 锁定已应用迁移的校验和并测试"停在已应用 025 的库"能升到最新。

### P1（已修）：文档与事实不符
交接文档、Obsidian 都写"生产未升级 schema025"，实际生产已在 25。已在 `docs/CLAUDE_HANDOFF_2026-10-30.md`、`docs/HANDOFF.md` 更正。**Obsidian 里相应两篇（实施与验收记录、本机能力实测）仍写"生产未升级"，需要同步更正。**

### P1：a2v 口型人工验收未通过
技术执行通过（14 个小样），用户反馈"口型还是不太对"，原因未定。a2v 在文档里按"实验可选"，没改默认值，不阻塞部署，但不能当作已验证的稳定能力对外宣称。

### P2
- 没有版本 tag：基线只有 `v1-final`。发布前应打 tag（建议 `v2.0.0-rc1`）。
- 真实模型验证是小样（1–3.5 秒、draft 画质），不能推算正式产能；图片编辑、ASR、数字人、声音转换、增强、训练等能力未逐一实测。
- 完整设计范围未交付：registry 治理、通用时间线版本、旧入口迁移。
- 更新/热重启功能（`storyagent update`、网页"更新并重启"、重启守护）已由用户在生产上验证可用（2026-10-30，用户口头确认；本次审查未复验）。
- 重启排空修复（`docs/reviews/2026-10-28-restart-drain.md`）需要在生产上部署后才生效。

### P3
- 测试封闭性：较早一版 `test_longcat_makes_a_talking_head_from_portrait_and_speech` 在环境里设了 `HF_TOKEN` 时会失败并打印令牌；HEAD 上的版本只断言适配器自己的键，已不再出现（用户确认该令牌无需处理）。
- JS 大包提示（前端未分包）。
- 本地 main（主检出）落后 `origin/main` 一个提交，见下。

## 本轮审查中顺带修好并已在 main 的问题
权限（`/api/maintain`、purge 仅管理员）、清理历史按项目范围、purge 对资料库引用的保护、孤儿清理先清空 current、按性别命名人物、`video_with_sound` 重复渲染、`say-macos` 选项注入（提交 `e22efcd`、`82c09d0`）。

## 部署前检查单
1. 主检出执行 `git merge --ff-only origin/main`（或直接用 `scripts/deploy.sh`，它会自己拉取并快进）。
2. 先备份生产库：`sqlite3 data/storyagent.db ".backup 'data/storyagent.db.bak-before-deploy-20261030'"`；迁移本身也会自动做 `bak-v25` 快照。
3. 无运行中的作业时再部署；部署后重启 `scripts/start.sh`，确认 `/api/ready` 返回 `schema_version: 26`。
4. 部署后先跑一个小任务确认，再批量生产。
5. 打 tag，并更新 Obsidian 的"生产未升级"。

## 通过的检查 / 残余风险 / 下一步
- 通过：Git 基线、导入、迁移（全新库、生产副本）、后端/前端全量、lint/类型/i18n、密钥扫描、权限矩阵测试。
- 残余风险：真实模型质量未人工验收、版本无 tag。
- 下一步：按检查单部署 → 小任务验证 → 打 tag → 定位口型（先中英文单片对照）→ 收口剩余设计范围。
