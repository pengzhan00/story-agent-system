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
| Uploads & library | `POST /api/projects/{pid}/uploads?filename=&slot=&shot_id=` (raw body) | returns an `upload.*` asset; use it as `asset:<id>` in job inputs |
| | `GET/POST /api/library`, `POST /api/library/upload?filename=&role=&name=[&consent=&text=]` (raw body), `GET/PATCH/DELETE /api/library/{id}`, `GET /api/library/{id}/file` | publish an asset (copy) under `role/name`; jobs use `library:<role>/<name>`; DELETE is a soft delete |
| Story | `GET/POST /api/projects/{pid}/characters`, `GET/PATCH/DELETE .../characters/{id}`; the same for `locations`, `music` (themes + lyrics), `sfx` (no PATCH) | partial bodies; unknown fields are 422; rows are only reachable through their own project |
| | `GET .../episodes`, `PATCH .../episodes/{id}`, `GET .../scripts`, `GET .../scripts/{episode_no}`, `PATCH .../scripts/{ep}/acts/{act}/scenes/{scene}` | scene edits are saved into the script; re-run `shotplan` to rebuild shots |
| | `GET .../shots[?episode_id=]`, `GET/PATCH/DELETE .../shots/{id}`, `PUT .../shots/{id}/lock {locked}` | a locked shot rejects edits with `shot_locked`; re-planning keeps locked shots and the id (and specs/assets) of every other shot |
| | `GET .../dialogue`, `PUT .../shots/{id}/dialogue/{index}/speaker {character}` | `character_id` null = the speaker matches no character: reassign or create one |
| Workflow | `GET /api/projects/{pid}/workflow` | stage states, the active run with its progress, last runs |
| | `PATCH /api/projects/{pid}/settings` also takes `default_voice` (`library:voice/<name>`), `video_share` (0-1) and `video_budget_hours` (shot tiering), `GET /api/projects/{pid}/produce/plan?tier=`, `POST /api/projects/from-text {name, asset_id}` | the production plan (every task the produce stage would make: spec, kind, model, phase 1-3, state new/stale/done/blocked, estimate; send `params.only=[spec ids]` to a run to make just those); a project made from a text asset (story/script) |
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

`[auth] mode` in `auth.toml` (env `STORY_AGENT_AUTH`): `off` (default, one operator, no login; writes are still audited as `local`) or `token`. In token mode every `/api/*` call except the public ones (`/api/auth/login|mode|me`, `/api/i18n/*`, `/api/docs`, `/api/openapi.json`) needs a session: `Authorization: Bearer <token>` or the HttpOnly session cookie set by login (the web client uses the cookie; the live feed `/api/events` needs it too).

| Route | Who | Notes |
|---|---|---|
| `GET /api/auth/mode` | public | `{mode, first_user, locales}` (`first_user`: token mode and no account yet) |
| `POST /api/auth/login` | public | `{username, password, label?}` -> `{token, expires_at, user}` + cookie; wrong password `401 login_failed`; too many failures `429 account_locked` |
| `GET /api/auth/me` | public | who am I: `{mode, user\|null, can_write, can_create_project, is_admin}` |
| `POST /api/auth/logout`, `POST /api/auth/password` | any signed-in user | end the session / change own password (ends all sessions) |
| `GET/POST /api/users`, `PATCH /api/users/{id}` | admin | list/create; change role, `active`, name, reset password (ends that user's sessions); the last active admin cannot be demoted or deactivated (`409 last_admin`) |
| `GET /api/audit?user=&action=&project_id=&since=&limit=` | admin | newest first |

Roles are flags in `[auth.roles.<name>]`: `write` (any POST/PATCH/PUT/DELETE), `create_project` (create/from-text/restore/delete a project), `admin` (users, audit, launch, config). Reading is always allowed for a signed-in user. `401 unauthorized` = sign in; `403 forbidden` = role too low. Audit rows: `http.<METHOD>` for every write (user, path, project, status, client address; **never bodies or passwords**), plus `auth.login`, `auth.login_failed`, `auth.denied`, `auth.logout`, `auth.password_changed`, `user.create`, `user.update`.
