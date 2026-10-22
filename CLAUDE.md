# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local, offline "idea → short-drama MP4" system (漫剧故事工坊 / Story Agent System), v2. LLM agents write the story; media adapters (FLUX/mflux images, CosyVoice/Seed-VC voices, ACE-Step music, Phosphene/h3/LongCat video, ffmpeg assembly) turn editable *specs* into assets; the episode is composed with ffmpeg, QC'd and exported with AIGC marking and provenance. SQLite persistence, FastAPI + SSE API, React/Vite web client. Target is vertical 720×1280 dramas on macOS / Apple Silicon. Docs and many comments are in Chinese. The v1 implementation (Gradio, ComfyUI-centred) is gone from the tree; it lives in git tag `v1-final`, and `docs/PARITY_MATRIX.md` says where each old feature went.

**Taking over? Read `docs/HANDOFF.md` first** (sources of truth, data structures, architecture, local environment, what is done and what is next). The end goal is a continuous vertical short-drama series: `docs/SERIES.md`.

## Commands

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'   # first time
.venv/bin/pytest                                   # unit/contract tests (live tests need `-m live`)
.venv/bin/pytest tests/test_config.py::test_defaults   # single test
.venv/bin/ruff check storyagent tests && .venv/bin/mypy
(cd web && pnpm install && pnpm typecheck && pnpm lint && pnpm test && pnpm build)   # web client
.venv/bin/python -m storyagent api openapi --out web/openapi.json && (cd web && pnpm gen:api)   # after changing API schemas
scripts/start.sh                                   # worker + server (Ctrl-C stops only these two)

.venv/bin/python -m storyagent db migrate | status | import-legacy --from <v1 db> ; config show ; i18n check
.venv/bin/python -m storyagent project create|list [--trashed]|trash ID [--restore]
.venv/bin/python -m storyagent media healthcheck | submit | jobs | show | cancel | retry | adopt ; asset import ; library add|list|show|remove ; spec create|show|edit|diff|revert|lock|unlock|regenerate
.venv/bin/python -m storyagent worker [--once] ; serve [--host --port] ; llm check ; ops llm-unload ; ops launch [--all|NAME]
.venv/bin/python -m storyagent workflow run [--stage S] [--param k=v] [--inline] | status | watch | review ; export episode --project N --out DIR
STORY_AGENT_ENABLE_FAKE=1 registers the fake adapter for local end-to-end runs (see tests/test_cli_media.py)
```

Python code changes are picked up only after restarting the worker and the server (long-lived processes).

## Architecture (read across files)

Two processes share SQLite (WAL) and never call each other: `storyagent serve` (API + built web client) and `storyagent worker` (executes media jobs *and* workflow runs). Full picture in `ARCHITECTURE.md`; per-area docs in `docs/` (`WORKFLOW`, `MEDIA_CONTRACT`, `CONFIGURATION`, `API`, `RUNBOOK`).

- **Spec → job → asset** (`media/`): specs are editable, append-only, lockable (`specs.py`); a job is a frozen snapshot of a spec version (`ledger.py`, idempotent, leased, re-attachable); an asset is a verified file with provenance, at most one `current` per (project, shot, kind, slot) (`SpecStore.job_slot` gives non-shot subjects their own slot). `asset:<id>` / `library:<role>/<name>` references anywhere in job inputs are resolved by the worker (`job:<id>` = the main output of a completed job). "Completed" = output exists and ffprobe reads it. A job needs no project: `workshop.py` (`/api/workshop`, web 工坊) runs each module standalone from presets (`workshop_presets`) or as a pipeline whose steps wait on each other (`media_jobs.depends_on`; a failed step cancels the ones waiting).
- **Adapters** (`media/adapters/`, registered in `media/registry.py`): subprocess ones share `_subprocess.py`; heavy jobs are serialised by `broker.py`. Missing model/tool → `missing_*` error, never a silent fallback.
- **Workflow** (`workflow/`): stages `analyze, cast, outline, script, scenes, music, shotplan, specs, produce, compose`; a plain resume runs `DEFAULT_STAGES` (everything but `outline`, `produce`, `compose`; `outline` joins when the season plans more than one episode); series > season > episode: `analyze`/`cast` are kept once, `outline` per season, the rest per episode (`workflow_stages` is keyed by season + episode), and a run can cover `params.episodes` / `params.season` (see `docs/SERIES.md`); `DOWNSTREAM` marks stale stages; one active run per project, run by the worker's workflow pool. `RunProgress` (percent never regresses, ETA) is written ~1/s; stage messages may be `<i18n key>|<detail>`. Re-planning shots updates unlocked shots in place so ids/specs/assets survive.
- **Compose** (`compose/`): `timeline.py` is a pure EDL builder (speech measured first), `media/runners/assemble_episode.py` (stdlib only) renders with ffmpeg, `qc.py` measures the result, `export.py` builds the delivery package. A visible AIGC label needs ffmpeg with libass (ffmpeg-full) or the job is refused.
- **API/Web**: routes in `api/routes_*.py`, one error envelope (`errors.py`, codes → `api.error.<code>` keys), SSE feed; `web/` types are generated from OpenAPI and its i18n catalog is fetched from `/api/i18n/{locale}`.

Config layers (low → high): packaged `storyagent/config/defaults/*.toml` < `$STORY_AGENT_HOME/config/*.toml` < env < `projects.settings` < job/spec params. Compliance (labels, consent, licence gates) defaults on and can be switched off globally, by `STORY_AGENT_COMPLIANCE`, or per project; provenance is always recorded.

## Rules enforced by tests

`tests/test_guards.py`: inside `storyagent/` no `/Users/`, `~/myworkspace`, `127.0.0.1`/`localhost` literals, `pkill`/`shell=True`, or CJK string literals. Services/tools/models come from `Settings`; prompts live in TOML/JSON; every user-visible string is an i18n key in `storyagent/locales/{zh-CN,en}.json` (`storyagent i18n check` must stay green; the web has `react/jsx-no-literals`). Never add code that kills or starts processes belonging to someone else. The only thing the system starts is what the operator listed under `[launch.<service>]` in `services.toml` and asked for (launch screen / `ops launch`). It stops a service only when that entry says `stop = "port"` and the operator clicks Stop / runs `ops stop NAME`: SIGTERM to the process of the same user listening on the service's port (SIGKILL only with force), refused while a job of that service waits. `os.kill` exists once, in `Launcher._signal` (`tests/test_guards.py` enforces it); nothing else may signal a process (`storyagent/launcher.py`).

## Gotchas

- `data/` and `$STORY_AGENT_HOME` hold the live database and media; never commit them (`.gitignore` covers `*.db`, `data/`, `.env`, `.claude/`).
- ffmpeg on `PATH` may lack `subtitles`/`ass`; `[tools.ffmpeg] binary` points at ffmpeg-full for burn-in and labels.
- Tests that need real ffmpeg skip when it is missing; adapter tests use stub tools plus tiny real media made with ffmpeg.
- A change to a schema-bearing response model needs `Strict` (see `api/schemas.py`) so the generated TypeScript has required fields.
