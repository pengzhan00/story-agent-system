# HTTP API

`storyagent serve` (default `http://<[api] host>:<[api] port>`, loopback only by default; for a team turn on sign-in, see "Team access" below — never expose it on a network with `[auth] mode = "off"`).
Interactive docs: `/api/docs`. Machine contract: `/api/openapi.json` (the web client is generated from it).
The API never generates anything itself: it reads/writes the ledger, and `storyagent worker` does the work. Either process can restart without the other.

## Conventions

* JSON in and out; ids: projects are integers, jobs/assets/specs are 32-char hex strings.
* **Errors** are always `{"error": {"code", "message", "remediation", "detail"}}`. `message`/`remediation` are localised, `detail` is for developers. Codes: `not_found` (404), `conflict` (409), `spec_locked` (409), `shot_locked` (409), `nothing_to_do` (409), `invalid` (422), `asset_file_missing` (404), `too_large` (413), `episode_not_ready` (409), `llm_failed` (502), `internal` (500).
* **Language**: `?lang=zh-CN` wins, then `Accept-Language`, then the configured locale. `GET /api/i18n/{locale}` returns the flat catalog the web client uses (same keys as the backend).
* Job `error_code` values are `ErrorCode` names; translate them with `error.<code>` and `error.<code>.remediation` from the catalog.

## Routes

