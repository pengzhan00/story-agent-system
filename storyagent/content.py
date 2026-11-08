"""Content ratings at work: which rating a project / job / asset has, the hard gates of a restricted rating, the vault,
unlock sessions and the export check. Resource management only (see ``docs/RESTRICTED_CONTENT.md``): nothing here
writes or judges a prompt beyond refusing the ones that point at a minor.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from storyagent.config import Settings
from storyagent.db import Database
from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.media.contracts import JobRecord, ValidationIssue
from storyagent.media.probe import find_tool
from storyagent.util import iso_at, now_iso

_REF = re.compile(r"^(asset|library|job):")
_DIGITS = re.compile(r"\d+")
MASK_SIZE = 96


class ContentError(ValueError):
    """``code`` is an i18n key suffix (``content.error.<code>``)."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code, self.params = code, params


# -- ratings ------------------------------------------------------------------------------------------------
def project_rating(db: Database, project_id: int | None) -> str:
    if project_id is None:
        return ""
    row = db.conn.execute("SELECT rating FROM projects WHERE id=?", (project_id,)).fetchone()
    return str(row["rating"]) if row else ""


def effective_rating(settings: Settings, db: Database, project_id: int) -> str:
    """The project's rating, or the highest rating of any asset in it (a general project that used a restricted
    model has restricted assets, and is treated as restricted wherever material leaves)."""
    rating = settings.content.policy(project_rating(db, project_id)).name
    for row in db.conn.execute("SELECT DISTINCT rating FROM media_assets WHERE project_id=?", (project_id,)):
        rating = settings.content.higher(rating, row["rating"])
    return rating


def model_rating(settings: Settings, model_ref: str) -> str:
    model = settings.models.get(model_ref)
    return str(model.extra.get("rating", settings.content.default_rating)) if model else settings.content.default_rating


def job_rating(settings: Settings, project_rating_: str, model_ref: str) -> str:
    """What a job's output is rated: the higher of its project and its model."""
    return settings.content.higher(project_rating_, model_rating(settings, model_ref))


def digest_text(text: str) -> str:
    """What the model-call log keeps instead of a restricted prompt."""
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]} ({len(text)} chars)"


# -- the vault ----------------------------------------------------------------------------------------------
def vault_ready(settings: Settings) -> bool:
    """The vault can be written: its volume (``volume_mount``) is mounted, when one is configured."""
    mount = settings.content.volume_mount
    if mount and not os.path.ismount(os.path.expanduser(mount)):
        return False
    return True


# An unlock without an end (``unlock_ttl_sec = 0``) is stored with this far-away time and shown without one.
NO_EXPIRY = "9999-12-31T23:59:59.000Z"


# -- the hard gates ------------------------------------------------------------------------------------------
def _strings(node: Any) -> Iterator[str]:
    if isinstance(node, str):
        if not _REF.match(node) and not ("/" in node and " " not in node):
            yield node
    elif isinstance(node, Mapping):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _strings(value)


def _blocked(code: str, **params: object) -> ValidationIssue:
    """``code: details`` is what the job's error message shows (the text of the error itself is a fixed i18n key)."""
    detail = ", ".join(f"{k}={v}" for k, v in params.items())
    return ValidationIssue(ErrorCode.CONTENT_BLOCKED, f"{code}: {detail}" if detail else code,
                           {"reason": code, **params})


def character_gate_issues(settings: Settings, db: Database, project_id: int) -> tuple[list[ValidationIssue], list[str]]:
    """The gates that hold for a whole project, whatever the job: every character an adult and confirmed, and the
    vault there. Also returns the characters' own texts, which are checked for blocked terms with the job's."""
    cfg = settings.content
    issues: list[ValidationIssue] = []
    texts: list[str] = []
    rows = db.conn.execute(
        "SELECT name, role, age, appearance, personality, background, prompt_template, adult_confirmed "
        "FROM characters WHERE project_id=?", (project_id,)).fetchall()
    for c in rows:
        if not c["adult_confirmed"]:
            issues.append(_blocked("adult_not_confirmed", name=c["name"]))
        ages = [int(x) for x in _DIGITS.findall(str(c["age"]))]
        if ages and min(ages) < cfg.min_adult_age:
            issues.append(_blocked("minor_age", name=c["name"], age=c["age"]))
        texts += [str(c[k]) for k in ("role", "appearance", "personality", "background", "prompt_template")]
    return issues, texts


