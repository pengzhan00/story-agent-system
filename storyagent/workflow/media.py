"""The two stages that spend the machine's time: ``produce`` (specs -> media) and ``compose`` (media -> episode).

Both hand work to the media ledger and *wait* for the worker, so they show real progress (jobs finished of jobs
needed) and are safe to re-run: ``produce`` skips every spec whose current asset was made from its current version,
and the ledger de-duplicates identical active jobs.

Phases (each waits for the one before, because a video starts from its keyframe):
    1  portraits, scene keyframes, music, theme song, sound effects, dialogue lines
    2  shot keyframes
    3  shot videos (first frame = the keyframe)
Params: ``tier``, ``skip`` (subject types not to make), ``force``.
Progress messages are ``<i18n key>|<detail>``; clients translate the part before ``|``.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from storyagent.compose.qc import run_qc
from storyagent.compose.timeline import AssetRef, ShotInput, TimelineInput, build_timeline
from storyagent.config import ConfigError
from storyagent.domain import JobStatus, MediaKind, QualityTier
from storyagent.i18n import get_catalog
from storyagent.media.contracts import AssetRecord, JobRecord, MediaJob
from storyagent.media.ledger import Ledger
from storyagent.media.specs import SpecRecord, SpecStore

from .stages import Ctx, StageError, WorkflowCancelled

PHASES: tuple[tuple[str, ...], ...] = (
    ("character.portrait", "scene.keyframe", "music.bgm", "music.theme_song", "sfx.cue", "dialogue.line"),
    ("shot.keyframe",),
    ("shot.video",),
)
FAILED = {JobStatus.WORKFLOW_ERROR, JobStatus.TRANSPORT_ERROR, JobStatus.TIMEOUT, JobStatus.FAILED_VALIDATION}


def _ledger(ctx: Ctx) -> Ledger:
    ledger: Ledger = ctx.spec_store.ledger
    return ledger


def _shot_id(spec: SpecRecord) -> int | None:
    head = spec.subject_id.split("#")[0]
    return int(head) if spec.subject_type.startswith(("shot.", "dialogue.")) and head.isdigit() else None


def current_asset(ledger: Ledger, spec: SpecRecord) -> AssetRecord | None:
    rows = ledger.list_assets(project_id=spec.project_id, spec_id=spec.id, current_only=True, limit=5)
    return rows[0] if rows else None


def up_to_date(ledger: Ledger, spec: SpecRecord) -> bool:
    asset = current_asset(ledger, spec)
    return asset is not None and (asset.spec_version or 0) >= spec.version


def wait_for_jobs(
    ctx: Ctx, ledger: Ledger, jobs: list[JobRecord], on_progress: Callable[[int, int, int], None],
) -> list[JobRecord]:
    """Block until every job is terminal. Cancelling the run cancels the jobs that have not finished."""
    poll = float(ctx.settings.workflow.get("poll_seconds", 1.0))
    pending = {j.id for j in jobs}
    final: dict[str, JobRecord] = {}
    try:
        while pending:
            if ctx.cancelled():
                raise WorkflowCancelled()
            for jid in list(pending):
                rec = ledger.get_job(jid)
                if rec.status.is_terminal:
                    final[jid] = rec
                    pending.discard(jid)
            on_progress(len(final), len(jobs), sum(1 for j in final.values() if j.status in FAILED))
            if pending:
                time.sleep(poll)
    except WorkflowCancelled:
        for jid in pending:
            ledger.request_cancel(jid)
        raise
    return [final[j.id] for j in jobs]


def _plan_phase(
    ctx: Ctx, ledger: Ledger, specs: list[SpecRecord], types: tuple[str, ...], skipped: list[dict[str, Any]],
    tier: QualityTier, counts: dict[str, int],
) -> list[tuple[SpecRecord, MediaJob]]:
    store: SpecStore = ctx.spec_store
    out: list[tuple[SpecRecord, MediaJob]] = []
    default_voice = str(ctx.settings.workflow.get("default_voice", "")).strip()
    skip = {str(x) for x in ctx.params.get("skip", [])}      # e.g. ["shot.video"]: stills only
    for spec in specs:
        if spec.subject_type not in types or spec.subject_type in skip:
            continue
        if up_to_date(ledger, spec) and not ctx.force():
            counts["reused"] += 1
            continue
        try:
            ctx.settings.model(spec.model_ref) if spec.model_ref else ctx.settings.default_model(spec.kind.value)
        except ConfigError:
            skipped.append({"subject": spec.subject_type, "id": spec.subject_id, "reason": "no_model",
                            "kind": spec.kind.value})
            continue
        extra: dict[str, Any] = {}
        if spec.subject_type == "dialogue.line" and not spec.params.get("voice_ref"):
            if not default_voice:
                skipped.append({"subject": spec.subject_type, "id": spec.subject_id, "reason": "no_voice"})
                continue
            extra["voice_ref"] = default_voice
        if spec.subject_type == "shot.video":
            key = store.current(spec.project_id, "shot.keyframe", spec.subject_id)
            frame = current_asset(ledger, key) if key else None
            if frame is None:
                skipped.append({"subject": spec.subject_type, "id": spec.subject_id, "reason": "no_keyframe"})
                continue
            extra["refs"] = [f"asset:{frame.id}"]
        out.append((spec, store.build_job(spec, tier=tier, seed=spec.seed, shot_id=_shot_id(spec), extra_inputs=extra)))
    return out


# Seconds per job when the ledger has no history for the model yet (only used to shape the progress bar).
FALLBACK_SECONDS = {"image": 45.0, "video": 240.0, "audio.tts": 12.0, "audio.music": 90.0, "audio.sfx": 20.0}


def _expected_seconds(ctx: Ctx, ledger: Ledger, specs: list[SpecRecord], tier: QualityTier) -> float:
    total = 0.0
    skip = {str(x) for x in ctx.params.get("skip", [])}
    for spec in specs:
        if spec.subject_type in skip or not any(spec.subject_type in p for p in PHASES):
            continue
        if up_to_date(ledger, spec) and not ctx.force():
            continue
        try:
            model_ref = spec.model_ref or ctx.settings.default_model(spec.kind.value).ref
        except ConfigError:
            continue
        known = ledger.estimate_seconds(model_ref, tier.value)
        kind = spec.kind.value
        guess = FALLBACK_SECONDS.get(kind) or FALLBACK_SECONDS.get(kind.split(".")[0], 60.0)
        total += float(known or guess)
    return total


def produce(ctx: Ctx) -> dict[str, Any]:
    ledger = _ledger(ctx)
    tier = QualityTier(str(ctx.params.get("tier") or ctx.settings.workflow.get("tier", "draft")))
    specs = ctx.spec_store.list_current(ctx.pid)
    skipped: list[dict[str, Any]] = []
    made = failed = 0
    counts = {"reused": 0}
    failures: list[dict[str, Any]] = []
    total_planned = sum(1 for s in specs if any(s.subject_type in p for p in PHASES))
    ctx.hint_weight(_expected_seconds(ctx, ledger, specs, tier))
    ctx.step(0, max(total_planned, 1), "")
    done_before = 0
    for number, phase in enumerate(PHASES, 1):
        plan = _plan_phase(ctx, ledger, specs, phase, skipped, tier, counts)
        if not plan:
            continue
        jobs: list[JobRecord] = []
        for _spec, job in plan:
            rec, _ = ledger.enqueue(job, ctx.project.settings)
            jobs.append(rec)
        label = f"workflow.progress.phase.{number}|({len(plan)})"

        def report(done: int, n: int, bad: int, base: int = done_before, text: str = label) -> None:
            ctx.step(base + done, max(total_planned, base + n), text)

        results = wait_for_jobs(ctx, ledger, jobs, report)
        for (spec, _), rec in zip(plan, results, strict=True):
            if rec.status is JobStatus.COMPLETED:
                made += 1
            elif rec.status is not JobStatus.CANCELLED:
                failed += 1
                failures.append({"subject": spec.subject_type, "id": spec.subject_id, "code": rec.error_code,
                                 "message": rec.error_message})
        done_before += len(plan)
    ctx.step(total_planned, max(total_planned, 1), "")
    summary = {"made": made, "reused": counts["reused"], "failed": failed, "skipped": len(skipped), "tier": tier.value,
               "failures": failures[:20], "skipped_detail": skipped[:40]}
    if failed:
        raise StageError("produce_failed", f"{failed} media jobs failed", count=failed, summary=summary)
    return summary


def _asset_ref(a: AssetRecord | None, kind: str) -> AssetRef | None:
    return AssetRef(a.id, kind, a.duration_sec) if a else None


def build_episode_timeline(ctx: Ctx, episode: int) -> dict[str, Any]:
    ledger, store = _ledger(ctx), ctx.spec_store
    ep = next((e for e in ctx.store.list_episodes(ctx.pid) if e["number"] == episode), None)
    if ep is None:
        raise StageError("needs_stage", "run the shotplan stage first", stage="shotplan")
    shots = ctx.store.list_shots(ctx.pid, ep["id"])
    if not shots:
        raise StageError("needs_stage", "run the shotplan stage first", stage="shotplan")
    inputs: list[ShotInput] = []
    for shot in shots:
        sid = str(shot["id"])
        vspec, kspec = store.current(ctx.pid, "shot.video", sid), store.current(ctx.pid, "shot.keyframe", sid)
        lines = []
        for idx, line in enumerate(shot["dialogue"]):
            dspec = store.current(ctx.pid, "dialogue.line", f"{sid}#{idx}")
            speech = _asset_ref(current_asset(ledger, dspec) if dspec else None, "audio.tts")
            lines.append((str(line.get("line", "")), speech))
        camera = (shot["render_payload"] or {}).get("camera", {})
        planned = float(camera.get("duration_sec", 3.0))
        inputs.append(ShotInput(
            shot_id=shot["id"], label=f"{shot['act_number']}.{shot['scene_number']}.{shot['shot_number']}",
            planned_sec=planned,
            video=_asset_ref(current_asset(ledger, vspec) if vspec else None, "video.generate"),
            image=_asset_ref(current_asset(ledger, kspec) if kspec else None, "image.generate"),
            lines=lines, narration=str(shot.get("narration", "")),
        ))
    bgm = None
    for m in ctx.store.list_music(ctx.pid):
        if m["kind"] == "bgm":
            spec = store.current(ctx.pid, "music.bgm", str(m["id"]))
            bgm = _asset_ref(current_asset(ledger, spec) if spec else None, "audio.music")
            if bgm:
                break
    profile = ctx.settings.delivery_profile(ctx.project.delivery_profile)
    label, metadata = compliance_marks(ctx)
    return build_timeline(TimelineInput(
        inputs, profile, ctx.settings.delivery["mix"], ctx.settings.delivery["subtitles"], bgm=bgm,
        title=ctx.project.name, label=label, metadata=metadata))


def compliance_marks(ctx: Ctx) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """The visible AIGC label and the container metadata this project asks for (none when compliance is off)."""
    cfg = ctx.settings.compliance_for(ctx.project.settings)
    if not cfg.enabled:
        return None, {}
    text = get_catalog(ctx.project.content_language).t(cfg.explicit_label_key)
    label = {"text": text, "mode": cfg.explicit_label} if cfg.explicit_label != "off" else None
    metadata: dict[str, str] = {}
    if cfg.implicit_metadata:
        metadata = {"comment": f"AIGC; generator=storyagent; label={text}", "encoded_by": "storyagent",
                    "title": ctx.project.name}
    return label, metadata


def compose(ctx: Ctx) -> dict[str, Any]:
    ledger = _ledger(ctx)
    episode = int(ctx.params.get("episode") or 1)
    ctx.step(0, 1, "workflow.progress.timeline|")
    timeline = build_episode_timeline(ctx, episode)
    if not timeline["segments"]:
        raise StageError("needs_stage", "nothing to assemble", stage="shotplan")
    job = MediaJob(kind=MediaKind.COMPOSE_ASSEMBLE, inputs={"timeline": timeline}, project_id=ctx.pid,
                   slot=f"episode_{episode:02d}", tier=QualityTier.FINAL,
                   params={"burn_subtitles": bool(ctx.params.get("burn_subtitles", True))})
    rec, _ = ledger.enqueue(job, ctx.project.settings)

    def report(done: int, total: int, bad: int) -> None:
        live = ledger.get_job(rec.id)
        ctx.step(int(live.progress * 100), 100, f"workflow.progress.assembling|{live.progress_msg}")

    final = wait_for_jobs(ctx, ledger, [rec], report)[0]
    if final.status is not JobStatus.COMPLETED:
        raise StageError("compose_failed", final.error_message or "assemble failed", job_error=final.error_code or "")
    assets = ledger.list_assets(project_id=ctx.pid, kind=MediaKind.COMPOSE_ASSEMBLE.value, current_only=True)
    episode_asset = next((a for a in assets if a.job_id == rec.id and a.slot == job.slot), None)
    ctx.step(90, 100, "workflow.progress.qc|")
    qc = (run_qc(ctx.settings, ctx.settings.home / episode_asset.path, ctx.settings.delivery_profile(
        ctx.project.delivery_profile)) if episode_asset else None)
    ctx.step(100, 100, "")
    return {"episode": episode, "asset_id": episode_asset.id if episode_asset else None, "job_id": rec.id,
            "duration_sec": timeline["total_sec"], "shots": len(timeline["segments"]),
            "warnings": timeline["warnings"][:40], "qc": qc}