| Area | Route | Notes |
|---|---|---|
| System | `GET /api/ready` | liveness + schema version |
| | `GET /api/health` | same report as `storyagent media healthcheck`, with localised state labels |
| | `GET /api/launch` | launch screen: every service with its state (`running`/`starting`/`failed`/`stopped`/`disabled`/`probe_only`), whether it can be started here, `issues` (failed health checks and language models a role needs that are not installed), `ready` (all `[healthcheck] required` services up and no required issue) |
| | `POST /api/launch/start-all`, `POST /api/launch/{name}/start` | start the configured `[launch.<name>]` command (detached, output in `logs/launch_<name>.log`); a service that already answers is left alone; 404 `launch_unknown`, 409 `launch_not_startable`, 424 `launch_missing`. Nothing is ever stopped |
| | `GET /api/meta` | enums with labels, models (params/tiers/licence), default models, recipes, queue state |
| | `GET /api/i18n/{locale}` | message catalog |
| | `GET /api/events?project_id=&after=&timeout=` | **SSE**. `event: job`, `data` = JobOut, `id` = event cursor; `event: workflow`, `data` = WorkflowRunOut (live `progress`, sent whenever the percentage/stage/message changes, plus the final frame). On connect: snapshot of active jobs and runs. Reconnect with `?after=<last id>` to catch up. `: keepalive` comments |
| Projects | `GET/POST /api/projects` (`?trashed=true` lists the recycle bin), `GET/PATCH /api/projects/{id}`, `PATCH /api/projects/{id}/settings` | settings = per-project `compliance` and `models.<ref>` overrides (null removes a group). `POST` takes `kind`, `pipeline`, `brief{seasons, episodes_per_season, episode_sec, audience, platform, logline}`: the plan and frame are decided at creation; the project carries `kind`, `pipeline`, `brief` |
| | `POST /api/projects/estimate {kind, pipeline, brief}`, `PATCH /api/projects/{id}/brief`, `PATCH /api/projects/{id} {pipeline|kind}` | size estimate before creating (tasks, models, machine time, `over_budget`); change the brief (seasons/episodes only grow); change the plan (whole model set; 409 `conflict` when it would turn the frame around after pictures exist). `GET /api/meta` lists `work_kinds` and `pipelines`; `GET /api/health?pipeline=|project=` judges the report by what the plan needs; `GET /api/workshop?pipeline=` starts the workshop from a plan and marks other models `in_plan: false` |
| | `DELETE /api/projects/{id}` (soft delete into the recycle bin; 409 while a run is active), `POST /api/projects/{id}/restore` | nothing is removed from disk; a trashed project cannot start runs |
| Jobs | `GET/POST /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events`, `POST /api/jobs/{id}/cancel`, `POST /api/jobs/{id}/retry`, `GET /api/queue` | POST returns 201, or 200 with `created:false` when an identical request is already active |
| Assets | `GET /api/assets`, `GET /api/assets/{id}`, `GET /api/assets/{id}/file`, `POST .../adopt`, `POST .../trash` | file streaming supports range requests; paths are confined to the data home |
| Scripts | `GET /api/scripts?q=&project_id=` (every project's scripts: counts, title, synopsis), `GET /api/scripts/{project_id}/{episode_no}` (adds `text`, the script as Markdown in the caller's language), `POST /api/scripts/{project_id}/{episode_no}/publish` `{name?, description?, tags?, replace?}` (file it as `library:script/<name>`, a snapshot) | a locked restricted project is listed without title and synopsis, not searched, and 403 `content_locked` for text and publish; a restricted script is filed into the vault; a taken name is 409 unless `replace` |
| Wiki | `GET /api/wiki/pages?q=&source=&tag=`, `GET /api/wiki/pages/{slug}`, `GET /api/wiki/graph?q=&source=&tag=&focus=&depth=&include_tags=&shared_tags=&include_models=&limit=`, `POST /api/wiki/sync`, `POST /api/wiki/ask` (question → answer with numbered sources, any signed-in user), `GET /api/wiki/assets/{name}` | `graph` returns `nodes` (pages, optional tag and model nodes) and `edges` (`link`, `tag`, `shared_tag`, `mention`); restricted notes that are locked are never nodes, edge ends or counts (403 for `focus` on one); see [WIKI.md](WIKI.md) |
| Uploads & library | `POST /api/projects/{pid}/uploads?filename=&slot=&shot_id=` (raw body) | returns an `upload.*` asset; use it as `asset:<id>` in job inputs |
| | `GET/POST /api/library`, `POST /api/library/upload?filename=&role=&name=[&consent=&text=]` (raw body), `GET/PATCH/DELETE /api/library/{id}`, `GET /api/library/{id}/file` | publish an asset (copy) under `role/name`; jobs use `library:<role>/<name>`; DELETE is a soft delete |
| Story | `GET/POST /api/projects/{pid}/characters`, `GET/PATCH/DELETE .../characters/{id}`; the same for `locations`, `music` (themes + lyrics), `sfx` (no PATCH) | partial bodies; unknown fields are 422; rows are only reachable through their own project |
| | `GET .../episodes`, `PATCH .../episodes/{id}`, `GET .../scripts`, `GET .../scripts/{episode_no}`, `PATCH .../scripts/{ep}/acts/{act}/scenes/{scene}` | scene edits are saved into the script; re-run `shotplan` to rebuild shots |
| | `GET .../shots[?episode_id=]`, `GET/PATCH/DELETE .../shots/{id}`, `PUT .../shots/{id}/lock {locked}` | a locked shot rejects edits with `shot_locked`; re-planning keeps locked shots and the id (and specs/assets) of every other shot |
| | `GET .../dialogue`, `PUT .../shots/{id}/dialogue/{index}/speaker {character}` | `character_id` null = the speaker matches no character: reassign or create one |
| Workflow | `GET /api/projects/{pid}/workflow` | stage states, the active run with its progress, last runs |
| | `PATCH /api/projects/{pid}/settings` also takes `default_voice` (`library:voice/<name>`), `video_share` (0-1) and `video_budget_hours` (shot tiering), `GET /api/projects/{pid}/produce/plan?tier=` (also `gates`: what stops every job), `POST /api/projects/{pid}/voices/auto` (a library voice by gender for every character without one, put on its spoken lines; `unresolved` lists who could not get one), `POST /api/projects/from-text {name, asset_id}` | the production plan (every task the produce stage would make: spec, kind, model, phase 1-3, state new/stale/done/blocked, estimate; send `params.only=[spec ids]` to a run to make just those); a project made from a text asset (story/script) |
| | `GET /api/projects/{pid}/series` | the series board: seasons > episodes with stage states, shot / keyframe / clip counts, outdated count and the finished film; planned episodes that are not started are listed with `exists=false` |
| | `GET /api/projects/{pid}/workflow?episode=N`, `GET /api/projects/{pid}/seasons`, `PATCH /api/projects/{pid}/seasons/{n}` | stage states as seen from episode N (analyze/cast are shared by the whole series, the rest are per episode); seasons with their episode counts; edit a season (title, summary, planned_episodes, target_sec) |
| | `POST /api/projects/{pid}/workflow/runs` with `params.episodes` ("1-10", "1,3" or a list) or `params.season` | a run over several episodes: series stages once, each episode resumes on its own; progress `stages[]` has one bar per stage and episode (`episode` null for series stages) |
| | `POST /api/projects/{pid}/workflow/runs {stages?, params}`, `GET .../workflow/runs`, `GET /api/workflow/runs/{id}`, `POST /api/workflow/runs/{id}/cancel` | no `stages` = resume (everything not completed); 409 `conflict` while a run is active, 409 `nothing_to_do` when all done. See `docs/WORKFLOW.md` |
| | `POST /api/projects/{pid}/review?episode=1` | script editor critique `{score, summary, issues[]}`; a language-model call (can take a minute), changes nothing; 502 `llm_failed` when the model service is down |
| Episode | `GET /api/projects/{pid}/episodes/{n}/package` | zip of the delivery package (film, SRT, stems, manifest, provenance); 409 `episode_not_ready` before the first compose |
| Assets | `GET /api/assets?kinds=a,b&standalone=&newest_first=&offset=&limit=` , `GET /api/library/roles` | list every asset (workshop results have no project) by kind, newest first; the names an item can be filed under (`library:<role>/<name>`) |
| Workshop | `GET /api/workshop` | every module (story, script, image, lora, song, voice, video, transcript) with its kinds: current model + alternatives (with availability), tier, effective parameters, the adapter's parameter schema, the inputs the kind takes and what it produces |
| | `GET /api/workshop/params?kind=&model_ref=&tier=` | `{base, preset, effective, schema}` for a model at a tier, to show and edit before running |
| | `PUT/DELETE /api/workshop/presets/{kind}` | save / reset the module's starting point: `{model_ref, tier, params}` (params = changes on top of models.toml + tier) |
| | `POST /api/workshop/run {kind, inputs, model_ref?, tier?, params?, seed?, count?}` | one module on its own (no project); `count` queues several candidates; the result is in `GET /api/workshop/history` |
| | `POST /api/workshop/pipelines {steps:[{kind, inputs, ...}]}` | queue steps together; an input set to `"@prev"` takes the previous step's output (`job:<id>`) and waits for it; a failed step cancels the steps waiting for it (`dependency_failed`); 422 `pipeline_incompatible` / `pipeline_no_previous` before anything is queued |
| | text modules | `text.story` / `text.script` are written by a language model (in the worker, through the `writer` role; prompts `workshop.story` / `workshop.script`); the result is a markdown asset, `"@prev"` or `asset:<id>` feeds it to the next text step; `train.lora` takes `images` (list) + `trigger`; an image `lora` + `lora_scale` input becomes `params.loras` |
| | `GET /api/workshop/history?module=&kind=&limit=` , `POST /api/workshop/upload?filename=` | standalone jobs with their assets; raw-body upload of an input file without a project |
| Specs | `/api/projects/{pid}/specs` (list, create), `.../specs/{subject_type}/{subject_id}` (get current, `PATCH` = new version), `.../versions`, `.../diff?v1=&v2=`, `.../revert`, `PUT/DELETE .../lock`, `.../acknowledge`, `.../regenerate`; `GET .../specs/review`, `GET /api/projects/{pid}/stale` | `?slot=` selects a variant (default `main`). `PATCH` only changes the fields that are sent; explicit `null` clears (e.g. `seed`) |

