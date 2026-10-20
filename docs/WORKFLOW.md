# Story workflow (R3)

One sentence in, an editable production out: characters, locations, script, shots, music/theme/sfx and — for every one of them — a **generation spec** (prompt, parameters, seed, references) that a person can edit and regenerate. The workflow writes the story and drafts the specs; the media layer (`docs/MEDIA_CONTRACT.md`) turns specs into pictures, voices, music and video.

```
analyze → cast → script → scenes → music → shotplan → specs
```

| Stage | What it does | Agents (LLM roles) |
|---|---|---|
| `analyze` | premise → production plan (genre, tone, acts, cast and place lists) and visual style | `director.analyze`, `art.style` |
| `cast` | one design per character (look, voice profile, image prompt, voice sample text) | `character.design` |
| `script` | outline of acts and scenes, then a shot breakdown per scene; or `source_text` adapted into the same structure | `screenwriter.outline` / `screenwriter.adapt`, `screenwriter.shots` |
| `scenes` | one design per location | `scene.design` |
| `music` | background cues, a theme song with lyrics, sound-effect list | `composer.bgm`, `composer.theme`, `sound.design` |
| `shotplan` | deterministic: builds every shot's `shot.v3` render payload (timing from dialogue, camera, subject, style) | – |
| `specs` | deterministic (+ optional `shot.visual` per shot): seeds the editable specs, with dependencies | `shot.visual` |

Agents are **data**: `storyagent/agents/specs/*.toml` (+ `schemas/*.json`). Copy one into `$STORY_AGENT_HOME/config/agents/` to change a prompt or model role without touching code. Each agent runs on an LLM *role* routed by `llm.toml` to a backend (ollama / llamacpp / openai_compat); output is requested as schema-constrained JSON, repaired if needed, validated and re-asked up to `max_attempts` times.

## Rules that matter

* **Resumable.** Every LLM result is saved as soon as it arrives (each scene breakdown, each character). A run that fails or is cancelled keeps everything done so far; running again only produces what is missing.
* **Stale propagation.** Completing a stage marks the stages that depend on it `stale` (`DOWNSTREAM`); a resume run redoes stale and pending stages. Explicitly re-running `analyze` re-plans by design; `force=true` redoes stages whose output exists.
* **Never overwrite people.** Specs seeded by the workflow have `author='agent'`. A spec a person edited (`author='user'`) or locked is never touched on re-seeding; agent drafts follow the story (new version). Locked shots survive `shotplan`; every other shot is rebuilt **in place**, so its id — and the specs and assets attached to it — stay.
* **Unknown speakers.** A dialogue line whose speaker matches no character is reported (`unresolved_speakers`) and shown with `character_id: null`; reassign it or add the character.
* **One active run per project**, executed by the worker (`storyagent worker`) or inline (`storyagent workflow run --inline`).

## Progress you can see

A run publishes `progress` (see `docs/API.md`): overall percent, per-stage bars, items done/total, the current message, elapsed time and ETA. Stage weights (`workflow/progress.py: STAGE_WEIGHTS`) only shape the overall bar; inside a stage the bar is exact. The terminal shows the same data:

```
[##########--------------]  42.0%  Step 3 of 7  Script  2 of 4 - scene 2  | Elapsed 1m20s, About 1m50s left
```

## Commands

```bash
storyagent llm check                                   # is every role's model installed?
storyagent workflow run --project 1 [--stage script] [--param episode=2] [--param source_text="..."] [--force]
storyagent workflow run --project 1 --inline           # no worker needed; draws the progress bar
storyagent workflow status --project 1                 # stage states (+ per-stage bars of the active run)
storyagent workflow watch [RUN_ID] [--project 1]       # follow a queued/running run
```

Params: `premise`, `acts`, `episode`, `source_text`, `visual_prompts` (false = deterministic prompts, no extra LLM call per shot), `min_shot_sec`, `max_shot_sec`, `force`.

## i18n key list for the UI

`workflow.stage.<stage>`, `workflow.status.<pending|queued|running|completed|failed|stale|cancelled>`, `workflow.progress.{stage_of,items,elapsed,eta,eta_unknown,working,queued}`, run `error_code` → `llm.error.<code>` / `workflow.error.<code>` (+ `.remediation`), API errors `api.error.{shot_locked,nothing_to_do}`. `GET /api/meta` returns `workflow_stages` and `workflow_statuses` with labels.
