# software-system-review — v2 release candidate (2026-10-21)

```
Verdict: partial
Validation: pass-with-warnings
Git baseline: branch claude/init-127186 (clean tree), v1 preserved as tag v1-final (ca223f5)
Release readiness: ready for local single-user use of the image + voice + music + compose path;
                   NOT ready to claim for h3 / longcat / Phosphene video (never run live) or for network exposure
```

## Validation (all run on this commit)

| Check | Result |
|---|---|
| Import / start | `import storyagent.api.app` ok; `scripts/start.sh` syntax ok; live server + worker ran the whole session |
| Migrations | fresh home: 0 → 4, second run idempotent; snapshot-before-upgrade covered by tests |
| Backend tests | 378 passed (2 live deselected) |
| Live e2e (`pytest -m live`) | 2 passed in 18m39s: real Ollama story (1 act/1 scene/2 shots) → FLUX portraits/keyframes + ACE-Step music → ffmpeg episode, 720×1280, audio present, QC passed |
| Lint / types | `ruff check storyagent tests` clean, `mypy` clean (87 files) |
| Web | typecheck, eslint (`jsx-no-literals`), 13 vitest, production build: all pass |
| i18n | `storyagent i18n check` consistent (zh-CN / en); locales ship inside the wheel |
| Packaging | wheel builds and contains migrations, defaults, agent specs, locales, runners |
| Manual UI | progress bars/ETA, creative panel, episode player + QC panel, recycle bin, rename checked in the browser |

## Findings

**P1 — not verified live: h3, longcat, Phosphene video, FastVideo.** Evidence: Phosphene service down; h3/longcat only exercised with stub tools + contract tests; FastVideo adapter not written (weights absent, API unverified). Impact: video generation is unproven end-to-end (stills with Ken Burns are the verified fallback). Fix: run each once when the service/weights are available; the produce stage already reports `no_model` instead of failing silently.

**P2 — no browser e2e (Playwright).** Evidence: coverage is API tests + vitest + the live pipeline test + manual browser checks. Impact: UI regressions in multi-step flows are caught only manually. Fix: add Playwright against `storyagent serve` with the fake LLM/adapters.

**P2 — API has no authentication.** Evidence: docs/API.md. Mitigation: binds loopback by default, uploads size-limited, asset paths confined to the data home. Fix before any LAN exposure.

**P2 — gaps vs v1 (see PARITY_MATRIX "已知缺口").** SFX synthesis, RIFE, EDL/FCPXML, crossfades, reference-image upload in the web UI, AI rewrite + change manifest. Each is listed with a reason; none is silent.

**P3 — `recipes.toml` is declared, validated and listed in `/api/meta` but not executed by the workflow.** Documented in CONFIGURATION.md; either wire it or drop it.

**P3 — no audit table for destructive actions** (project trash, library removal). They are reversible and logged; job history is complete. Documented in RUNBOOK.

**P3 — web client is not part of the wheel** (`web/dist` is built from the checkout). Fine for the intended editable install.

## Fixed during this review

* Wheel lacked the message catalogs → moved `locales/` into the package.
* Dashboard count vs drill-down: queue summary was global while the project tab listed one project, with no way to open a count → `/api/queue?project_id=`, status filter chips whose counts equal their lists, with a reconciliation test.
* `ruff` had been silently excluding `storyagent/agents` (v1 exclude patterns) → patterns removed, one long line fixed.
* Visible AIGC label can no longer be silently dropped: a ffmpeg without libass refuses the job.
* QC `episode_length` is advisory so short drafts are not "failed".

## Passed checks

Git-managed with a tagged rollback point; MECE workflow (story → media → episode) with one-click and per-stage paths; resumable runs and leases (kill -9 recovery tested); idempotent job submission; specs versioned/lockable/regenerable; live progress with ETA never regressing; compliance on by default and switchable per scope, provenance always recorded; export package with redacted provenance; hard-coded paths/text guarded by tests.

## Suggested next tasks

1. Bring Phosphene/h3/longcat up once and run `workflow run --stage produce` on a project; fix what real output reveals.
2. Playwright smoke for the create → run → edit → compose path.
3. Decide on `recipes.toml` (wire or delete) and the SFX model question.