## Typical creative loop

```
POST /api/projects                                   -> project
POST /api/projects/1/specs                           -> character.portrait/hero v1 (prompt, seed, refs)
POST /api/projects/1/specs/character.portrait/hero/regenerate {"n_candidates":3,"tier":"draft"}
GET  /api/events                                     -> progress per candidate (SSE)
GET  /api/assets?project_id=1                        -> candidates; GET /api/assets/{id}/file to preview
POST /api/assets/{id}/adopt                          -> pick the winner
PATCH /api/projects/1/specs/character.portrait/hero  {"prompt":"... at dusk"}   -> v2; old asset shows up in /stale
```

## Run progress (what a progress bar needs)

`WorkflowRunOut.progress`: `percent` (overall 0-100, never decreases), `stage`/`stage_index`/`stage_count`, `done`/`total` (items in the current stage, `total` 0 = not known yet), `message` (what is being done now), `elapsed_sec`, `eta_sec` (null until ~10 % is done) and `stages[]` (one bar each: `status`, `percent` — null while a running stage has not counted its items — and `seconds` once finished). It is written about once a second while the run is alive and once more at the end, so a poll or the SSE feed never shows a stalled number for a live run. A run whose worker died is requeued when its lease expires and continues from what was saved.

## Continuity (costumes and props)

