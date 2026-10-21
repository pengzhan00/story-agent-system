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
| `services.toml` | `[services.<name>]` url/health_path/enabled/probe_only (acestep 8001, ollama, phosphene 8198, llamacpp (off)); `[tools.<name>]` binaries and model dirs (h3, longcat, cosyvoice, lora_train, mflux, whisper, seedvc, ffmpeg); `[runtime]` worker knobs; `[healthcheck]` required + min_free_gib |
| `models.toml` | `[models.<ref>]`: kind, provider, family, version, heavy, est_memory_gib, license_tag, commercial_ok, attribution, prompt_profile, timeout_sec, `params`, `tiers.draft/preview/final`, extra (e.g. `train_template`); `[defaults]` kind -> default model |
| `recipes.toml` | `[recipes.<name>]` ordered `[[steps]]` (kind, model, tier, slot, params, when). Validated at load and listed in `/api/meta`; the workflow does **not** execute them (`produce` derives its steps from the specs) |
| `compliance.toml` | `enabled`, `implicit_metadata`, `explicit_label` (`off|overlay|both`), `require_voice_consent`, `require_likeness_consent`, `require_restricted_license_ack` |
| `llm.toml` | `[llm.backends.*]` (`ollama`, `llamacpp`, `openai_compat`; url comes from a service or `url`) and `[llm.roles.*]` (backend + model + params per agent role, `default` is the fallback) |
| `delivery.toml` | Delivery profiles (`[profiles.*]`: size, fps, codec, episode length range), `[mix]` (gains, loudnorm, slow-down limit), `[subtitles]`, `[qc]` thresholds |

`[tools.longcat] offline = true` (default) makes the avatar tool use only local files (no model-hub lookups, which hang without a route to the hub). `services.toml` also holds `[launch.<service>]` (how the launch screen / `storyagent ops launch [--all|NAME]` starts that service: `argv` list without a shell, `cwd`, `env`, `bulk`, `launcher`, `timeout_sec`, `hint_key`; only listed services get a Start button; the packaged defaults start ollama, ACE-Step (`.venv/bin/python3 -m acestep.api_server` in `~/ACE-Step-1.5`) and open the Pinokio app for Phosphene) and `[workflow]` (`tier`, `default_voice`, `poll_seconds`, `takes`) and `[api]` for `storyagent serve`: `host`, `port`, `cors_origins` (browser origins allowed to call it; the Vite dev server by default), `sse_poll_seconds`, `sse_keepalive_seconds`, `max_upload_mb` (largest accepted upload, default 2048), `web_dir` (built web client; empty = `web/dist`). See [API.md](API.md).

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