def gate_issues(settings: Settings, db: Database, job: JobRecord, project_id: int | None, rating: str
                ) -> list[ValidationIssue]:
    """The checks a job of a gated rating must pass. The first two cannot be switched off by the project or by the
    compliance switch: adults only, and consent for every real voice or likeness."""
    cfg = settings.content
    policy = cfg.policy(rating)
    if not policy.gates:
        return []
    issues: list[ValidationIssue] = []
    if policy.vault and not vault_ready(settings):
        issues.append(ValidationIssue(ErrorCode.VAULT_UNAVAILABLE, "the vault volume is not mounted",
                                      {"mount": cfg.volume_mount}))
    texts = list(_strings(job.inputs))
    if project_id is not None:
        found, own = character_gate_issues(settings, db, project_id)
        issues += found
        texts += own
    for text in texts:
        term = cfg.blocked_in(text)
        if term:
            issues.append(_blocked("blocked_term", term=term))
            break
    voice = job.inputs.get("voice_ref") or {}
    if job.kind in (MediaKind.AUDIO_TTS, MediaKind.AUDIO_CONVERT) and voice.get("audio") and not voice.get("consent"):
        issues.append(ValidationIssue(ErrorCode.CONSENT_REQUIRED, "voice reference without consent"))
    if job.kind is MediaKind.TRAIN_LORA and not job.inputs.get("consent"):
        issues.append(ValidationIssue(ErrorCode.CONSENT_REQUIRED, "training on a likeness without consent"))
    model = settings.models.get(job.model_ref)
    if model is not None and model.remote and not policy.allow_remote:
        issues.append(ValidationIssue(ErrorCode.REMOTE_NOT_ALLOWED, f"{job.model_ref} runs in the cloud",
                                      {"model": job.model_ref,
                                       "service": str(model.extra.get("service", model.provider))}))
    return issues


def plan_gates(settings: Settings, db: Database, project_id: int, rating: str) -> list[dict[str, str]]:
    """What would stop every job of this project before it starts, listed for the production plan (``[]`` when the
    rating has no gates). The characters' own texts are checked for blocked terms like a job's."""
    policy = settings.content.policy(rating)
    if not policy.gates:
        return []
    out: list[dict[str, str]] = []
    if policy.vault and not vault_ready(settings):
        out.append({"reason": "vault_unavailable", "name": "", "detail": settings.content.volume_mount})
    issues, texts = character_gate_issues(settings, db, project_id)
    for i in issues:
        out.append({"reason": str(i.params.get("reason", "")), "name": str(i.params.get("name", "")),
                    "detail": str(i.params.get("age", ""))})
    for text in texts:
        term = settings.content.blocked_in(text)
        if term:
            out.append({"reason": "blocked_term", "name": "", "detail": term})
            break
    return out