`GET/POST /api/projects/{id}/looks`, `GET/PATCH/DELETE .../looks/{look}`: a look is `{character_id, label, costume, from_episode, from_act, from_scene}` - what the character wears from that point of the story until their next look (the costume designed with the character is created as look "default" from 1/1/1 by the cast stage). `GET/POST /api/projects/{id}/props`, `GET/PATCH/DELETE .../props/{prop}`: `{name (unique per project), description, introduced_episode}`; every shot whose text (action, expression, narration, beat, dialogue, sfx cues) mentions the name carries the prop. The shot plan resolves both into the shot payload (`subject.characters[].costume`, `continuity.props`) and the visual prompt uses exactly those words; re-run `shotplan` then `specs` after editing (changed specs make older takes outdated).

## Content ratings (restricted material)
Configuration in `content.toml`; behaviour in `docs/RESTRICTED_CONTENT.md`. A project has a `rating` (set at creation, `rating` in `POST /api/projects` / `--rating`; changeable only while it has no files). Jobs and assets are rated `max(project, model)`; assets carry `rating`.
- Storage: a rating with `vault = true` writes files under `$STORY_AGENT_HOME/vault/<slug>/media/...` (uploads, pack imports, the finished film imported from an editor, library copies - `vault/_library/`).
- Hard gates (checked by the worker before every job of a rating with `gates = true`; compliance switches cannot lift them): every character of the project has `adult_confirmed` and an age (if numeric) of at least `min_adult_age`; no `blocked_terms` in the job's prompts or the characters' texts; voice references and likeness training need recorded consent; no cloud model. A refusal is `failed_validation` with `error_code` `content_blocked` (the job message names the gate) or `vault_unavailable` (the volume in `volume_mount` is not mounted).
- Masking: while the caller has no unlock session, `AssetOut`/`JobOut`/`SpecOut`/`ProjectCard` of a `mask_ui` rating come back with `locked: true` (assets: `path`, `sha256`, `metadata`, `provenance` blanked; jobs: `inputs` blank; specs: `masked: true`, prompts blank; project cards: no premise or poster). `GET /api/assets/{id}/file` and `/api/library/{id}/file` answer 403 `content_locked`; `GET /api/assets/{id}/mask` is a small blurred JPEG (images/videos) that is always allowed.
- `GET /api/content/status` -> `{unlocked, expires_at, ttl_sec, needs_password, vault_ready, encryption, default_rating, ratings[]}`; `POST /api/content/unlock` `{confirm_adult: true, password}` (password only with accounts; wrong password = 403 `content_unlock_denied`); `DELETE /api/content/unlock`. Audit actions: `content.unlock`, `content.unlock_failed`, `content.lock`, `content.view`, `content.export`, `content.adult_confirmed`.
- Export: pack, delivery package, FCP package and platform delivery refuse a project that holds material of a rating without `allow_export` (403 `content_export_blocked`, CLI exit 1) unless `confirm_restricted` is given (query parameter; `confirm_restricted` in the delivery body; `--confirm-restricted` in the CLI); over the API that also needs an active unlock and is audited. Packages then carry `notice_key: content.export.personal_use` / `restricted_export` in their manifests.
- Logs: for a rating with `log_prompts = false` the model-call log (`generation_logs`, `/api/projects/{id}/ai-calls`) keeps `sha256:<digest> (<n> chars)` instead of prompt, system prompt and answer.
- CLI: `storyagent project create NAME --rating restricted`, `storyagent content status --project N`, `storyagent content adult --project N (--character NAME ... | --all)`.

