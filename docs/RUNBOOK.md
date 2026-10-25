# Runbook

```bash
scripts/start.sh                      # worker + web server together, restarted together on exit 75 (hot restart); Ctrl-C stops only these two
storyagent update status | check | run [--restart]   # the in-app update (steps of scripts/deploy.sh), see "生产部署与更新"
storyagent db migrate                 # idempotent; snapshots *.bak-v<N> before upgrading
storyagent media healthcheck          # one screen; exit 0 = every required entry ok, 1 = not, 2 = bad config
storyagent media healthcheck --json --require tool.h3     # cron-friendly
storyagent serve                      # HTTP API (+ /api/docs); run next to the worker, in any order
storyagent worker                     # long-running; SIGTERM/SIGINT release leases cleanly
storyagent worker --once              # drain the queue and exit
storyagent media jobs [--status running]   |  media show <id>  |  media cancel <id>  |  media retry <id> [--new-seed]
storyagent workflow run --project 1 [--stage produce] [--param tier=draft]   # needs a worker (or --inline for story stages)
storyagent workflow status --project 1 | watch   # progress bars, ETA
storyagent export episode --project 1 --out DIR  # delivery package; exit 6 when a blocking QC check failed
storyagent project list [--trashed] | trash ID [--restore]
storyagent ops llm-unload             # unload resident LLMs (when a heavy job waits on `llm_resident`)
```