# -- unlock sessions -----------------------------------------------------------------------------------------
class UnlockStore:
    """Time-limited permission to see restricted material. ``user_id`` is ``None`` for the local operator (auth
    off); with accounts, each user unlocks for themself."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db, self.settings = db, settings

    def start(self, user_id: int | None, actor: str) -> dict[str, Any]:
        ttl = self.settings.content.unlock_ttl_sec
        expires = iso_at(time.time() + ttl) if ttl > 0 else NO_EXPIRY
        with self.db.transaction() as conn:
            conn.execute("UPDATE content_unlocks SET revoked_at=? WHERE user_id IS ? AND revoked_at IS NULL",
                         (now_iso(), user_id))
            conn.execute("INSERT INTO content_unlocks(user_id, actor, expires_at) VALUES (?,?,?)",
                         (user_id, actor, expires))
        return {"unlocked": True, "expires_at": None if ttl <= 0 else expires, "ttl_sec": ttl}

    def active(self, user_id: int | None) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT id, created_at, expires_at FROM content_unlocks "
            "WHERE user_id IS ? AND revoked_at IS NULL AND expires_at>? ORDER BY id DESC LIMIT 1",
            (user_id, now_iso())).fetchone()
        return dict(row) if row else None

    def stop(self, user_id: int | None) -> bool:
        cur = self.db.conn.execute("UPDATE content_unlocks SET revoked_at=? WHERE user_id IS ? AND revoked_at IS NULL",
                                   (now_iso(), user_id))
        return cur.rowcount > 0


# -- leaving the machine ------------------------------------------------------------------------------------
def check_export(settings: Settings, db: Database, project_id: int, *, confirmed: bool = False) -> str:
    """Raise ``ContentError('export_blocked')`` when the project holds material of a rating that is not exportable and
    the caller has not confirmed. Returns the effective rating."""
    rating = effective_rating(settings, db, project_id)
    if not settings.content.policy(rating).allow_export and not confirmed:
        raise ContentError("export_blocked", rating=rating)
    return rating


# -- rating an asset by hand --------------------------------------------------------------------------------
def rate_asset(settings: Settings, ledger: Any, asset_id: str, rating: str) -> Any:
    """Set the rating of one asset by hand. Neither a model nor a plan rates a picture by itself, so this is how a
    finished asset is marked restricted (or taken back to an open rating). When the two ratings keep their files in
    different places (the vault and the open project folders) the file moves with the record; if the record cannot
    be written the file goes back, so path and file never disagree."""
    cfg = settings.content
    if not cfg.known(rating):
        raise ContentError("unknown_rating", rating=rating)
    asset = ledger.get_asset(asset_id)
    if asset.rating == rating:
        return asset
    was_vault, will_vault = cfg.policy(asset.rating).vault, cfg.policy(rating).vault
    moved: tuple[Path, Path] | None = None
    path = asset.path
    if was_vault != will_vault:
        if will_vault and not vault_ready(settings):
            raise ContentError("vault_unavailable")
        src_root = settings.vault_dir if was_vault else settings.projects_dir
        dst_root = settings.vault_dir if will_vault else settings.projects_dir
        src = settings.home / asset.path
        try:
            relative = src.relative_to(src_root)
        except ValueError:
            raise ContentError("asset_not_in_place", asset=asset.id) from None
        dst = dst_root / relative
        if dst.exists():
            raise ContentError("target_exists", asset=asset.id)
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved = (src, dst)
        path = dst.relative_to(settings.home).as_posix()
    try:
        ledger.db.conn.execute("UPDATE media_assets SET rating=?, path=? WHERE id=?", (rating, path, asset.id))
    except Exception:
        if moved is not None:
            shutil.move(str(moved[1]), str(moved[0]))
        raise
    (settings.home / "cache" / "mask" / f"{asset.id}.jpg").unlink(missing_ok=True)   # a stale masked thumbnail
    return ledger.get_asset(asset.id)


# -- masked thumbnails --------------------------------------------------------------------------------------
def mask_thumbnail(settings: Settings, source: Path, dest: Path) -> bool:
    """A tiny, heavily blurred JPEG of an image or video (cached at ``dest``); ``False`` when there is nothing to show
    (audio, no ffmpeg)."""
    if dest.is_file():
        return True
    ffmpeg = find_tool(settings, "ffmpeg")
    if ffmpeg is None or not source.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(source), "-frames:v", "1",
            "-vf", f"scale={MASK_SIZE}:{MASK_SIZE}:force_original_aspect_ratio=decrease,boxblur=8:3", "-q:v", "8",
            str(dest)]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=60, check=False)  # noqa: S603 - fixed argv
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0 or not dest.is_file():
        dest.unlink(missing_ok=True)
        return False
    return True
