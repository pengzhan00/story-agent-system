# Workflow: story, media, episode

One sentence in, an editable production out: characters, locations, script, shots, music/theme/sfx and — for every one of them — a **generation spec** (prompt, parameters, seed, references) that a person can edit and regenerate. The workflow writes the story and drafts the specs; the media layer (`docs/MEDIA_CONTRACT.md`) turns specs into pictures, voices, music and video.

```
analyze → cast → (outline) → script → scenes → music → shotplan → specs → produce → compose
└────────────── story (default run) ───────────────────┘  └─ media ─┘ └ episode ┘
```

A plain resume runs the seven story stages only; `produce` and `compose` are started on purpose (Web buttons "生成素材" / "合成成片" / "一键做完整部", or `--stage produce`).

| Stage | What it does | Agents (LLM roles) |
|---|---|---|
| `analyze` | premise → production plan (genre, tone, acts, cast and place lists) and visual style | `director.analyze`, `art.style` |
| `cast` | one design per character (look, voice profile, image prompt, voice sample text) | `character.design` |
| `outline` | season: one request plans every episode (title, synopsis, opening hook, cliffhanger); optional, part of a plain resume only when the season plans more than one episode; the script stage then follows the episode's entry | `screenwriter.season` |
| `script` | outline of acts and scenes, then a shot breakdown per scene; or `source_text` adapted into the same structure | `screenwriter.outline` / `screenwriter.adapt`, `screenwriter.shots` |
| `scenes` | one design per location | `scene.design` |
| `music` | background cues, a theme song with lyrics, sound-effect list | `composer.bgm`, `composer.theme`, `sound.design` |
| `shotplan` | deterministic: builds every shot's `shot.v3` render payload (timing from dialogue, camera, subject, style) | – |
| `specs` | deterministic (+ optional `shot.visual` per shot): seeds the editable specs, with dependencies | `shot.visual` |

| `produce` | submits a media job for every spec that has no up-to-date asset, in dependency order: (1) portraits, scene keyframes, music, theme song, sound cues, dialogue voices → (2) shot keyframes → (3) shot videos (from the keyframe). Skips what is current; reports what it had to skip (`no_voice`, `no_model`, `no_keyframe`); jobs that fail fail the stage but the rest is kept | media adapters via the ledger/worker |
| `compose` | builds the timeline (speech first, see below), renders `episode.mp4` + `episode.srt` with ffmpeg, then runs QC | `compose.assemble` adapter |

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


## Produce and compose

* `produce` params: `tier` (draft/preview/final; default `[workflow] tier`), `skip` (list of spec subject types, e.g. `["shot.video"]`), `force`, `takes`. Dialogue needs the character's `voice_ref_audio` (upload a sample in the Web "故事" page) or `[workflow] default_voice` in `services.toml`.
* `compose` timeline (`compose/timeline.py`, pure and tested): the dialogue is generated first and measured with ffprobe; a shot lasts `max(planned, lead-in + lines + gaps + tail)`. A clip that is too short is slowed down (up to `max_slowdown`) then holds its last frame; one that is too long is trimmed; a still image gets a slow push-in; a shot with no picture becomes a black card. Every improvisation is a **warning** in the report (`no_picture`, `no_video`, `missing_speech`, `no_bgm`) - the episode still renders.
* Audio: music bed ducked under the voices (sidechain), loudness normalised to `[mix] loudnorm_i` (-16 LUFS), skipped when there is nothing audible.
* **AIGC marking** follows `compliance.toml` / project settings: `implicit_metadata` writes a `comment` tag into the container; `explicit_label = overlay` burns a small label (top-left, text from the catalog key `compliance.label.aigc` in the project's content language) over the whole film; `both` adds a two-second end card. A label needs an ffmpeg with libass (`ffmpeg-full`): without it the job is refused with `missing_tool` rather than delivered unlabelled.
* **QC** (`compose/qc.py`, `[qc]` in `delivery.toml`): file size, duration, resolution, aspect vs the delivery profile, audio track, mean volume, loudness (±3 LU around the target), black share, frozen picture. `episode_length` against the profile range is advisory (drafts are short). The result is in the compose stage summary and shown under the player.
* **Export**: `storyagent export episode --project N --out DIR` or `GET /api/projects/{id}/episodes/{n}/package` (zip): `episode.mp4`, `episode.srt`, `stems/` (music + each spoken line as used), `provenance.json` (per source asset: model, version, licence, attribution - no prompts, no paths), `manifest.json` (sha256 per file + QC). Rebuilt from the ledger, so it always describes what was rendered.
