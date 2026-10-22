"""The two stages that spend the machine's time: ``produce`` (specs -> media) and ``compose`` (media -> episode).

Both hand work to the media ledger and *wait* for the worker, so they show real progress (jobs finished of jobs
needed) and are safe to re-run: ``produce`` skips every spec whose current asset was made from its current version,
and the ledger de-duplicates identical active jobs.

Phases (each waits for the one before, because a video starts from its keyframe):
    1  portraits, scene keyframes, music, theme song, sound effects, dialogue lines
    2  shot keyframes
    3  shot videos (first frame = the keyframe)
Params: ``tier``, ``skip`` (subject types not to make), ``only`` (spec ids: make just these - the confirmed
production plan, see ``workflow/plan.py``), ``force``.
A shot keyframe is composed from what the earlier phase made: the portraits of the characters in the shot and the
scene keyframe go in as reference images of an image-edit job (``[workflow] keyframe_from_references``), so the faces
and the place stay the ones that were approved instead of being re-imagined from words.
Progress messages are ``<i18n key>|<detail>``; clients translate the part before ``|``.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from storyagent.compose.pacing import duration_window
from storyagent.compose.qc import run_qc
from storyagent.compose.timeline import AssetRef, Card, ShotInput, TimelineInput, build_timeline
from storyagent.config import ConfigError, Settings
from storyagent.domain import JobStatus, MediaKind, QualityTier
from storyagent.i18n import get_catalog
from storyagent.media.compliance import visible_marks
from storyagent.media.contracts import AssetRecord, JobRecord, MediaJob
from storyagent.media.ledger import Ledger, LedgerError
from storyagent.media.specs import SpecRecord, SpecStore
from storyagent.story import StoryStore

from .stages import Ctx, StageError, WorkflowCancelled
from .tiering import policy_from, video_shots

PHASES: tuple[tuple[str, ...], ...] = (
    ("character.portrait", "scene.keyframe", "music.bgm", "music.theme_song", "shot.sfx",
     "dialogue.line"),
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
    """The asset currently in this spec's slot. Every spec version is its own row, so after an edit the asset made
    from an older version is still the slot's current one - it stays in use (and counts as outdated, see
    ``up_to_date``) until a new asset replaces it."""
    return slot_asset(ledger, spec.project_id, spec.subject_type, spec.subject_id, spec.slot, spec.id)


def slot_asset(
    ledger: Ledger, project_id: int, subject_type: str, subject_id: str, slot: str, spec_id: str | None = None,
) -> AssetRecord | None:
    if spec_id:
        rows = ledger.list_assets(project_id=project_id, spec_id=spec_id, current_only=True, limit=5)
        if rows:
            return rows[0]
    older = ledger.db.conn.execute(
        """SELECT a.id FROM media_assets a JOIN gen_specs s ON s.id = a.spec_id
           WHERE s.project_id=? AND s.subject_type=? AND s.subject_id=? AND s.slot=?
             AND a.is_current=1 AND a.trashed_at IS NULL ORDER BY s.version DESC, a.rowid DESC LIMIT 1""",
        (project_id, subject_type, subject_id, slot)).fetchone()
    return ledger.get_asset(older["id"]) if older else None


def allowed_models(settings: Settings, spec: SpecRecord) -> set[str]:
    """The models the project's pipeline plan allows for this spec: its own choice, else the plan's model for the kind
    (a shot keyframe may also come from the plan's image-edit model, which composes it from reference images)."""
    allowed: set[str] = set()
    try:
        allowed.add(spec.model_ref or settings.default_model(spec.kind.value).ref)
        if spec.subject_type == "shot.keyframe" and not spec.model_ref:
            allowed.add(settings.default_model(MediaKind.IMAGE_EDIT.value).ref)
    except ConfigError:
        pass
    return allowed


def up_to_date(ledger: Ledger, spec: SpecRecord, settings: Settings | None = None) -> bool:
    """Made from this version of the spec, and not older than what it was built on: a clip whose keyframe has been
    made again since is outdated even though its own spec did not change. With ``settings`` (the project's own, i.e.
    its pipeline plan) an asset made by a model the plan does not use - after the plan was changed - is outdated too;
    an imported file (no job) is never judged by its model."""
    asset = current_asset(ledger, spec)
    if asset is None or (asset.spec_version or 0) < spec.version:
        return False
    for dep in spec.depends_on:
        base = slot_asset(ledger, spec.project_id, str(dep.get("subject_type", "")), str(dep.get("subject_id", "")),
                          str(dep.get("slot", "main")))
        if base is not None and base.created_at > asset.created_at:
            return False
    if settings is not None and asset.job_id:
        try:
            made_by = ledger.get_job(asset.job_id).model_ref
        except LedgerError:
            return True
        allowed = allowed_models(settings, spec)
        if allowed and made_by and made_by not in allowed:
            return False
    return True


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


def default_voice_of(settings: Settings, project_settings: dict[str, Any] | None) -> str:
    """Voice for a character that has none: the project's own choice, else ``[workflow] default_voice``."""
    own = str((project_settings or {}).get("default_voice") or "").strip()
    return own or str(settings.workflow.get("default_voice", "")).strip()


def _plan_phase(
    ctx: Ctx, ledger: Ledger, specs: list[SpecRecord], types: tuple[str, ...], skipped: list[dict[str, Any]],
    tier: QualityTier, counts: dict[str, int],
) -> list[tuple[SpecRecord, MediaJob]]:
    store: SpecStore = ctx.spec_store
    out: list[tuple[SpecRecord, MediaJob]] = []
    default_voice = default_voice_of(ctx.settings, ctx.project.settings)
    picks = video_picks(ctx.settings, ctx.store, ledger, ctx.pid, ctx.project.settings, tier)
    skip = {str(x) for x in ctx.params.get("skip", [])}      # e.g. ["shot.video"]: stills only
    only = {str(x) for x in ctx.params.get("only", [])}      # the confirmed plan: just these specs
    for spec in specs:
        if spec.subject_type not in types or spec.subject_type in skip or (only and spec.id not in only):
            continue
        if up_to_date(ledger, spec, ctx.settings) and not ctx.force():
            counts["reused"] += 1
            continue
        if spec.subject_type == "shot.video" and spec.subject_id not in picks and spec.id not in only:
            skipped.append({"subject": spec.subject_type, "id": spec.subject_id, "reason": "still"})
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
        job = store.build_job(spec, tier=tier, seed=spec.seed, shot_id=_shot_id(spec), extra_inputs=extra)
        if spec.subject_type == "shot.keyframe":
            job = keyframe_from_references(ctx, ledger, spec, job)
        out.append((spec, job))
    return out


def keyframe_from_references(ctx: Ctx, ledger: Ledger, spec: SpecRecord, job: MediaJob) -> MediaJob:
    """Compose the shot from the approved portraits and the scene image: an image-edit job whose reference images are
    the current assets of the specs this keyframe depends on. Without any (or switched off), the job stays as it was."""
    modes = scene_modes(ctx.store, ctx.pid)
    if not compose_wanted(ctx.settings, modes, spec) or spec.kind is not MediaKind.IMAGE_GENERATE:
        return job
    refs: list[str] = []
    for dep in spec.depends_on:
        if dep.get("subject_type") not in ("character.portrait", "scene.keyframe"):
            continue
        found = ctx.spec_store.current(spec.project_id, str(dep["subject_type"]), str(dep["subject_id"]),
                                       str(dep.get("slot", "main")))
        asset = current_asset(ledger, found) if found else None
        if asset is not None:
            refs.append(f"asset:{asset.id}")
    if not refs:
        return job
    try:
        model = ctx.settings.model(spec.model_ref) if spec.model_ref and ctx.settings.model(
            spec.model_ref).kind == MediaKind.IMAGE_EDIT.value else ctx.settings.default_model(
                MediaKind.IMAGE_EDIT.value)
    except ConfigError:
        return job
    # the edit model has its own parameter defaults (tier layers resolve in the worker from model_ref and tier)
    return MediaJob(**{**job.__dict__, "kind": MediaKind.IMAGE_EDIT, "model_ref": model.ref,
                       "inputs": {**job.inputs, "refs": [*refs, *job.inputs.get("refs", [])]}})


# Seconds per job when the ledger has no history for the model yet (only used to shape the progress bar and the plan).
# image.edit (a keyframe composed from reference images) was measured at about 11 minutes, plain generation at 1.5.
FALLBACK_SECONDS = {"image": 45.0, "image.edit": 600.0, "video": 240.0, "audio.tts": 12.0, "audio.music": 90.0,
                    "audio.sfx": 20.0}


def fallback_seconds(settings: Settings, model_ref: str, kind: str) -> float:
    """Seconds per job to assume without history: the model's own ``est_seconds`` (models.toml) if it states one, else
    the kind's figure above."""
    try:
        own = float(settings.model(model_ref).extra.get("est_seconds", 0) or 0)
    except (ConfigError, TypeError, ValueError):
        own = 0.0
    return own or float(FALLBACK_SECONDS.get(kind) or FALLBACK_SECONDS.get(kind.split(".")[0], 30.0))


def scene_modes(store: StoryStore, project_id: int) -> dict[str, str]:
    """``keyframe_mode`` of every location, by the id its scene-keyframe spec carries as subject."""
    return {str(loc["id"]): str(loc.get("keyframe_mode") or "auto") for loc in store.list_locations(project_id)}


def compose_wanted(settings: Settings, modes: dict[str, str], spec: SpecRecord) -> bool:
    """Whether this shot keyframe is composed from reference images: the mode of its location when set, else the
    project default."""
    for dep in spec.depends_on:
        mode = modes.get(str(dep.get("subject_id"))) if dep.get("subject_type") == "scene.keyframe" else None
        if mode in ("compose", "text"):
            return mode == "compose"
    return bool(settings.workflow.get("keyframe_from_references", True))


def keyframe_edit_model(settings: Settings, spec: SpecRecord, modes: dict[str, str]) -> str:
    """The image-edit model a shot keyframe will be composed with (``""`` = it is generated from words alone). Mirrors
    ``keyframe_from_references`` without needing the portraits to exist yet, so the plan can state the real cost."""
    if (spec.subject_type != "shot.keyframe" or spec.kind is not MediaKind.IMAGE_GENERATE
            or not compose_wanted(settings, modes, spec)):
        return ""
    if not any(d.get("subject_type") in ("character.portrait", "scene.keyframe") for d in spec.depends_on):
        return ""
    try:
        if spec.model_ref and settings.model(spec.model_ref).kind == MediaKind.IMAGE_EDIT.value:
            return spec.model_ref
        return settings.default_model(MediaKind.IMAGE_EDIT.value).ref
    except ConfigError:
        return ""


def clip_seconds(settings: Settings, ledger: Ledger, tier: QualityTier) -> float:
    """What one video clip is expected to cost on this machine (measured when there is history)."""
    try:
        ref = settings.default_model("video.generate").ref
    except ConfigError:
        return FALLBACK_SECONDS["video"]
    return float(ledger.estimate_seconds(ref, tier.value) or FALLBACK_SECONDS["video"])


def video_picks(
    settings: Settings, store: StoryStore, ledger: Ledger, project_id: int, project_settings: dict[str, Any] | None,
    tier: QualityTier,
) -> set[str]:
    """Shot ids (as spec subject ids) that get a video clip under the tiering policy and the creator's overrides."""
    episodes: dict[int, list[dict[str, Any]]] = {}
    for shot in store.list_shots(project_id):
        episodes.setdefault(shot["episode_id"], []).append(shot)
    policy = policy_from(settings.workflow, project_settings, clip_seconds(settings, ledger, tier))
    return {str(i) for i in video_shots(episodes, policy)}


def _expected_seconds(ctx: Ctx, ledger: Ledger, specs: list[SpecRecord], tier: QualityTier) -> float:
    total = 0.0
    skip = {str(x) for x in ctx.params.get("skip", [])}
    only = {str(x) for x in ctx.params.get("only", [])}
    modes = scene_modes(ctx.store, ctx.pid)
    picks = video_picks(ctx.settings, ctx.store, ledger, ctx.pid, ctx.project.settings, tier)
    for spec in specs:
        if spec.subject_type in skip or not any(spec.subject_type in p for p in PHASES):
            continue
        if only and spec.id not in only:
            continue
        if spec.subject_type == "shot.video" and spec.subject_id not in picks and spec.id not in only:
            continue
        if up_to_date(ledger, spec, ctx.settings) and not ctx.force():
            continue
        kind = spec.kind.value
        edit_ref = keyframe_edit_model(ctx.settings, spec, modes)
        try:
            model_ref = edit_ref or spec.model_ref or ctx.settings.default_model(spec.kind.value).ref
        except ConfigError:
            continue
        if edit_ref:
            kind = MediaKind.IMAGE_EDIT.value
        known = ledger.estimate_seconds(model_ref, tier.value)
        total += float(known or fallback_seconds(ctx.settings, model_ref, kind))
    return total


def produce(ctx: Ctx) -> dict[str, Any]:
    ledger = _ledger(ctx)
    tier = QualityTier(str(ctx.params.get("tier") or ctx.settings.workflow.get("tier", "draft")))
    specs = ctx.spec_store.list_current(ctx.pid)
    skipped: list[dict[str, Any]] = []
    made = failed = 0
    counts = {"reused": 0}
    failures: list[dict[str, Any]] = []
    only = {str(x) for x in ctx.params.get("only", [])}
    total_planned = sum(1 for s in specs if any(s.subject_type in p for p in PHASES) and (not only or s.id in only))
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


def edit_options(ctx: Ctx) -> dict[str, Any]:
    """Cards and fades: ``delivery.toml [edit]`` < the project's ``settings.edit`` < the run's ``edit`` parameter."""
    out = dict(ctx.settings.delivery.get("edit", {}))
    for layer in (ctx.project.settings.get("edit"), ctx.params.get("edit")):
        if isinstance(layer, dict):
            out.update(layer)
    return out


def episode_cards(ctx: Ctx, episode: int, opts: dict[str, Any]) -> tuple[list[Card], list[Card]]:
    """(cards before the story, cards after it) for this episode, in the project's content language."""
    tr = get_catalog(ctx.project.content_language).t
    eps = {e["number"]: e for e in ctx.store.list_episodes(ctx.pid)}
    me, before, after = eps.get(episode), eps.get(episode - 1), eps.get(episode + 1)
    intro: list[Card] = []
    outro: list[Card] = []
    if opts.get("recap_card") and before and str(before["summary"]).strip():
        intro.append(Card("recap", (tr("compose.card.recap"), str(before["summary"]).strip()[:240]),
                          float(opts.get("recap_sec", 3.5))))
    if opts.get("title_card") and me:
        heading = tr("compose.card.episode", season=me["season"], n=me["number_in_season"])
        intro.append(Card("title", (ctx.project.name, f"{heading} {me['title']}".strip()),
                          float(opts.get("title_sec", 2.0))))
    if opts.get("next_card"):
        lines = [tr("compose.card.next")]
        if after and str(after["title"]).strip():
            lines.append(tr("compose.card.next_title", title=str(after["title"]).strip()))
        outro.append(Card("next", tuple(lines), float(opts.get("next_sec", 2.5))))
    return intro, outro


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
        effects = []
        for idx in range(len((shot["render_payload"].get("audio") or {}).get("sfx_cues", []))):
            espec = store.current(ctx.pid, "shot.sfx", f"{sid}#{idx}")
            effect = _asset_ref(current_asset(ledger, espec) if espec else None, "audio.sfx")
            if effect:
                effects.append((effect, 0.0))                       # an effect starts with its shot
        camera = (shot["render_payload"] or {}).get("camera", {})
        planned = float(camera.get("duration_sec", 3.0))
        inputs.append(ShotInput(
            shot_id=shot["id"], label=f"{shot['act_number']}.{shot['scene_number']}.{shot['shot_number']}",
            planned_sec=planned,
            video=_asset_ref(current_asset(ledger, vspec) if vspec else None, "video.generate"),
            image=_asset_ref(current_asset(ledger, kspec) if kspec else None, "image.generate"),
            lines=lines, sfx=effects, narration=str(shot.get("narration", "")),
            scene=f"{shot['act_number']}.{shot['scene_number']}",
        ))
    bgm = None
    cues = [m for m in ctx.store.list_music(ctx.pid) if m["kind"] == "bgm"]
    cues.sort(key=lambda m: not str(m["scene_ref"]).startswith(f"ep{episode:02d}_"))     # this episode's cues first
    for m in cues:
        spec = store.current(ctx.pid, "music.bgm", str(m["id"]))
        bgm = _asset_ref(current_asset(ledger, spec) if spec else None, "audio.music")
        if bgm:
            break
    profile = ctx.settings.delivery_profile(ctx.project.delivery_profile)
    label, metadata = compliance_marks(ctx)
    opts = edit_options(ctx)
    intro, outro = episode_cards(ctx, episode, opts)
    return build_timeline(TimelineInput(
        inputs, profile, ctx.settings.delivery["mix"], ctx.settings.delivery["subtitles"], bgm=bgm,
        title=ctx.project.name, label=label, metadata=metadata, intro_cards=intro, outro_cards=outro,
        scene_fade_sec=float(opts.get("scene_fade_sec", 0) or 0)))


def compliance_marks(ctx: Ctx) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """The visible AIGC label and the container metadata this project asks for (none when compliance is off)."""
    return visible_marks(ctx.settings, ctx.project.settings, ctx.project.content_language, ctx.project.name)


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
    profile = dict(ctx.settings.delivery_profile(ctx.project.delivery_profile))
    target = ctx.target_sec()
    if target:                    # an explicit target replaces the profile's length range
        profile["min_episode_sec"], profile["max_episode_sec"] = duration_window(
            target, float(ctx.settings.delivery["pacing"]["tolerance"]))
    qc = run_qc(ctx.settings, ctx.settings.home / episode_asset.path, profile, timeline["segments"]) \
        if episode_asset else None
    ctx.step(100, 100, "")
    return {"episode": episode, "asset_id": episode_asset.id if episode_asset else None, "job_id": rec.id,
            "duration_sec": timeline["total_sec"], "shots": len(timeline["segments"]),
            "warnings": timeline["warnings"][:40], "qc": qc}
