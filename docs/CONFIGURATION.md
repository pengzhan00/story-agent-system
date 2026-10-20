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
| `services.toml` | `[services.<name>]` url/health_path/enabled/probe_only (comfy 8188, acestep 8001, ollama, llamacpp (off), fastvideo, nodus probe-only); `[tools.<name>]` binaries and model dirs (h3, longcat, cosyvoice, fastvideo, lora_train, ffmpeg); `[runtime]` worker knobs; `[healthcheck]` required + min_free_gib |
| `models.toml` | `[models.<ref>]`: kind, provider, family, version, heavy, est_memory_gib, license_tag, commercial_ok, attribution, prompt_profile, timeout_sec, `params`, `tiers.draft/preview/final`, extra (e.g. `train_template`); `[defaults]` kind -> default model |
| `recipes.toml` | `[recipes.<name>]` ordered `[[steps]]` (kind, model, tier, slot, params, when). Validated at load; executed from R3 |
| `compliance.toml` | `enabled`, `implicit_metadata`, `explicit_label` (`off|overlay|both`), `require_voice_consent`, `require_restricted_license_ack` |
| `llm.toml` | `[llm.backends.*]` (`ollama`, `llamacpp`, `openai_compat`; url comes from a service or `url`) and `[llm.roles.*]` (backend + model + params per agent role, `default` is the fallback) |
| `delivery.toml` | Delivery profiles and QC thresholds |

## Environment variables

| Variable | Effect |
|---|---|
| `STORY_AGENT_HOME` | Data/config root |
| `STORY_AGENT_CONFIG_DIR` | Override the config directory |
| `STORY_AGENT_LOCALE` | `zh-CN` or `en` |
| `STORY_AGENT_<SERVICE>_URL` | Override a service URL, e.g. `STORY_AGENT_COMFY_URL`, `STORY_AGENT_OLLAMA_URL` |
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

0 ok · 1 not found / health failing · 2 configuration · 3 migration · 4 legacy import · 5 spec locked.
