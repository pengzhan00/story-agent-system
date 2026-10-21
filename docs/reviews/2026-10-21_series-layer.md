# software-system-review — series layer baseline (2026-10-21)

Scope: whole product at `main` after the series work (seasons, per-episode state, batch runs, season outline, series board,
character bible, shot tiering, edit cards, confirmable production plan). Previous review: `2026-10-21_v2-release-candidate.md`.

```
Verdict: partial
Validation: pass-with-warnings   (live end-to-end passed once; series features beyond a single episode not run live)
Git baseline: branch claude/init-127186 at the commit that carries this file (clean tree, pushed); v1 preserved as tag v1-final
Release readiness: ready for local single-user use of the series pipeline up to keyframes / voices / music / compose;
                   NOT ready to claim for: real-model runs of the new keyframe-from-references + tiering path over a
                   whole episode, h3 / longcat / LoRA training (never run live), a real cloud video vendor, LAN exposure
```

## Validation (run on this commit)

| Check | Result |
|---|---|
| Import / start | `import storyagent.api.app` ok; production restarted on the deployed commit, `/api/ready` returns schema 11 |
| Migrations | fresh home 0 → 11; copy of the production DB (snapshot v6) → 11, idempotent, `integrity_check` ok, `foreign_key_check` clean. The production DB holds no project rows, so data-carrying upgrades (007 stage rows, 008 music refs) are covered by unit tests with seeded rows, not by real data |
| Backend tests | 480 passed, 2 live deselected |
| Lint / types | `ruff check storyagent tests` clean, `mypy` clean (99 files) |
| Web | typecheck, eslint (`react/jsx-no-literals`), 13 vitest, production build: all pass |
| i18n | `storyagent i18n check` consistent (zh-CN / en) |
| Live e2e | `test_one_sentence_to_a_playable_episode` with real Ollama + FLUX (mflux) + ACE-Step + ffmpeg: **passed, 1342 s**. Job timings: image.edit keyframe 656 s (x2), image.generate 88 s (x4), music 51 s (x2), assemble 2 s |
| Manual UI | Series tab, Season card, default-voice picker, batch inputs, production plan viewed in the browser on a dev server (sandbox data) |

## Findings

### Fixed during this review

* **P1 — editing a spec made its shot disappear from the film.** Evidence: reproduced; after `spec.edit` on a shot's
  keyframe/video, `build_episode_timeline` returned a `black` segment and `no_picture`. Cause: every spec version is its own
  row with its own id, assets were looked up by spec id, so the still-current asset of the old version was ignored.
  Impact: the core "edit the prompt, regenerate" loop blanked shots in any film assembled in between; the plan never showed
  `stale` (always `new`), and the flow counts dropped the made count. Fix: lookup by the spec's subject and slot
  (`current_asset` / `slot_asset`), old asset stays in use and counts as outdated. Regression test added.
* **P1 — season run and season outline disagreed on episode numbers.** Evidence: reproduced; season 2 with season 1 at 3 of
  10 episodes: the run would cover episodes 4-5, the outline created 11-12. Impact: scripts for the wrong episodes, duplicate
  numbering. Fix: one rule (`WorkflowState.season_first_episode`) for both; regression test added.

* **P1 — the production plan understated keyframes composed from references.** Evidence: live run, `image.edit` keyframes
  took ~656 s each, `image.generate` ~88 s; the plan listed those keyframes as `image.generate` with its estimate. Impact:
  at 800 shots the real cost is ~146 h of keyframes, shown as ~20 h, so the "confirm before spending machine time" step
  misled. Fix: the plan now reports kind `image.edit`, the edit model and its (history or 600 s fallback) estimate; test
  added. The cost itself is a decision for the owner: `[workflow] keyframe_from_references` could be limited to the
  tiers/shots that need identity consistency.

### Open

* **P1 — only partly verified live: the keyframe-from-references path ran once (1 episode, 2 shots with refs); shot tiering
  over a whole episode and season/batch runs on real models were not.** Evidence: the rest is covered with fake adapters and
  a fake LLM. Impact: quality of the composed keyframes
  and real timings are unknown. Fix: the 30 s pilot episode (`docs/SERIES.md` phase 0).
* **P1 (carried) — h3, longcat, LoRA training never run live.**
* **P2 — API has no authentication** (loopback by default, uploads bounded, asset paths confined). Fix before LAN use.
* **P2 — no scheduled backup and no autostart.** `data/` is outside Git; backups are the operator's (`sqlite3 .backup`);
  migrations take a `.bak-vN` snapshot, which is the only automatic copy.
* **P2 — destructive edits have no audit trail**: deleting a character / music / shot row is a hard delete (specs and
  assets keep their own history; projects and assets use a recycle bin). Fix: soft delete or an audit table.
* **P2 — web has 13 unit tests and no browser e2e** (Playwright is excluded by the owner's choice); new series UI was checked
  by hand only.
* **P3 — project list card shows step progress of episode 1 only** (`stages_done/total`); for a many-episode project the
  series board is the accurate view.
* **P3 — cross-episode lock for locations and visual style** (bible, scenes/style half) is not built.
* **P3 — crossfades, SFX synthesis, several BGMs per episode, avatar/lip-sync step in the project flow** are not built.
* **P3 — cloud vendor adapter exercised only against a fake vendor.**

## Passed checks

Git-managed, pushed, rollback point `v1-final`, per-migration snapshot; MECE flow series > season > episode with per-scope
stage state, batch/resume per episode, outline-first for planned seasons; confirmation queue before spending machine time
(production plan, per-shot tiering, clip budget); dashboard counts reconcile with their lists (series board test compares
against shots and the asset ledger); idempotent submission and resumable runs; asset file serving confined to the data home;
no secrets or databases tracked; hard-coded text guarded by tests (backend) and lint (web); compliance default-on with
provenance always recorded.

## Residual risks

Real-model behaviour of the new keyframe chain; no auth if the port is exposed; one machine holds all data without automatic
backup; the series layer has only been driven through fake LLM/adapters and a handful of hand checks in the browser.

## Suggested next tasks

1. Pilot episode (30 s) on real models; record timings and look at the composed keyframes; tune `video_share`.
2. Backup job (launchd or cron calling `sqlite3 .backup` + copying `data/projects`) and an autostart plist.
3. Soft delete / audit for story rows; project card progress per series.
4. Playwright smoke when the owner allows it.
