# Runbook

```bash
scripts/start.sh                      # worker + web server together; Ctrl-C stops only these two
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

## Symptoms

| Symptom | Cause / action |
|---|---|
| Job stays `queued`, message `heavy_busy` / `light_limit` / `memory_budget` | Normal arbitration; raise `memory_budget_gib` only if the machine really has room |
| Job stays `queued`, `llm_resident` | An LLM is loaded. `storyagent ops llm-unload`, or set `llm_unload_policy = "auto"` |
| Job `running` with no progress, worker dead | Wait `lease_seconds`; the next worker requeues and re-attaches (or re-submits). After `max_attempts` the job fails `worker_lost` |
| `missing_model` / `missing_tool` | `media healthcheck` names the file; downloads are never automatic |
| `consent_required` / `license_ack_required` | Record voice consent on the character / acknowledge the licence in project settings, or turn compliance off for the project |
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


## 生产部署与更新

生产 = 主检出目录(分支 `main`,保持干净)+ 自己的 `.venv` + 自己的 `data/`;开发在工作树里,互不影响(详见 HANDOFF.md §6)。

```bash
cd <主检出>
scripts/deploy.sh                 # 快进到 origin/main、安装、构建网页、迁移数据库(先自动快照)、检查 i18n;不重启任何东西
.venv/bin/python -m storyagent media jobs --status running     # 没有重任务在跑再重启(重启会打断它,之后被重投)
# Ctrl-C 停掉 scripts/start.sh,然后:
scripts/start.sh                  # 数据库迁移 + worker + server(端口 8765)
```
备份:`data/` 不进 Git。至少定期备份 `data/storyagent.db`(用 `sqlite3 data/storyagent.db ".backup 备份路径"`,服务在跑时也安全)和 `data/projects/`、`data/library/`、`data/config/`。
回滚:`git checkout <上一个提交>`,再 `scripts/deploy.sh` 的安装/构建步骤;数据库迁移前的快照在 `data/storyagent.db.bak-vN`。
