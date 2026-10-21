# HTTP API

`storyagent serve` (default `http://<[api] host>:<[api] port>`, loopback only, no authentication yet — do not expose it on a network).
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
| Projects | `GET/POST /api/projects` (`?trashed=true` lists the recycle bin), `GET/PATCH /api/projects/{id}`, `PATCH /api/projects/{id}/settings` | settings = per-project `compliance` and `models.<ref>` overrides (null removes a group) |
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
| | `POST /api/projects/{pid}/workflow/runs {stages?, params}`, `GET .../workflow/runs`, `GET /api/workflow/runs/{id}`, `POST /api/workflow/runs/{id}/cancel` | no `stages` = resume (everything not completed); 409 `conflict` while a run is active, 409 `nothing_to_do` when all done. See `docs/WORKFLOW.md` |
| | `POST /api/projects/{pid}/review?episode=1` | script editor critique `{score, summary, issues[]}`; a language-model call (can take a minute), changes nothing; 502 `llm_failed` when the model service is down |
| Episode | `GET /api/projects/{pid}/episodes/{n}/package` | zip of the delivery package (film, SRT, stems, manifest, provenance); 409 `episode_not_ready` before the first compose |
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

## Not in the API

Authentication is out of scope for the single-user local deployment; add it before binding to anything but loopback.
