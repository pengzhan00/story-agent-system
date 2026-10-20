# Media contract (R1)

How anything that generates a file (image, video, voice, music, ...) plugs into Story Agent.
Code: `storyagent/media/`. Config: [CONFIGURATION.md](CONFIGURATION.md). Operations: [RUNBOOK.md](RUNBOOK.md).

## Concepts

| Concept | What it is | Mutable? |
|---|---|---|
| **Spec** (`gen_specs`) | What the creator edits: prompt, negative prompt, text payload (dialogue / lyrics), model, seed, params, refs. One per *subject* (`character.portrait`, `character.voice`, `dialogue.line`, `scene.keyframe`, `shot.keyframe`, `shot.video`, `music.bgm`, `music.theme_song`, `sfx.cue`, `style.guide`, `lora.training`) + slot. | Append-only: an edit is a new version; revert = new version copying an old one; `locked` blocks edits |
| **Job** (`media_jobs`) | One execution, a frozen snapshot of the resolved prompt / params / seed / refs. Survives restarts. | State only |
| **Asset** (`media_assets`) | A produced file: relative path, sha256, size, ffprobe facts, provenance, version. At most one `is_current` per (project, shot, kind, slot); the rest are candidates. | Only `is_current` / `trashed_at` |
| **Adapter** | Wrapper around one runtime (ComfyUI, ACE-Step, CosyVoice, h3, ...). Never touches the DB. | n/a |

`MediaKind` is `<domain>.<operation>`: `image.generate image.edit video.generate video.av video.avatar video.lipsync video.enhance audio.tts audio.music audio.sfx audio.enhance text.asr compose.assemble analyze.qc train.lora`.
`QualityTier`: `draft | preview | final`.

## Job lifecycle

```
queued --claim(lease)--> running --> completed
   ^                        |-----> workflow_error | transport_error | timeout | failed_validation | cancelled
   '-- lease expired / retry'
```

* `enqueue` is idempotent: same project+shot+kind+slot+model+resolved params+inputs while a job is active returns the existing job.
* `claim` is one atomic `UPDATE`; the worker heartbeats every `heartbeat_seconds`; a lease not renewed within `lease_seconds` is reclaimed (`requeue_expired`). The `external_id` is kept so the next worker can `reattach`; otherwise the job is re-submitted. A job out of attempts fails with `worker_lost`.
* **`completed` means: the artifact file exists, is non-empty, passes ffprobe for known media types, and is registered with sha256.** Anything else is a failure with an `ErrorCode`.
* Cancel: `request_cancel` flag; the worker calls `adapter.cancel`, ends `cancelled`. Graceful worker shutdown releases leases (`release`).
* Candidates: each regenerate creates a job; with `auto_adopt = true` the first asset of a slot becomes current, later ones stay candidates until `adopt`.

## Adapter protocol (`media/contracts.py`)

```python
class Adapter(Protocol):
    provider: str
    def capabilities(self) -> Capabilities      # kinds, heavy, supports_cancel/reattach, param_schema, poll_interval_sec
    def health(self) -> HealthReport             # ok | missing_model | missing_tool | not_running | degraded | ...
    def validate(self, job, ctx) -> list[ValidationIssue]
    def submit(self, job, ctx) -> str            # external id
    def poll(self, external_id, job, ctx) -> Progress   # running | succeeded | failed(+AdapterError)
    def fetch(self, external_id, job, ctx) -> list[ArtifactSpec]
    def cancel(self, external_id, job, ctx) -> None
    def reattach(self, external_id, job, ctx) -> bool
```

Rules: raise `AdapterError(ErrorCode.X, ..., retryable=...)`; never invent parameter defaults you cannot justify (leave to config); write only inside `ctx.work_dir` (the worker moves artifacts into `projects/<slug>/media/<kind>/<shot>/`).
Register in `media/registry.py`. The `fake` adapter exists only when `STORY_AGENT_ENABLE_FAKE=1` and defines the contract tests.

## Parameter resolution (`media/params.py`)

adapter built-ins < `[models.X.params]` < `[models.X.tiers.<tier>]` < `projects.settings.models.X` < per-job/spec `params`. The final dict is stored in `media_jobs.params`. Unknown keys warn (block when `[runtime] strict_params = true`).

## Broker (`media/broker.py`)

One heavy job at a time; at most `max_light_jobs` light jobs; `memory_budget_gib` (a lone oversized job may still run); `llm_unload_policy`: `never` (default; job waits with reason `llm_resident`), `ask` (waits, operator runs `ops llm-unload`), `auto` (worker unloads resident LLMs). Waiting is visible: `progress_msg` carries a `WaitReason` (`heavy_busy | light_limit | memory_budget | llm_resident`). No head-of-line blocking.

## Compliance (`media/compliance.py`)

On by default, switchable globally / per env / per project (see CONFIGURATION). When on: restricted-licence models need a project ack (`license_ack_required`), voice cloning needs recorded consent (`consent_required`). Provenance (model, version, params, seed, licence) is **always** recorded, independent of the switch. `redact_for_export` strips internal paths.

## Error codes -> i18n

Every `ErrorCode` has `error.<code>` and `error.<code>.remediation` in `locales/*.json`; statuses `status.<job status>`, kinds `media.kind.<kind>`, tiers `media.tier.<tier>`, subjects `spec.subject.<type>`, wait reasons `wait.<reason>`, health `health.<state>`. `tests/test_i18n.py` fails if any enum value lacks a label.
