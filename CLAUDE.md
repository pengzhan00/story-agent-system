# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local, offline "idea → short-drama MP4" system (漫剧故事工坊 / Story Agent System). Ollama LLM agents write the script, then ComfyUI (LTX 2.3 video, ACE-Step music) + TTS + ffmpeg render and compose it. Persistence is SQLite; UI is Gradio. Target is vertical 720×1280 short dramas. macOS / Apple Silicon only in practice. Docs and most comments are in Chinese.

## Rewrite in progress (v2)

The legacy code (`core/ agents/ pipelines/ ui/ main.py`, described below) is frozen and being replaced by the new `storyagent/` package, built milestone by milestone (R0–R7; plan and review in `docs/reviews/`, feature-by-feature tracking in `docs/PARITY_MATRIX.md`). Do not extend legacy code; new work goes in `storyagent/`. R0 (done) provides: `config/` (settings from packaged TOML defaults < `$STORY_AGENT_HOME/config/*.toml` < env; no hard-coded paths/ports), `i18n/` (all user-visible text via `t(key)` with `locales/{zh-CN,en}.json`), `db/` (versioned SQL migrations, automatic pre-upgrade snapshot, read-only `legacy_import`), `domain/enums.py` (`MediaKind` as `<domain>.<operation>`, `JobStatus`, `QualityTier`), `cli.py`.

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[dev]'   # first time
.venv/bin/pytest                      # unit/contract tests (live tests need `-m live`)
.venv/bin/pytest tests/test_config.py::test_defaults   # single test
.venv/bin/ruff check storyagent tests --exclude tests/legacy && .venv/bin/mypy
.venv/bin/python -m storyagent db migrate | status | import-legacy --from <v1 db> ; config show ; i18n check
```

Rules enforced by `tests/test_guards.py`: no `/Users/`, `~/myworkspace`, `127.0.0.1`/`localhost` literals, `pkill`/`shell=True`, or CJK string literals inside `storyagent/`. Services/tools/models come from `Settings` (`storyagent/config/defaults/*.toml`). `tests/legacy/` holds the old run-at-import scripts (excluded from pytest, deleted in R7).

## Legacy system (frozen)

### Commands

The legacy code has no test runner or linter. Its old scripts (now in `tests/legacy/`) are **standalone scripts**, not a pytest suite: they run at import time, hit live ComfyUI/Ollama/ffmpeg, and read pre-existing DB rows and `output/` assets at hardcoded `/Users/pengzhan/...` paths. Run one with `python3 tests/legacy/test_e2e_quick.py`; expect them to fail without the local services and models. Cheap sanity check after editing: `python3 -m py_compile <file>`.

```bash
./setup.sh                  # create .venv, install requirements.txt, write .env
./start.sh                  # start Ollama check + ComfyUI + Gradio (--check / --restart / --comfyui / --env)
python3 main.py             # Gradio main UI (7860; auto-increments if busy, GRADIO_SERVER_PORT overrides)
python3 main.py --render    # standalone render UI (ui/render_app.py, 7861)
python3 main.py --workshop  # storyboard workshop UI (ui/workshop.py, 7861)
python3 main.py --check     # environment check (Ollama, DB, output dir)
python3 main.py --cli       # list projects + env; --demo summarizes projects

# every agent is its own CLI
python3 agents/writer/main.py --action generate_storyline --input '{"premise":"..."}' --project-id 1
python3 agents/<name>/main.py --task-mode --action <action>   # run as task-queue worker instead
```

`.env` (git-ignored, loaded by `main.py:_load_local_env` without overriding real env vars) supplies `COMFYUI_URL`, `COMFYUI_DIR`, `COMFYUI_PYTHON`, `UI_PORT` (read by `start.sh` only; `main.py` uses `GRADIO_SERVER_PORT`), `ACESTEP_*`, etc. ComfyUI must run with **its own** venv (`$COMFYUI_DIR/.venv`, has torch); using the project venv yields confusing node-registration errors. `./start.sh --env` diagnoses this.

## Architecture

Three layers; read across them to understand a feature:

- **`core/`** — infrastructure. `database.py` (single shared SQLite connection behind an `RLock`, WAL; dataclass models in `models.py` mapped through `_table_map`/`_json_fields`, JSON columns auto-(de)serialized), `ollama_client.py` (`generate`/`generate_json` with heavy JSON-repair helpers for flaky LLM output; per-stage model profile), `orchestrator.py` (the pipeline), `task_queue.py` (DB-backed queue + `TaskWorker` for agent processes), `pipeline_state.py` (resume state is **derived from DB + files on disk**, not a separate state file), `asset_registry.py`, `service_ports.py` (ComfyUI/ACE-Step URL discovery and auto-launch), `comfyui_env.py`.
- **`agents/<name>/`** — one folder per agent, each with `core.py` (prompts + business logic, exposes `run_action(action, input, project_id, task_id)`) and `main.py` (argparse CLI + optional task-queue worker). Agents call `core.ollama_client` and write results via `core.database`. `concept_finder` is the exception: just a `core.py` helper, no CLI and no `run_action`.
- **`pipelines/`** — media generation. `render_pipeline.py` (ComfyUI workflow builders), `audio_pipeline.py` (TTS chain + ACE-Step music + SFX), `compositor.py` (ffmpeg shot→episode mixing/subtitles), `quality_gate.py`, `batch_renderer.py`, `quick_video.py` (the "快速做视频" shortcut flow), `output_manager.py`.
- **`ui/app.py`** (~5.4k lines) is the main Gradio app and calls the orchestrator and pipelines directly; `render_app.py`/`workshop.py` are the 7861 apps.

### Orchestrator flow

`core/orchestrator.py:run_pipeline_generator` is a **generator yielding `(percent, message)`** that the UI streams. Stages: director analysis → script → characters → scenes → art/music/SFX → shot plan (`_create_shot_plan_txn` builds each shot's `render_payload`) → render → TTS/BGM → compose → export (`DeliveryPackage`). `run_stage_*` functions run single stages; `resume_pipeline` and `request_render_stop` support continuation/cancel.

### Render dispatch

`pipelines/pipeline_config.json` is the source of truth. `RenderDispatcher.from_config` instantiates the classes listed there (looked up in `_PIPELINE_CLASSES`), sorts by `priority`, and `active_pipeline` picks the requested one (currently `ltx_i2v`). Workflow JSONs (`pipelines/ltx_*_workflow*.json`) are loaded and patched per shot. After each render, `quality_gate.validate_render_output` runs; fallback to the next pipeline (up to 2 retries, ending at `stub`) happens **only if** the payload sets `allow_fallback=true` or `STORY_AGENT_ALLOW_RENDER_FALLBACK=1` — by default a production-pipeline failure raises. The Wan2.* pipeline classes still exist in `render_pipeline.py` but are not in the config.

### Audio

TTS tries backends in ranked order (`_ranked_tts_backends`: ChatTTS in a separate venv at `~/chattts` → edge_tts → bark → kokoro → pyttsx3). Music goes through ACE-Step via ComfyUI using `pipelines/audio_ace_step1_5_xl_{sft,turbo}.json`. LTX's own audio track was dropped from render output (Audio VAE noise, commit e00c490); `compositor.compose_shot` mixes TTS + BGM + SFX over the video instead.

## Gotchas

- **Hardcoded paths.** `core/database.py:DB_PATH` is `~/myworkspace/projects/story-agent-system/data/story_agents.db` (the *main checkout*, not the cwd), so any worktree or clone shares that DB. `main.py:check_environment` and most tests hardcode the same root. Models live in `~/myworkspace/ComfyUI_models/` (mapped into ComfyUI via `extra_model_paths.yaml`), not in this repo.
- **Docs drift.** `ARCHITECTURE.md` is labelled the source of truth but is partly stale: it says port 8188 and `active_pipeline: wan2_ti2v`, and the README lists the same 8188/gemma setup. Actual code: ComfyUI is auto-launched on **13201** and ACE-Step on **13200** (`core/service_ports.py`, URL override via `STORY_AGENT_COMFYUI_URL`/`COMFYUI_URL`), while `start.sh` still probes 8188. Trust the code and `pipeline_config.json` over the prose; update `ARCHITECTURE.md` when changing pipeline behavior.
- `capability_audit.json` is a manually refreshed snapshot of local models, not read at runtime.
- `.gitignore` excludes `*.db`, `output/`, `.env`, `.claude/`, and model/asset dirs — never expect these in commits.
