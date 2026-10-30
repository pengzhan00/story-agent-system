# Architecture (v2)

Single-user, local, offline. Two long-lived processes share one SQLite database (WAL) and never call each other:

```
 browser ──HTTP/SSE──▶ storyagent serve  (FastAPI: reads/writes the ledger; serves web/dist)
                              │
                         SQLite (WAL)  ◀── the only thing the two processes share
                              │
                       storyagent worker  (executes media jobs and workflow runs, heartbeats, reattaches after a crash)
                              │
        adapters: ComfyUI-free; HTTP (acestep, phosphene) · subprocess (mflux, cosyvoice, seedvc, whisper, h3,
                  lora_train) · ffmpeg runner (assemble)
```

Either process can restart at any time: queued work stays in the database, running work is recovered through leases.

## Layers

| Layer | Package | Responsibility |
|---|---|---|
| Config | `config/` | Layered TOML + env (`Settings`); no hard-coded paths/ports (enforced by `tests/test_guards.py`) |
| i18n | `i18n/`, `locales/` (inside the package) | `t(key)` everywhere; the web client downloads the same catalog (`/api/i18n/{locale}`) |
| Persistence | `db/` | Versioned, checksummed SQL migrations (001-004), automatic pre-upgrade snapshot, optional read-only v1 import |
| Domain | `domain/enums.py` | `MediaKind` (`<domain>.<operation>`), `JobStatus`, `QualityTier`, `ErrorCode` |
| Media core | `media/` | `contracts` (MediaJob, Adapter protocol) · `ledger` (jobs, versioned assets, leases, idempotency) · `specs` (editable creative specs, stale propagation, regenerate) · `broker` (one heavy job at a time, memory budget, LLM-resident policy) · `worker` · `healthcheck` · `compliance` · `library` (cross-project voices/refs/LoRAs) · `params` (layered parameters) |
| Adapters | `media/adapters/` | One class per tool; shared subprocess/HTTP plumbing; outputs always verified with ffprobe before an asset is registered |
| LLM | `llm/`, `agents/` | Backends (ollama, openai-compatible, llama.cpp) routed per role; agents are TOML + JSON schema, schema-constrained output with repair and re-ask |
| Story | `story.py`, `projects.py` | Characters, locations, music, shots, dialogue, scripts; project records incl. recycle bin |
| Workflow | `workflow/` | Nine resumable stages, run queue, progress tracker (percent never regresses, ETA), produce (specs → jobs), compose |
| Compose | `compose/` | `timeline` (pure EDL), `qc`, `export` (delivery package) |
| API | `api/` | FastAPI routes, one error envelope, SSE; OpenAPI is the contract for `web/` |
| Web | `web/` | React + TypeScript + TanStack Query; types generated from OpenAPI |

## The ideas that hold it together

1. **Spec, job, asset.** A *spec* is what a person edits (prompt, parameters, seed, references, locked or not; append-only versions). A *job* is one execution, a frozen snapshot of a spec version. An *asset* is a file the ledger vouches for (sha256, ffprobe facts, provenance, version, at most one `current` per slot; the rest are candidates). Editing a spec never touches finished assets; they just show up as stale.
2. **The ledger is authoritative.** "Completed" means the output file exists, is non-empty, and ffprobe can read it. Disk is checked against the ledger, never guessed from globs.
3. **Resumable everywhere.** Every LLM result is saved as it arrives; every job has a lease + heartbeat; a dead worker's jobs are requeued and re-attached (or re-submitted by idempotency key) without duplicates.
4. **Nothing silently degrades.** A missing model or tool is `missing_model` / `missing_tool` with a named remedy; produce reports what it skipped; compose turns gaps into explicit warnings.
5. **Everything is configurable, nothing is hard-wired.** Pipelines differ per machine and per project: parameters, tiers, models, LLM roles, QC thresholds, compliance switches are data.
6. **Compliance is a switch, provenance is not.** Labels, consent checks and licence gates are on by default and can be turned off per scope; provenance is always recorded.
7. **Progress is never a mystery.** Runs and jobs publish live progress (overall percent, per-stage bars, item counts, current message, ETA) through the database; the CLI and the web client draw the same data.

## Data flow of one episode

```
premise ─▶ analyze ─▶ cast ─▶ script ─▶ scenes ─▶ music ─▶ shotplan ─▶ specs      (LLM; all rows editable)
specs ─▶ produce ─▶ jobs ─▶ worker ─▶ adapters ─▶ assets (portraits, keyframes, voices, music, videos)
assets ─▶ compose: timeline (speech measured first) ─▶ assemble job ─▶ episode.mp4 + .srt ─▶ QC ─▶ export package
```

See [WORKFLOW.md](docs/WORKFLOW.md), [MEDIA_CONTRACT.md](docs/MEDIA_CONTRACT.md), [CONFIGURATION.md](docs/CONFIGURATION.md), [API.md](docs/API.md), [RUNBOOK.md](docs/RUNBOOK.md). Review records: `docs/reviews/`.