## Delivery per platform
`compose/delivery.py`, presets in `platforms.toml`. One assembled episode is re-encoded for a destination: the frame and fps of the preset (a film of another aspect is `crop`ped, `pad`ded with black, or laid over a blurred copy of itself), loudness in two passes (measure, then a linear `loudnorm` correction to the preset's I / TP / LRA), H.264 at the preset's `crf` or, with `max_mb`, at the bitrate that fits that size. Also written: `cover.jpg` (frame at `cover_sec`), `trailer.mp4` (first `trailer_sec` seconds, faded out; none when the film is not longer), `subtitles.<lang>.srt` for the episode's own language and every requested translation (translator agent `subtitle.translate`, chunks of 40 lines, same timing, the count must match), and `delivery.json` (platform, source asset + sha256, measured result, warnings, sha256 of each file). The container's AIGC metadata is carried over. Nothing is cut to fit: a film over `max_duration_sec` or a file over `max_mb` is only reported.
- `GET /api/platforms`: the presets (`default: true` on the configured one).
- `POST /api/projects/{id}/episodes/{n}/delivery` body `{platform, languages[], confirm_restricted}` queues a `compose.deliver` job (adapter `delivery`, slot `<platform>_ep<NN>`) and answers 202 `{job_id, created}`; an identical active request returns the same job (`created: false`). Progress (`encode`, `cover`, `trailer`, `subtitles`), cancel (between steps) and retry are the ordinary job ones; a restart runs the delivery again. 404 for an unknown platform, 409 `episode_not_ready` until the episode is assembled. The result is a zip asset of kind `compose.deliver` (a new version per run; its metadata is `delivery.json`): `GET /api/projects/{id}/deliveries` lists them newest first (`{asset, platform, episode, warnings[], subtitles[], result}`) and `GET /api/assets/{asset.id}/file` downloads one (so the content lock and the audit apply). Warning codes (`delivery.warn.<code>`): `crop_label`, `crop_burned_subtitles`, `over_duration`, `over_size`, `size_unreachable`, `trailer_too_long`, `no_subtitles`, `no_audio`, `loudness_unmeasured`. A failed translation fails the job (`service_unreachable`, retryable).
- CLI: `storyagent delivery platforms`, `storyagent delivery make --project N [--episode 1] [--platform a,b] [--lang en,ja] --out DIR`.

## Final Cut Pro interchange (FCPXML out, edit compared, finished film in)
`compose/interchange.py`. Picture is the primary storyline (one clip per shot; a slowed shot is a retime `timeMap`), cards are gaps with a note, dialogue / effects / music are connected audio lanes (-1 / -2 / -3) with FCP roles `dialogue`, `effects`, `music` and the mix gains as `adjust-volume`; a music bed shorter than the film repeats. Media paths are relative (`./media/...`), `--absolute` writes `file://` URLs of the export folder. The output is checked against Apple's FCPXML 1.10 DTD in the tests (Final Cut Pro 12.3's own DTD, when present); opening it in Final Cut Pro is the operator's acceptance step (File > Import > XML).
- `GET /api/projects/{id}/episodes/{n}/fcp` is a zip: `episode.fcpxml`, `media/{video,image,speech,sfx,music}/…`, `episode.srt` (import it as captions), `interchange.json` (which file is which shot/asset). 409 `episode_not_ready` until the episode is assembled.
- `POST …/fcp/diff?filename=` (raw FCPXML exported back from FCP) returns `{original_sec, edited_sec, shots[{shot_id, status: kept|trimmed|moved|removed, duration, new_duration}], added[{name, media, duration}], changed}`; nothing is changed.
- `POST …/fcp/final?filename=&note=` (raw `.mp4/.mov/.m4v`) registers the finished film as a new current version of the episode (`compose.assemble`, slot `episode_NN`, provenance `origin: external_edit`, `derived_from` the previous version); QC and `…/package` then describe that file.
- CLI: `fcp export --project N --episode E --out DIR [--absolute]`, `fcp diff PACKAGE_DIR EDITED.fcpxml`, `fcp final FILE --project N --episode E [--note]`.
- Not covered: transitions/fades (not written; the cut is hard-cut in FCP), burned-in subtitles (use `episode.srt`), Ken Burns on stills (a still is a plain clip), colour. Reading an edited project back is a comparison, not a rebuild of our timeline.

