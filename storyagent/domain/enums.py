from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class MediaKind(StrEnum):
    """What a job *does*, as ``<output domain>.<operation>`` — mutually exclusive by construction.

    generate = new content from prompts/refs, edit = transform existing content under instruction,
    enhance = fidelity-only change (upscale/interpolate/denoise), analyze = produces judgments/text.
    Sub-modes (t2v/i2v/first-last/reference...) are ``params['mode']``, not new kinds.
    i18n keys: ``media.kind.<value>``.
    """

    IMAGE_GENERATE = "image.generate"
    IMAGE_EDIT = "image.edit"
    VIDEO_GENERATE = "video.generate"  # silent or audio stripped
    VIDEO_AV = "video.av"  # joint video + synchronized audio (e.g. MiniMax H3)
    VIDEO_AVATAR = "video.avatar"  # portrait + speech -> talking head
    VIDEO_LIPSYNC = "video.lipsync"  # existing video + speech -> re-timed mouth
    VIDEO_ENHANCE = "video.enhance"  # upscale / frame interpolation
    AUDIO_TTS = "audio.tts"  # incl. voice cloning
    AUDIO_MUSIC = "audio.music"
    AUDIO_SFX = "audio.sfx"
    AUDIO_ENHANCE = "audio.enhance"  # denoise / loudness / ducking stems
    TEXT_ASR = "text.asr"  # transcription (subtitles, clone ref_text)
    COMPOSE_ASSEMBLE = "compose.assemble"  # shots + stems -> episode
    ANALYZE_QC = "analyze.qc"  # technical + semantic quality checks


class QualityTier(StrEnum):
    """Draft -> preview -> final: AIGC is a lottery, so iterate cheap and render dear once.

    i18n keys: ``media.tier.<value>``.
    """

    DRAFT = "draft"  # smoke: tiny resolution/frames, seconds-to-minutes
    PREVIEW = "preview"  # representative look, fast distilled settings
    FINAL = "final"  # delivery quality


class JobStatus(StrEnum):
    """Terminal states follow docs/INDUSTRIAL_RENDER_PIPELINE.md, plus a real ``cancelled``.

    i18n keys: ``status.<value>``.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    WORKFLOW_ERROR = "workflow_error"
    TRANSPORT_ERROR = "transport_error"
    TIMEOUT = "timeout"
    FAILED_VALIDATION = "failed_validation"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self not in (JobStatus.QUEUED, JobStatus.RUNNING)


class ShotStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    GENERATING = "generating"
    GENERATED = "generated"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


_NON_SLUG = re.compile(r"[^\w\-]+", re.UNICODE)


def slugify(text: str, fallback: str = "project") -> str:
    """Filesystem-safe slug that keeps CJK characters (``\\w`` is unicode-aware)."""
    norm = unicodedata.normalize("NFKC", text).strip().lower()
    slug = _NON_SLUG.sub("-", norm).strip("-_")
    return slug[:60] or fallback
