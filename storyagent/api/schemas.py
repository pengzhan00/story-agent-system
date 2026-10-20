"""Request/response models. They are the OpenAPI contract the web client is generated from."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from storyagent.domain import JobStatus, MediaKind, QualityTier


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


# -- errors -----------------------------------------------------------------------------------------
class ErrorBody(BaseModel):
    code: str
    message: str
    remediation: str = ""
    detail: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# -- projects ---------------------------------------------------------------------------------------
class ProjectOut(Out):
    id: int
    slug: str
    name: str
    description: str
    genre: str
    content_language: str
    delivery_profile: str
    aspect_ratio: str
    status: str
    settings: dict[str, Any]
    created_at: str
    updated_at: str


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    genre: str = ""
    description: str = ""
    content_language: str = ""


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    genre: str | None = None
    content_language: str | None = None
    delivery_profile: str | None = None
    aspect_ratio: str | None = None


class ProjectSettingsPatch(BaseModel):
    """Merged into ``projects.settings``; a key set to null is removed."""

    compliance: dict[str, Any] | None = None
    models: dict[str, dict[str, Any]] | None = None


# -- jobs -------------------------------------------------------------------------------------------
class JobOut(Out):
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
    heavy: bool
    est_memory_gib: float
    est_seconds: int
    license_tag: str
    commercial_ok: bool
    attempt: int
    max_attempts: int
    parent_job_id: str | None
    progress: float
    progress_msg: str
    error_code: str | None
    error_message: str | None
    cancel_requested: bool
    created_at: str
    started_at: str | None
    finished_at: str | None
    actual_seconds: float | None


class JobCreate(BaseModel):
    kind: MediaKind
    inputs: dict[str, Any] = Field(default_factory=dict)
    project_id: int | None = None
    shot_id: int | None = None
    model_ref: str = ""
    tier: QualityTier = QualityTier.FINAL
    slot: str = "main"
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    priority: int = Field(default=5, ge=0, le=100)


class JobCreated(BaseModel):
    job: JobOut
    created: bool = Field(description="false: an identical request was already active; its job is returned")


class JobEvent(BaseModel):
    id: int
    ts: str
    level: str
    event: str
    data: dict[str, Any]


class RetryRequest(BaseModel):
    new_seed: bool = False


class CancelResult(BaseModel):
    status: JobStatus


# -- assets -----------------------------------------------------------------------------------------
class AssetOut(Out):
    id: str
    job_id: str | None
    project_id: int | None
    shot_id: int | None
    spec_id: str | None
    spec_version: int | None
    kind: str
    slot: str
    path: str
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


class StaleAsset(BaseModel):
    asset: AssetOut
    latest_spec_version: int


# -- specs ------------------------------------------------------------------------------------------
class SpecOut(Out):
    id: str
    project_id: int
    subject_type: str
    subject_id: str
    slot: str
    version: int
    parent_version: int | None
    kind: MediaKind
    model_ref: str
    prompt: str
    negative_prompt: str
    text_payload: str
    params: dict[str, Any]
    seed: int | None
    refs: list[Any]
    depends_on: list[dict[str, Any]]
    author: str
    note: str
    created_at: str
    locked: bool = False


class SpecCreate(BaseModel):
    subject_type: str
    subject_id: str
    kind: MediaKind
    slot: str = "main"
    model_ref: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    text_payload: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    refs: list[Any] = Field(default_factory=list)
    depends_on: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class SpecEdit(BaseModel):
    """Only the fields that are sent change. ``params_patch`` merges into params; ``params`` replaces them."""

    model_ref: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    text_payload: str | None = None
    params: dict[str, Any] | None = None
    params_patch: dict[str, Any] | None = None
    seed: int | None = None
    refs: list[Any] | None = None
    depends_on: list[dict[str, Any]] | None = None
    note: str = ""


class SpecRevert(BaseModel):
    version: int = Field(ge=1)


class SpecDiffItem(BaseModel):
    field: str
    before: Any
    after: Any


class SpecLockRequest(BaseModel):
    note: str = ""


class RegenerateRequest(BaseModel):
    n_candidates: int = Field(default=1, ge=1, le=16)
    vary_seed: bool = True
    tier: QualityTier = QualityTier.FINAL
    shot_id: int | None = None
    priority: int = Field(default=5, ge=0, le=100)


class ReviewItemOut(BaseModel):
    spec: SpecOut
    changed_dependencies: list[dict[str, Any]]


# -- meta / system ----------------------------------------------------------------------------------
class LabeledValue(BaseModel):
    value: str
    label: str


class ModelOut(BaseModel):
    ref: str
    kind: str
    provider: str
    version: str
    family: str
    heavy: bool
    est_memory_gib: float
    license_tag: str
    commercial_ok: bool
    attribution: str
    prompt_profile: str
    timeout_sec: int
    params: dict[str, Any]
    tiers: dict[str, dict[str, Any]]


class RecipeStepOut(BaseModel):
    kind: str
    model: str
    tier: str
    slot: str
    when: str
    params: dict[str, Any]


class RecipeOut(BaseModel):
    name: str
    label: str
    steps: list[RecipeStepOut]


class QueueState(BaseModel):
    queued: int
    running: int
    waiting: dict[str, int] = Field(description="queued jobs per wait reason key (wait.<reason>)")


class Meta(BaseModel):
    version: str
    locale: str
    locales: list[str]
    kinds: list[LabeledValue]
    tiers: list[LabeledValue]
    statuses: list[LabeledValue]
    subject_types: list[LabeledValue]
    models: list[ModelOut]
    defaults: dict[str, str]
    recipes: list[RecipeOut]
    compliance_enabled: bool
    llm_unload_policy: str
    queue: QueueState


class HealthEntry(BaseModel):
    name: str
    category: str
    state: str
    label: str
    detail: str
    missing: list[str]
    required: bool
    ok: bool


class HealthOut(BaseModel):
    exit_code: Literal[0, 1]
    entries: list[HealthEntry]


class Ready(BaseModel):
    ok: bool = True
    schema_version: int
