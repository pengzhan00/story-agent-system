# Runbook

```bash
storyagent db migrate                 # idempotent; snapshots *.bak-v<N> before upgrading
storyagent media healthcheck          # one screen; exit 0 = every required entry ok, 1 = not, 2 = bad config
storyagent media healthcheck --json --require tool.h3     # cron-friendly
storyagent serve                      # HTTP API (+ /api/docs); run next to the worker, in any order
storyagent worker                     # long-running; SIGTERM/SIGINT release leases cleanly
storyagent worker --once              # drain the queue and exit
storyagent media jobs [--status running]   |  media show <id>  |  media cancel <id>  |  media retry <id> [--new-seed]
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
| `separate_vocals` job fails in Demucs | `~/venvs/seedvc/bin/demucs` needs the `sphn` package (missing on 2026-10-21); install it in that venv |
| `train.lora` ends `output_missing` | mflux-train writes `<output>_<timestamp>/checkpoints/*_checkpoint.zip`; check the job log for a crash before the first checkpoint (`save_frequency`) |
| Output exists but job failed `output_invalid` | Zero-byte or unreadable by ffprobe; see `media show <id>` events |

## Recovery verified

`kill -9` of the worker mid-job: lease expires -> `lease_expired_requeued` -> `claimed` -> `reattached` -> `completed`; exactly one asset (checked manually and in `tests/test_worker.py`).

## Backups

The DB is `$STORY_AGENT_HOME/data/*.db` (WAL). Copy with `sqlite3 <db> ".backup <file>"`, never the bare file while a worker runs. Assets live under `projects/<slug>/media/`; the ledger is authoritative, the disk is verified against sha256.
