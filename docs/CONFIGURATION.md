# Configuration

Nothing is hard-wired: pipelines differ, so defaults are layered and every layer is optional.

## Layers (low -> high)

1. Packaged defaults `storyagent/config/defaults/*.toml`
2. `$STORY_AGENT_HOME/config/*.toml` (same file names; tables are deep-merged, so you only write what you change)
3. Environment variables
4. Project settings (`projects.settings` JSON)
5. Per job / per spec `params`

`$STORY_AGENT_HOME` defaults to `<repo>/data`. Inspect the result with `storyagent config show`.

## Files

| File | Purpose |
|---|---|
| `services.toml` | `[services.<name>]` url/health_path/enabled/probe_only (acestep 8001, comfy 8188 (Stable Audio 3 sound effects), ollama, phosphene 8198, llamacpp (off)); `[tools.<name>]` binaries and model dirs (h3, longcat, cosyvoice, lora_train, mflux, whisper, seedvc, ffmpeg); `[runtime]` worker knobs; `[healthcheck]` required + min_free_gib |
| `models.toml` | `[models.<ref>]`: kind, provider, family, version, heavy, est_memory_gib, license_tag, commercial_ok, attribution, prompt_profile, timeout_sec, `params`, `tiers.draft/preview/final`, extra (e.g. `train_template`); `[defaults]` kind -> default model |
| `recipes.toml` | `[recipes.<name>]` ordered `[[steps]]` (kind, model, tier, slot, params, when). Validated at load and listed in `/api/meta`; the workflow does **not** execute them (`produce` derives its steps from the specs) |
| `compliance.toml` | `enabled`, `implicit_metadata`, `explicit_label` (`off|overlay|both`), `require_voice_consent`, `require_likeness_consent`, `require_restricted_license_ack` |
| `llm.toml` | `[llm.backends.*]` (`ollama`, `llamacpp`, `openai_compat`; url comes from a service or `url`) and `[llm.roles.*]` (backend + model + params per agent role, `default` is the fallback) |
| `delivery.toml` | Delivery profiles (`[profiles.*]`: size, fps, codec, episode length range), `[mix]` (gains, loudnorm, slow-down limit), `[edit]` (scene fade, recap / title / next cards and their lengths; per project in `settings.edit`), `[subtitles]` (font, size, colours as RRGGBB, bold, `border_style` 1 outline / 3 box, `alignment`), `[qc]` thresholds |

`[tools.longcat] offline = true` (default) makes the avatar tool use only local files (no model-hub lookups, which hang without a route to the hub). `services.toml` also holds `[launch.<service>]` (how the launch screen / `storyagent ops launch [--all|NAME]` starts that service: `argv` list without a shell, `cwd`, `env`, `bulk`, `launcher`, `timeout_sec`, `hint_key`; only listed services get a Start button; the packaged defaults start ollama, ACE-Step (`.venv/bin/python3 -m acestep.api_server` in `~/ACE-Step-1.5`) and open the Pinokio app for Phosphene) and `[workflow]` (`video_share` (fraction of an episode's shots that get a video clip, default 0.2), `video_min_per_episode` (2), `video_budget_hours` (0 = no limit; clips in the whole series) - each overridable per project in `projects.settings` and per shot with `shots.treatment`; `keyframe_from_references` (shot keyframes are composed with `image.edit` from the made portraits and scene image; false = text-to-image only; measured ~11 min per keyframe against ~1.5 min for text-to-image, and the production plan shows that cost; each location can override it with `scenes.keyframe_mode` = `auto` (this default) / `compose` / `text`, edited in the Story tab), `tier`, `default_voice`, `poll_seconds`, `takes`) and `[api]` for `storyagent serve`: `host`, `port`, `cors_origins` (browser origins allowed to call it; the Vite dev server by default), `sse_poll_seconds`, `sse_keepalive_seconds`, `max_upload_mb` (largest accepted upload, default 2048), `web_dir` (built web client; empty = `web/dist`). See [API.md](API.md).

**One direction per project.** The picture frame (vertical, landscape, square) is the project's delivery profile (`delivery.toml [profiles.*]` width x height), chosen when the project is created (`POST /api/projects` / `from-text` `delivery_profile`, `project create --profile`, the web form) and the aspect ratio follows from it. Every picture and video size is then derived from it: the picture/video models list a size once (`models.toml`, pixel budget per tier) and the ledger turns it to the project's shape (`media/params.py orient_size`: same pixel count, 16 px grid for pictures, 32 px for video); explicit `projects.settings.models.<ref>` / job sizes still win. Switching between vertical and landscape is refused once the project has pictures (`409`); a frame of the same direction (720p -> 1080p) may still change. Differences between clips are handled in the edit (compose crops/pads), not by mixing directions at generation.

**Memory and the queue.** `[runtime] max_concurrent_jobs = 1` (default) makes the media queue strictly linear: one local job at a time, whatever its kind, in the order the production plan lists them (grouped by model, so a model is loaded once). Memory is handed back as soon as each job ends: subprocess tools (FLUX, CosyVoice, h3, LongCat, ffmpeg) exit; the ComfyUI adapter calls `/free` after every clip; Ollama drops the language model 30 s after our last call (`[llm.backends.ollama] keep_alive`, `"0"` = at once). What the system cannot release: the ACE-Step server keeps its models loaded while it runs (it has no unload call, and the system never stops a service) - stop it yourself when you are not making music.

## Environment variables

| Variable | Effect |
|---|---|
| `STORY_AGENT_HOME` | Data/config root |
| `STORY_AGENT_CONFIG_DIR` | Override the config directory |
| `STORY_AGENT_LOCALE` | `zh-CN` or `en` |
| `STORY_AGENT_<SERVICE>_URL` | Override a service URL, e.g. `STORY_AGENT_OLLAMA_URL`, `STORY_AGENT_ACESTEP_URL` |
| `STORY_AGENT_LLM_UNLOAD_POLICY` | `never|ask|auto` |
| `STORY_AGENT_COMPLIANCE` | `on|off` (also `1/0/true/false`); anything else is a config error |
| `STORY_AGENT_LEGACY_DB` | Source for the optional `db import-legacy` |
| `STORY_AGENT_ENABLE_FAKE` | `1` registers the fake adapter (tests/demos only) |

## Project overrides (`projects.settings`)

```json
{"compliance": {"enabled": false},
 "models": {"ltx-2.3-22b-distilled": {"fps": 24}}}
```

A project can switch compliance off or on regardless of the global value; unspecified keys inherit.

## Exit codes (CLI)

0 ok · 1 not found / health failing · 2 configuration · 3 migration · 4 legacy import · 5 spec locked · 6 export: a blocking QC check failed.


## 云端模型相关开关

`[runtime] allow_remote`(默认 false;项目设置 `allow_remote` 覆盖)、`max_remote_jobs`(默认 4)。云端模型条目与适配器配置见 `docs/CLOUD.md`。
