"""The media job contract: request, record, and the adapter protocol.

Every provider (ACE-Step, Phosphene, CosyVoice, h3.c, ...) is wrapped by an ``Adapter``.
Adapters never touch the database; the worker drives them and the ledger records the outcome.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from storyagent.config import Settings
from storyagent.domain import JobStatus, MediaKind, QualityTier
from storyagent.domain.errors import ErrorCode
from storyagent.util import loads


class HealthState(StrEnum):
    """i18n keys: ``health.<value>``."""

    OK = "ok"
    MISSING_MODEL = "missing_model"
    MISSING_TOOL = "missing_tool"
    NOT_RUNNING = "not_running"
    DEGRADED = "degraded"
    PROBE_ONLY = "probe_only"
    DISABLED = "disabled"


@dataclass(frozen=True)
class MediaJob:
    """A request to run something. ``params`` are the highest-priority overrides; the final
    resolved values (see ``media.params``) are frozen into the job at enqueue time."""

    kind: MediaKind
    inputs: Mapping[str, Any]
    project_id: int | None = None
    shot_id: int | None = None
    model_ref: str = ""  # "" => default model for the kind
    tier: QualityTier = QualityTier.FINAL
    slot: str = "main"
    params: Mapping[str, Any] = field(default_factory=dict)
    seed: int | None = None
    priority: int = 5  # lower runs sooner
    spec_id: str | None = None
    spec_version: int | None = None
    idempotency_key: str = ""  # "" => derived from the request
    max_attempts: int | None = None


@dataclass(frozen=True)
class JobRecord:
    id: str
    project_id: int | None
    shot_id: int | None
    spec_id: str | None
    spec_version: int | None
    kind: MediaKind
    provider: str
    model_ref: str
    tier: QualityTier
    slot: str
    status: JobStatus
    priority: int
    inputs: dict[str, Any]
    params: dict[str, Any]
    seed: int | None
    idempotency_key: str
    heavy: bool
    est_memory_gib: float
    est_seconds: int
    license_tag: str
    commercial_ok: bool
    attempt: int
    max_attempts: int
    parent_job_id: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    external_id: str | None
    progress: float
    progress_msg: str
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    created_at: str
    started_at: str | None
    finished_at: str | None
    actual_seconds: float | None
    peak_memory_gib: float | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> JobRecord:
        return cls(
            id=r["id"], project_id=r["project_id"], shot_id=r["shot_id"], spec_id=r["spec_id"],
            spec_version=r["spec_version"], kind=MediaKind(r["kind"]), provider=r["provider"],
            model_ref=r["model_ref"], tier=QualityTier(r["tier"]), slot=r["slot"],
            status=JobStatus(r["status"]), priority=r["priority"], inputs=loads(r["inputs"], {}),
            params=loads(r["params"], {}), seed=r["seed"], idempotency_key=r["idempotency_key"],
            heavy=bool(r["heavy"]), est_memory_gib=r["est_memory_gib"], est_seconds=r["est_seconds"],
            license_tag=r["license_tag"], commercial_ok=bool(r["commercial_ok"]), attempt=r["attempt"],
            max_attempts=r["max_attempts"], parent_job_id=r["parent_job_id"], lease_owner=r["lease_owner"],
            lease_expires_at=r["lease_expires_at"], external_id=r["external_id"], progress=r["progress"],
            progress_msg=r["progress_msg"], error_code=r["error_code"], error_message=r["error_message"],
            cancel_requested=bool(r["cancel_requested"]), created_at=r["created_at"],
            started_at=r["started_at"], finished_at=r["finished_at"], actual_seconds=r["actual_seconds"],
            peak_memory_gib=r["peak_memory_gib"],
        )


@dataclass(frozen=True)
class AssetRecord:
    id: str
    job_id: str | None
    project_id: int | None
    shot_id: int | None
    spec_id: str | None
    spec_version: int | None
    kind: str
    slot: str
    path: str  # relative to the data home
    sha256: str
    bytes: int
    duration_sec: float | None
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool | None
    metadata: dict[str, Any]
    provenance: dict[str, Any]
    version: int
    is_current: bool
    created_at: str
    trashed_at: str | None

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> AssetRecord:
        return cls(
            id=r["id"], job_id=r["job_id"], project_id=r["project_id"], shot_id=r["shot_id"],
            spec_id=r["spec_id"], spec_version=r["spec_version"], kind=r["kind"], slot=r["slot"],
            path=r["path"], sha256=r["sha256"], bytes=r["bytes"], duration_sec=r["duration_sec"],
            width=r["width"], height=r["height"], fps=r["fps"],
            has_audio=None if r["has_audio"] is None else bool(r["has_audio"]),
            metadata=loads(r["metadata"], {}), provenance=loads(r["provenance"], {}),
            version=r["version"], is_current=bool(r["is_current"]), created_at=r["created_at"],
            trashed_at=r["trashed_at"],
        )


class AdapterError(Exception):
    """Raised by adapters. ``code`` fixes the terminal status and the user-facing messages;
    ``params`` fill the ``error.<code>`` i18n placeholders; ``retryable`` marks transient faults."""

    def __init__(
        self, code: ErrorCode, message: str = "", *, retryable: bool = False, **params: object
    ) -> None:
        super().__init__(message or code.value)
        self.code = code
        self.message = message or code.value
        self.retryable = retryable
        self.params = params


@dataclass(frozen=True)
class ValidationIssue:
    code: ErrorCode
    message: str = ""
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Progress:
    state: str  # "running" | "succeeded" | "failed"
    fraction: float = 0.0
    message: str = ""
    error: AdapterError | None = None


@dataclass(frozen=True)
class ArtifactSpec:
    """A file the adapter produced. ``slot``/``kind`` default to the job's."""

    path: Path
    kind: MediaKind | None = None
    slot: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Capabilities:
    kinds: tuple[MediaKind, ...]
    heavy: bool = False
    supports_cancel: bool = True
    supports_reattach: bool = False
    # param name -> {"type": int|float|str|bool|list|dict, "min":…, "max":…, "choices":[…]}
    param_schema: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    poll_interval_sec: float = 2.0


@dataclass(frozen=True)
class HealthReport:
    state: HealthState
    detail: str = ""
    missing: tuple[str, ...] = ()


@dataclass
class JobContext:
    settings: Settings
    job: JobRecord
    work_dir: Path  # scratch space owned by this job; removed after the artifacts are registered
    log: Callable[..., None]  # log(event, level="info", **data) -> job_events
    project_settings: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Adapter(Protocol):
    provider: str

    def capabilities(self) -> Capabilities: ...
    def health(self) -> HealthReport: ...
    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]: ...
    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        """Start the work; return an external id that ``poll``/``fetch``/``cancel`` accept."""
        ...
    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress: ...
    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]: ...
    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None: ...
    def reattach(self, external_id: str, job: JobRecord, ctx: JobContext) -> bool:
        """After a restart: is the work behind ``external_id`` still alive/recoverable?"""
        ...


class AdapterBase:
    """Convenience defaults; concrete adapters override what they support."""

    provider = ""

    def health(self) -> HealthReport:
        return HealthReport(HealthState.OK)

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        return []

    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None:
        return None

    def reattach(self, external_id: str, job: JobRecord, ctx: JobContext) -> bool:
        return False