New machine: `storyagent setup` (detect, list what is missing, write this machine's config, verify) - see [INSTALL.md](INSTALL.md).

## Symptoms

| Symptom | Cause / action |
|---|---|
| Job stays `queued`, message `heavy_busy` / `light_limit` / `memory_budget` | Normal arbitration; raise `memory_budget_gib` only if the machine really has room |
| Job stays `queued`, `llm_resident` | An LLM is loaded. `storyagent ops llm-unload`, or set `llm_unload_policy = "auto"` |
| Job `running` with no progress, worker dead | Wait `lease_seconds`; the next worker requeues and re-attaches (or re-submits). After `max_attempts` the job fails `worker_lost` |
| `missing_model` / `missing_tool` | `media healthcheck` names the file; downloads are never automatic |
| `consent_required` / `license_ack_required` | Record voice consent on the character / `license_ack_required` only happens with `compliance.restricted_license = "block"`: acknowledge the licence in project settings, or set it to `alert` |
| A subprocess job failed and its scratch directory is gone | `<home>/logs/jobs/<job id>.log` keeps the tool log |
| Seed-VC job runs for minutes at 0 % CPU | The tool is stuck in the hub downloader: v2 looks up its AR/CFM checkpoints on the hub unless they are passed explicitly (the model entry does, from `checkpoints/`). Keep `[tools.seedvc] offline = true` |
| `separate_vocals` job fails in Demucs | `~/venvs/seedvc/bin/demucs` needs the `sphn` package (installed on 2026-10-21; a fresh venv needs `python -m ensurepip` then `pip install sphn julius lameenc openunmix einops`) |
| LongCat job stays at "loading model" with ~0 % CPU and a connection to a remote https host | The inference script fetches the Whisper feature-extractor config from the model hub. The adapter runs it with `HF_HUB_OFFLINE=1` (`[tools.longcat] offline = true`); the config must be in the local Hugging Face cache (`openai/whisper-large-v3`) |
| Seed-VC hangs at 0 % CPU while downloading hubert/bigvgan | The hub's Xet transport does not work behind the firewall; the adapter sets `HF_HUB_DISABLE_XET=1`. The first run also downloads `hubert-large-ll60k` and `bigvgan_v2_22khz_80band_256x` and compiles shaders (~4 min), later runs take seconds |
| Phosphene panel fails to start with `FileExistsError` | `~/pinokio/api/phosphene/mlx_outputs` is a dead symlink (its target was deleted): recreate the target directory, do not edit code |
| `train.lora` ends `output_missing` | mflux-train writes `<output>_<timestamp>/checkpoints/*_checkpoint.zip`; check the job log for a crash before the first checkpoint (`save_frequency`) |
| `produce` finishes with "skipped: no usable model" | Nothing is configured for that kind (e.g. no sound-effect model) or its service is down (`media healthcheck`); the rest of the stage is kept |
| `produce` skips dialogue (`no_voice`) | The speaking character has no voice sample: upload one on the character (Web "故事" page) or set `[workflow] default_voice` |
| `compose` fails with `missing_tool` mentioning libass | A visible AIGC label is required but the ffmpeg in use cannot burn it; point `[tools.ffmpeg] binary` at ffmpeg-full or set `explicit_label = "off"` |
| Run says `running` but nothing moves | Start the worker (`storyagent worker`): only it executes runs and media jobs. After a crash the lease expires and the run is requeued |
| A code change had no effect on a live setup | Restart the worker and the server: they load code once |
| Output exists but job failed `output_invalid` | Zero-byte or unreadable by ffprobe; see `media show <id>` events |

## Recovery verified

`kill -9` of the worker mid-job: lease expires -> `lease_expired_requeued` -> `claimed` -> `reattached` -> `completed`; exactly one asset (checked manually and in `tests/test_worker.py`).

## Backups

The DB is `$STORY_AGENT_HOME/data/*.db` (WAL). Copy with `sqlite3 <db> ".backup <file>"`, never the bare file while a worker runs. Assets live under `projects/<slug>/media/`; the ledger is authoritative, the disk is verified against sha256.

## Upgrade and rollback

Migrations only go forward. Before a schema upgrade `storyagent db migrate` copies the database to `<db>.bak-v<N>` (the version it had). To roll back: stop the worker and the server, move the current `*.db` (and its `-wal`/`-shm`) aside, copy the snapshot back as the database, and check out the matching code revision (`git checkout <tag or commit>`). The v1 implementation is the git tag `v1-final`; v1 databases are never touched (`db import-legacy` only reads them).

## Team access and audit

Single operator (default): `[auth] mode = "off"`, no login. For a team: set `[auth] mode = "token"` in `$STORY_AGENT_HOME/config/auth.toml` (or `STORY_AGENT_AUTH=token`), restart `storyagent serve`, then create the first administrator on the server and the others from the web client (Team page) or the CLI:

```
storyagent user create NAME --role admin            # asks for the password (or --password-env VAR)
storyagent user list
storyagent user set NAME --role editor | --inactive | --reset-password
storyagent audit [--user U] [--action A] [--project N] [--limit N]
```

Forgot the only admin's password: `storyagent user set NAME --reset-password` on the server. A locked account (too many wrong passwords) opens again after `lockout_minutes`, or reset its password. Listening beyond loopback (`[api] host`) also needs HTTPS in front (a reverse proxy): the session cookie is marked `Secure` only when the request arrives over https. Every write and every sign-in event is in `audit_log` (Team > Audit in the web client); the table is append-only and holds no request bodies. Jobs keep their own full event history (`media show <id>`).

## Restricted content (vault on an encrypted volume)

A project of the `restricted` rating writes its files to `$STORY_AGENT_HOME/vault/` instead of `projects/`. To make that folder really private, put it on an encrypted volume **you** create and mount (the system never mounts, unmounts or encrypts anything):

1. Create an encrypted disk image (Disk Utility > New Image > Blank Image > encryption AES-256, or `hdiutil create -encryption AES-256 -size 200g -fs APFS -volname StoryVault ~/StoryVault.dmg`) and mount it.
2. Make the vault a link into it: `ln -s /Volumes/StoryVault/vault "$STORY_AGENT_HOME/vault"` (create `/Volumes/StoryVault/vault` first; do this before the first restricted project, or move the existing `vault/` folder across).
3. In `$STORY_AGENT_HOME/config/content.toml` set `[content] volume_mount = "/Volumes/StoryVault"`. While that path is not a mount point, restricted jobs fail with `vault_unavailable` (mount the volume, then retry the job from the queue); general projects are not affected.
4. Backups: `db migrate` snapshots include the ledger (paths, ratings, prompts of restricted jobs), **not** the vault files. Back the vault up by copying the (unmounted) disk image. Do the same for the database file if its prompts must stay private - it is not encrypted by the system.

Who can see what: with `[auth] mode = "off"` the unlock is only a guard against showing material by mistake; with accounts each user unlocks for themselves (adult confirmation + their password) for `unlock_ttl_sec`. `storyagent audit --action content.` lists unlock, lock, view, export and the adult declarations; failed unlocks are recorded too. Export of a restricted project always needs `--confirm-restricted` (CLI) / `confirm_restricted=true` (API) and, over the API, an active unlock.

## 生产部署与更新

生产 = 主检出目录(分支 `main`,保持干净)+ 自己的 `.venv` + 自己的 `data/`;开发在工作树里,互不影响(详见 HANDOFF.md §6)。

### 在系统里更新并热重启(推荐)

前提:服务是用 `scripts/start.sh` 起的(它是守护);登录用户是管理员(`[auth] mode = "off"` 时就是本机操作员)。

1. 网页「系统」页最下面的「更新与重启」卡片:显示当前版本(提交号 + 标题)、落后几个提交(附标题)、正在跑的任务;点「检查更新」会 `git fetch`。
2. 点「更新并重启」:按 `scripts/deploy.sh` 的步骤依次做 —— 获取远端 → 比较 → `git merge --ff-only` → `uv pip install -e .` → `pnpm install --frozen-lockfile` → `pnpm build` → `db migrate`(先自动快照)→ `i18n check`。每一步的成败、输出的最后几行都在卡片里;完整日志在 `<home>/logs/update-<时间>.log`。**任何一步失败就停在那里**:不重启,旧代码继续跑(合并之后才失败的话,检出已经在新提交上,卡片里有起点提交,必要时 `git reset --hard <起点>` 手动回退)。
3. 全部成功后请求重启:worker 立刻**停止领取新作业**,默认**等正在跑的任务做完**再退出;勾选「立即中断并重启」(会二次确认)则马上把运行中的作业放回队列(重任务会重做,或用适配器的续接),工作流在约一分钟内被新 worker 重新接手。等待期间卡片上有「立即中断并重启」和「取消重启」。
4. worker 和 server 各自以**退出码 75** 干净退出(没有任何杀进程/发信号的代码),`scripts/start.sh` 发现 75 就先做 `db migrate`、再把两个都重新拉起(并重新执行自己,start.sh 本身改了也会生效)。网页自动重连,恢复后提示「已更新并重启」并给出「重新载入页面」。
5. 没有守护(不是用 start.sh 起的,或另开终端跑的 `storyagent serve`):只更新、不重启,卡片和 `storyagent update run` 都会明确提示手动重启。
6. 守护标记:`start.sh` 启动时写 `<home>/update/supervisor.json`(含它的 pid),退出时删除;API 只在这个 pid 就是自己父进程时才认为有守护。请求/应答也是同目录的小文件(`restart.json`、`restart-worker.json`),状态在 `state.json`。

只有管理员能用(`/api/update` 是 `[auth] admin_prefixes`,路由里再校验一次);同一时间只允许一次更新;工作区必须干净;默认只在**主检出**(非 git 工作树、在 `main` 分支)启用,开发用的工作树会显示「不能更新」;`[update] enabled = true` 可强制开启,`false` 关闭(见 CONFIGURATION.md)。每次更新写审计:`update.start`、`update.finish`(状态、失败步骤、起止提交)、`update.restart`、`update.restart_cancel`,另有 HTTP 写请求的通用审计。

命令行等价物:`storyagent update status | check | run [--restart [--now]]`(`run` 不加 `--restart` 只更新)。`storyagent update marker --pid N [--clear]` 是 start.sh 内部用的。

退出码约定:`RESTART_EXIT_CODE = 75`(`storyagent/restart.py`),`scripts/start.sh` 里的 `RESTART_EXIT=75` 与之相同(`tests/test_update.py` 校验)。worker 或 server 以 75 退出 = 请求整体重启;其他退出码仍是整体停止(Ctrl-C 只停这两个)。

### 手动方式(没有守护,或网页不可用)

```bash
cd <主检出>
scripts/deploy.sh                 # 快进到 origin/main、安装、构建网页、迁移数据库(先自动快照)、检查 i18n;不重启任何东西
.venv/bin/python -m storyagent media jobs --status running     # 没有重任务在跑再重启(重启会打断它,之后被重投)
# Ctrl-C 停掉 scripts/start.sh,然后:
scripts/start.sh                  # 数据库迁移 + worker + server(端口 8765)
```
备份:`data/` 不进 Git。至少定期备份 `data/storyagent.db`(用 `sqlite3 data/storyagent.db ".backup 备份路径"`,服务在跑时也安全)和 `data/projects/`、`data/library/`、`data/config/`。
回滚:`git checkout <上一个提交>`,再 `scripts/deploy.sh` 的安装/构建步骤;数据库迁移前的快照在 `data/storyagent.db.bak-vN`。

首次升级到带这个功能的版本时,正在跑的旧 start.sh / worker / server 不认识重启请求:先用上面的手动方式更新并重启一次,之后才有热重启。
