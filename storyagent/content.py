"""Content ratings at work: which rating a project / job / asset has, the hard gates of a restricted rating, the vault,
unlock sessions and the export check. Resource management only (see ``docs/RESTRICTED_CONTENT.md``): nothing here
writes or judges a prompt beyond refusing the ones that point at a minor.
"""
from __future__ import annotations

import hashlib
import os
import re
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


# -- unlock sessions -----------------------------------------------------------------------------------------
class UnlockStore:
    """Time-limited permission to see restricted material. ``user_id`` is ``None`` for the local operator (auth
    off); with accounts, each user unlocks for themself."""

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db, self.settings = db, settings

    def start(self, user_id: int | None, actor: str) -> dict[str, Any]:
        ttl = self.settings.content.unlock_ttl_sec
        expires = iso_at(time.time() + ttl)
        with self.db.transaction() as conn:
            conn.execute("UPDATE content_unlocks SET revoked_at=? WHERE user_id IS ? AND revoked_at IS NULL",
                         (now_iso(), user_id))
            conn.execute("INSERT INTO content_unlocks(user_id, actor, expires_at) VALUES (?,?,?)",
                         (user_id, actor, expires))
        return {"unlocked": True, "expires_at": expires, "ttl_sec": ttl}

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