## Shot grammar (storyboard check)
`grammar.toml` holds the framing scale (`sizes.order` widest to tightest, with aliases) and the rules, each with a severity and its own numbers; set `enabled = false` on a rule to switch it off. Rules: `establishing` (first shot of a new place is wide-ish), `jump_size` (neighbouring shots, same people and place, framed almost alike), `size_leap`, `coverage` (a scene of several shots with one framing), `dialogue_close` (talk without a tight shot), `speaker_in_frame` (error), `axis_cross` (opposite `staging.screen_direction`), `single_action` (connector words in the action), `monotone_pace`. A shot whose size text is not on the scale is counted as `unclassified`, not judged.
- `GET /api/projects/{id}/grammar?episode=` returns `{shots, unclassified, counts{error,warning,info}, violations[{rule, severity, shot_id, episode, act, scene, shot, params, fixable, fix}]}`; `POST .../grammar/fix` `{episode?, rules?, shot_ids?}` repairs the fixable ones (size, add the speaker to the frame, align the screen direction), one pass, locked shots left alone, and returns `{fixed, left, report}`. A repair does not rewrite the shot's prompts: redo its specs when the picture changes.
- The `shotplan` stage summary carries `grammar` (the counts) so the run shows it. Shot payload `staging` = `{start_pose, end_pose, gait, camera_position, lens_mm, screen_direction}` (optional; the script stage may write them, you may edit them); the fallback image/video prompts use them.
- CLI: `grammar check --project N [--episode E]` (exit 1 when there are errors), `grammar fix --project N [--episode E] [--rule R]`.
- i18n: `grammar.msg.<rule>` (with the rule's params), `grammar.fix.<type>`, `grammar.reason.<reason>`, `web.grammar.*`.

## Asset packs (export, import, loose folders)
A pack is a directory or `.zip` with `pack.json` (schema `storyagent.pack.v1`; every file listed with its sha256), `story/<table>.json`, `specs/specs.json` and `assets/…/assets.json` + the files. Rows are matched by stable keys (characters/scenes/music by name, episodes by number, shots by episode+act+scene+shot), never by database id; ids in specs, slots and `asset:` references are remapped on import. Per-asset provenance is redacted (no prompts, no paths).
- `POST /api/projects/{id}/pack?scope=all|assets|story&include_history=` → writes `<home>/exports/<slug>-<time>.zip`, returns `{name, bytes, counts, warnings}`; `GET /api/packs/{name}` downloads it.
- `POST /api/projects/import-pack?filename=&into=&on_conflict=skip|new_version&only=all|assets|story&dry_run=` (raw zip body) → report `{dry_run, project_id, created_project, story, specs, assets, warnings, unassigned}`. Without `into` a new project is made from the manifest (needs the create-project right). `dry_run` runs the whole import and rolls it back; nothing is written.
- Same sha256 in the same slot is never stored twice (`duplicate`). A slot that already has a different current take: `skip` stores the incoming file as a candidate, `new_version` makes it current (and updates story rows / adds spec versions).
- Zips are unpacked member by member: absolute paths, `..` and archives that unpack past `[runtime] pack_max_gib` are refused. A file that does not match its checksum is reported `corrupt` and not imported.
- CLI: `pack export --project N --out DIR|file.zip [--scope] [--include-history]`, `pack import SRC [--into N] [--only] [--on-conflict] [--dry-run]`, `pack import-folder DIR --project N` (no manifest: file names are mapped by `[runtime] loose_rules`, e.g. `shot_003.mp4` → the video of shot 3, `<character>.png` → that character's portrait, `bgm*.wav` → the first music theme; what no rule fits is listed under `unassigned`).
- Errors use the usual envelope (`invalid`, 422; 404 for a missing pack) with the reason in `detail` (`pack.error.<code>`); warnings are `pack.warn.<code>`.

## Asset packs (export, import, loose folders)
A pack is a directory or `.zip` with `pack.json` (schema `storyagent.pack.v1`; every file listed with its sha256), `story/<table>.json`, `specs/specs.json` and `assets/assets.json` plus the media files. Rows are matched by stable keys (characters/scenes/music by name, episodes by number, shots by episode+act+scene+shot), never by database id; ids in specs, slots and `asset:` references are remapped on import. Per-asset provenance is redacted (no prompts, no paths).
- `POST /api/projects/{id}/pack?scope=all|assets|story&include_history=` writes `<home>/exports/<slug>-<time>.zip` and returns `{name, bytes, counts, warnings}`; `GET /api/packs/{name}` downloads it.
- `POST /api/projects/import-pack?filename=&into=&on_conflict=skip|new_version&only=all|assets|story&dry_run=` (raw zip body) returns `{dry_run, project_id, created_project, story, specs, assets, warnings, unassigned}`. Without `into` a new project is made from the manifest (needs the create-project right). `dry_run` runs the whole import and rolls it back, nothing is written.
- The same sha256 in the same slot is never stored twice (`duplicate`). A slot that already has a different current take: `skip` stores the incoming file as a candidate, `new_version` makes it current (and updates story rows / adds spec versions).
- Zips are unpacked member by member: absolute paths, `..` and archives that unpack past `[runtime] pack_max_gib` are refused. A file that does not match its checksum is reported `corrupt` and not imported.
- CLI: `pack export --project N --out DIR|file.zip [--scope] [--include-history]`, `pack import SRC [--into N] [--only] [--on-conflict] [--dry-run]`, `pack import-folder DIR --project N` (no manifest: file names are mapped by `[runtime] loose_rules`, e.g. `shot_003.mp4` is the video of shot 3, `<character>.png` that character's portrait, `bgm*.wav` the first music theme; what no rule fits is listed under `unassigned`).
- Errors use the usual envelope (`invalid`, 422; 404 for a missing pack) with the reason in `detail` (`pack.error.<code>`); warnings are `pack.warn.<code>`.

## Review loop (notes, redo, compare)

A reviewer looks at a shot's takes, writes a note, redoes from it and adopts the better take. A note belongs to a spec (and may name the take it is about); statuses `open` -> `redoing` (a redo was queued) -> `resolved` | `dismissed`; reopening goes back to `open`. Authors come from the signed-in user (`local` with sign-in off); the write endpoints are audited like every write.

| Route | Notes |
|---|---|
| `GET /api/projects/{id}/review/board` | every shot with the takes in use (`assets`) and its `open_notes` / `redoing_notes` / `resolved_notes` counts |
| `GET /api/projects/{id}/notes?status=&shot_id=&subject_type=&subject_id=` | newest first |
| `POST /api/projects/{id}/notes` | `{subject_type, subject_id, slot?, shot_id?, asset_id?, body}`; the spec must exist (404), an empty body is 422 |
| `PATCH /api/projects/{id}/notes/{note}` | `{status: open|resolved|dismissed, resolution?}` |
| `POST /api/projects/{id}/notes/{note}/redo` | `{prompt?, negative_prompt?, text_payload?, model_ref?, seed?, params_patch?, n_candidates (1-8), tier}`: the changes become a new spec version (author `user`, note quoting the review; a locked spec is `409 spec_locked`), then `n_candidates` takes are queued; nothing sent = same spec, new seeds. A resolved/dismissed note must be reopened first (409) |
| `GET /api/projects/{id}/notes/{note}/compare` | `{note, before (the take the note names), after (takes made by its redo jobs), jobs}` |

Adopting a take that a redo made (`POST /api/assets/{id}/adopt`) resolves the notes waiting on it (`resolution = "adopted:<asset>"`); adopting any other take leaves notes alone.

## Team access (accounts, roles, audit)

`[auth] mode` in `auth.toml` (env `STORY_AGENT_AUTH`): `off` (default, one operator, no login; writes are still audited as `local`) or `token`. In token mode every `/api/*` call except the public ones (`/api/auth/login|register|setup|mode|me`, `/api/i18n/*`, `/api/docs`, `/api/openapi.json`) needs a session: `Authorization: Bearer <token>` or the HttpOnly session cookie set by login (the web client uses the cookie; the live feed `/api/events` needs it too).

| Route | Who | Notes |
|---|---|---|
| `GET /api/auth/mode` | public | `{mode, first_user, locales, allow_registration}` (`first_user`: token mode and no account yet) |
| `POST /api/auth/register` | public | `{username, password, birth_date (YYYY-MM-DD), display_name?}` -> 201 `{token, expires_at, user}` + cookie. A `registration_role` account (default `viewer`) whose birth date is only a **claim** (`age_status = pending`) until an admin approves it; `409 user_exists`, `422 bad_birth_date|weak_password`, `403 registration_closed` (switched off, or `max_pending_registrations` requests already wait) |
| `POST /api/auth/setup` | public | `{username, password, birth_date, display_name?}`: only while no account exists; creates the first administrator (age approved at once) and signs in; later `409 setup_done` |
| `POST /api/auth/age` | any signed-in user | `{birth_date}`: give (again) the birth date after a rejection; an approved age is changed by an admin only |
| `POST /api/auth/login` | public | `{username, password, label?}` -> `{token, expires_at, user}` + cookie; wrong password `401 login_failed`; too many failures `429 account_locked` |
| `GET /api/auth/me` | public | who am I: `{mode, user\|null, can_write, can_create_project, is_admin}` |
| `POST /api/auth/logout`, `POST /api/auth/password` | any signed-in user | end the session / change own password (ends all sessions) |
| `GET/POST /api/users`, `PATCH /api/users/{id}` | admin | list/create; change role, `active`, name, reset password (ends that user's sessions); the last active admin cannot be demoted or deactivated (`409 last_admin`) |
| `POST /api/users/{id}/age` | admin | `{approve, birth_date?, note?}`: approve the claimed birth date (or the corrected one) or reject it. The age is **worked out from the approved birth date every time**, so it grows by itself. Audit `user.age_approved` / `user.age_rejected` |
| `GET /api/audit?user=&action=&project_id=&since=&limit=` | admin | newest first |

Roles are flags in `[auth.roles.<name>]`: `write` (any POST/PATCH/PUT/DELETE), `create_project` (create/from-text/restore/delete a project), `admin` (users, audit, launch, config). Reading is always allowed for a signed-in user. `401 unauthorized` = sign in; `403 forbidden` = role too low. Audit rows: `http.<METHOD>` for every write (user, path, project, status, client address; **never bodies or passwords**), plus `auth.login`, `auth.login_failed`, `auth.register`, `auth.register_failed`, `auth.age_claimed`, `user.age_approved`, `user.age_rejected`, `content.age_denied`, `content.view` (any rating with an age limit or masking, `detail.rating`), `auth.denied`, `auth.logout`, `auth.password_changed`, `user.create`, `user.update`.

## Language models and the AI prompt buttons
- `GET /api/llm[?project_id=]` – backends (`state` ok | unreachable | disabled, installed `models`) and every role with the backend/model it uses now (`source`: config | global | project) and its `config_*` values. `PUT /api/llm/roles/{role}` `{backend, model, project_id?}` chooses a model (422 `llm_model_not_installed` when the backend lists models and this is not one; a backend that is down accepts the name); `DELETE /api/llm/roles/{role}[?project_id=]` goes back to the next level. `POST /api/llm/test` `{backend, model}` asks one short question and returns whether and how fast it answers. Resolution of a call: model named by the call > project choice > global choice > `llm.toml`. Applies to the next call, also in a running worker.
- `POST /api/ai/prompt` `{action: suggest|translate, text, purpose, style?, instruction?, source?, target, project_id?}` → `{text, action, backend, model}`. The AI buttons beside every prompt (spec editor, review redo, workshop long texts, agent prompts): `suggest` rewrites the prompt following the author's instruction, `translate` puts it in another language keeping weights, placeholders, trigger words and numbers. Nothing is saved; the screen shows the result and the author takes it or not. The model is the `prompter` role's (of the project when given); calls of a restricted project keep only digests in the model-call log. Errors: 502 `llm_unreachable` / `llm_model_missing` / `llm_failed`.

## Project pipeline (models in use)
| Route | Notes |
|---|---|
| `GET /api/projects/{id}/pipeline` | per task kind: the plan's recommended model, the model in use, whether it was changed by hand (`overridden`) or paired automatically (`auto`), the candidates (`paired` = same family as the picture / video model in use, `rating`, `remote`, `commercial_ok`) and `notes` (`auto_paired`, `dropped`, `family_mismatch`, `rated_model`). `409 no_pipeline` for a project without a plan |
| `PUT /api/projects/{id}/pipeline` | `{models: {kind: ref}}` replaces the hand-picked models (empty = the plan's defaults); wrong kind/model `422` |
| `DELETE /api/projects/{id}/pipeline` | back to the plan's recommended models |
`GET /api/meta` plans carry `rating` (the rating a project of that plan starts with).

## Launch screen: stop and memory
| Route | Notes |
|---|---|
| `GET /api/launch` | adds `memory` (`total_gib`, `free_gib`, `swap_total_gib`, `swap_used_gib`, `swap_percent`; 80+ = free memory before a heavy job) and `stoppable` per service |
| `POST /api/launch/{name}/stop` | `{force?}`: only for a service whose `[launch.<name>]` has `stop = "port"`. Ends the process of the same user that listens on the service's port (SIGTERM, SIGKILL with `force`); `409 service_busy` while a job of that service is queued/running (unless `force`), `409 launch_not_stoppable|launch_not_running|launch_stop_timeout`, `403 launch_foreign`. Audited as `service.stop`. CLI: `storyagent ops stop NAME [--force]` |

## Setup guide (new machine)

Admin only (`/api/setup` is an admin prefix). Reading changes nothing; nothing is downloaded, installed or started from the API - steps are shown, to be run with `storyagent setup run`.

- `GET /api/setup?profile=full&root=&extra=` - the machine checks (arch, system, macOS, memory, disk), every capability and component with its state (`ok | partial | missing | unverified`), need (`required | recommended | optional`), size, licence, the checks behind the state, the install steps (`command | download | manual`, with the resolved one-line command, and for a download its source, file and size), the downloads still to confirm, and `ready`.
- `GET /api/setup/config?profile=&root=&llm_model=` - the configuration changes this machine needs, per file (`changes`, the whole file as `text`). Writes nothing.
- `POST /api/setup/config {profile, root, extra, llm_model, write}` - with `write: true` the changed files are written to the config folder (the file replaced is kept as `<name>.bak-setup-<time>`), after the result has been loaded in a scratch copy; a configuration the loader refuses is `409 setup_config_invalid` and nothing is written. Audited as `setup.config`.
- `POST /api/setup/verify?profile=&root=` - the healthcheck with everything the profile needs counted as required.
