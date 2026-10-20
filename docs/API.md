# HTTP API (R1.5)

`storyagent serve` (default `http://<[api] host>:<[api] port>`, loopback only, no authentication yet — do not expose it on a network).
Interactive docs: `/api/docs`. Machine contract: `/api/openapi.json` (the web client is generated from it).
The API never generates anything itself: it reads/writes the ledger, and `storyagent worker` does the work. Either process can restart without the other.

## Conventions

* JSON in and out; ids: projects are integers, jobs/assets/specs are 32-char hex strings.
* **Errors** are always `{"error": {"code", "message", "remediation", "detail"}}`. `message`/`remediation` are localised, `detail` is for developers. Codes: `not_found` (404), `conflict` (409), `spec_locked` (409), `invalid` (422), `asset_file_missing` (404), `too_large` (413), `internal` (500).
* **Language**: `?lang=zh-CN` wins, then `Accept-Language`, then the configured locale. `GET /api/i18n/{locale}` returns the flat catalog the web client uses (same keys as the backend).
* Job `error_code` values are `ErrorCode` names; translate them with `error.<code>` and `error.<code>.remediation` from the catalog.

## Routes

| Area | Route | Notes |
|---|---|---|
| System | `GET /api/ready` | liveness + schema version |
| | `GET /api/health` | same report as `storyagent media healthcheck`, with localised state labels |
| | `GET /api/meta` | enums with labels, models (params/tiers/licence), default models, recipes, queue state |
| | `GET /api/i18n/{locale}` | message catalog |
| | `GET /api/events?project_id=&after=&timeout=` | **SSE**. `event: job`, `data` = JobOut, `id` = event cursor. On connect: snapshot of active jobs. Reconnect with `?after=<last id>` to catch up. `: keepalive` comments |
| Projects | `GET/POST /api/projects`, `GET/PATCH /api/projects/{id}`, `PATCH /api/projects/{id}/settings` | settings = per-project `compliance` and `models.<ref>` overrides (null removes a group) |
| Jobs | `GET/POST /api/jobs`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events`, `POST /api/jobs/{id}/cancel`, `POST /api/jobs/{id}/retry`, `GET /api/queue` | POST returns 201, or 200 with `created:false` when an identical request is already active |
| Assets | `GET /api/assets`, `GET /api/assets/{id}`, `GET /api/assets/{id}/file`, `POST .../adopt`, `POST .../trash` | file streaming supports range requests; paths are confined to the data home |
| Uploads & library | `POST /api/projects/{pid}/uploads?filename=&slot=&shot_id=` (raw body) | returns an `upload.*` asset; use it as `asset:<id>` in job inputs |
| | `GET/POST /api/library`, `POST /api/library/upload?filename=&role=&name=[&consent=&text=]` (raw body), `GET/PATCH/DELETE /api/library/{id}`, `GET /api/library/{id}/file` | publish an asset (copy) under `role/name`; jobs use `library:<role>/<name>`; DELETE is a soft delete |
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

## Not yet in the API

Shots, scripts, characters, scenes and the staged workflow arrive with R3 (agents + workflow); compose/export with R5. Authentication is out of scope for the single-user local deployment; add it before binding to anything but loopback.
