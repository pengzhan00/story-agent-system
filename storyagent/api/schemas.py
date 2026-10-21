"""Request/response models. They are the OpenAPI contract the web client is generated from."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from storyagent.domain import JobStatus, MediaKind, QualityTier


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class Strict(BaseModel):
    """Response models whose defaulted fields are still always present: the generated TS types mark them required."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


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


class ProjectCard(Strict):
    """One tile of the project list: the project, how far it is, and the finished episode if there is one."""

    id: int
    name: str
    description: str
    genre: str
    status: str
    stages_done: int
    stages_total: int
    running: bool
    episode_asset_id: str | None
    episode_seconds: float | None
    episode_number: int | None
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
    allow_restricted_licenses: bool | None = Field(
        default=None, description="acknowledge models whose licence restricts commercial use or territory")
    allow_remote: bool | None = Field(
        default=None, description="allow models that run in the cloud (prompts and reference files leave this machine)")
    target_sec: float | None = Field(
        default=None, description="wanted episode length in seconds (advert to feature film); null = the profile's")
    default_voice: str | None = Field(
        default=None, description="voice (library:voice/<name>) for characters with none; null = the global one")


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
    depends_on: str | None = None
    pipeline_id: str | None = None
    pipeline_step: int | None = None


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


# -- library ----------------------------------------------------------------------------------------
class LibraryItemOut(BaseModel):
    id: str
    role: str
    name: str
    ref: str = Field(description="what a job input uses to refer to this item: library:<role>/<name>")
    description: str
    tags: list[str]
    meta: dict[str, Any]
    asset: AssetOut
    source_asset_id: str | None
    source_project_id: int | None
    created_at: str


class LibraryPublish(BaseModel):
    asset_id: str
    role: str
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    replace: bool = False


class LibraryPatch(BaseModel):
    description: str | None = None
    tags: list[str] | None = None
    meta: dict[str, Any] | None = None


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


class DeliveryProfileOut(Strict):
    name: str
    label: str
    width: int
    height: int
    fps: int
    target_sec: float | None
    min_episode_sec: float
    max_episode_sec: float


class Meta(BaseModel):
    version: str
    locale: str
    locales: list[str]
    kinds: list[LabeledValue]
    tiers: list[LabeledValue]
    statuses: list[LabeledValue]
    subject_types: list[LabeledValue]
    workflow_stages: list[LabeledValue]
    workflow_statuses: list[LabeledValue]
    models: list[ModelOut]
    defaults: dict[str, str]
    recipes: list[RecipeOut]
    delivery_profiles: list[DeliveryProfileOut]
    delivery_default: str
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




# -- story workflow -----------------------------------------------------------------------------------------
class StageProgressOut(Strict):
    stage: str
    episode: int | None = Field(default=None, description="the episode, in a run over several; null for series stages")
    status: Literal["pending", "running", "completed", "failed", "cancelled"] = "pending"
    percent: float | None = None            # None while a running stage has not reported its item count yet
    seconds: float | None = None


class RunProgressOut(Strict):
    """Everything a progress bar needs. ``percent`` is overall (0-100, never decreases); ``stages`` gives one bar per
    stage; ``eta_sec`` is None until enough has run to estimate."""

    stage: str | None = None
    episode: int | None = None
    stage_index: int = 0
    stage_count: int = 0
    done: int = 0
    total: int = 0
    message: str = ""
    percent: float = 0.0
    elapsed_sec: float = 0.0
    eta_sec: float | None = None
    stages: list[StageProgressOut] = Field(default_factory=list)


class WorkflowRunOut(Strict):
    id: str
    project_id: int
    stages: list[str]
    params: dict[str, Any]
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: RunProgressOut
    cancel_requested: bool
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class WorkflowStageOut(Strict):
    stage: str
    status: Literal["pending", "running", "completed", "failed", "stale"]
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0


class WorkflowOut(Strict):
    stages: list[WorkflowStageOut]
    active_run: WorkflowRunOut | None = None
    recent_runs: list[WorkflowRunOut] = Field(default_factory=list)
    episode: int = Field(default=1, description="the episode these stage states are for (series stages are shared)")


class WorkflowRunCreate(BaseModel):
    stages: list[str] | None = Field(default=None, description="omit to resume: every stage that is not completed")
    params: dict[str, Any] = Field(default_factory=dict)


class PlannedTaskOut(Strict):
    spec_id: str
    subject_type: str
    subject_id: str
    label: str
    kind: str
    model_ref: str
    phase: int
    state: str = Field(description="new | stale | done | blocked")
    reason: str = Field(description="why a blocked task cannot be made (no_model | no_voice)")
    est_seconds: int
    needs: list[str]


class PlanTotals(Strict):
    count: int = Field(description="tasks that would run (new + stale)")
    est_seconds: int
    by_state: dict[str, int]


class ProducePlanOut(Strict):
    tier: str
    tasks: list[PlannedTaskOut]
    totals: PlanTotals


class ProjectFromText(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    asset_id: str | None = Field(default=None, description="a text asset (story, script, transcript, uploaded text)")
    text: str = ""
    genre: str = ""
    content_language: str = ""
    start: bool = Field(default=True, description="queue the story stages (no media) right away")


class ProjectFromTextOut(Strict):
    project: ProjectOut
    run: WorkflowRunOut | None


class ReviewIssue(Strict):
    severity: str = ""
    where: str = ""
    problem: str = ""
    fix: str = ""


class ScriptReviewOut(Strict):
    score: float
    summary: str = ""
    issues: list[ReviewIssue] = Field(default_factory=list)


class AgentOut(Strict):
    name: str
    role: str
    description: str
    system: str
    prompt: str
    inputs: list[str]
    temperature: float
    max_tokens: int
    attempts: int
    model: str
    overridden: bool
    default_system: str
    default_prompt: str
    editable: list[str]


class AgentEdit(BaseModel):
    role: str | None = None
    system: str | None = None
    prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=16, le=65536)
    attempts: int | None = Field(default=None, ge=1, le=10)
    model: str | None = None


class AiCallRow(Strict):
    id: int
    label: str
    agent: str
    model: str
    tokens_in: int
    tokens_out: int
    duration_ms: int
    created_at: str
    prompt_chars: int
    response_chars: int


class AiCallDetail(Strict):
    id: int
    project_id: int | None
    label: str
    agent: str
    model: str
    system: str
    prompt: str
    response: str
    tokens_in: int
    tokens_out: int
    duration_ms: int
    created_at: str


class LaunchService(Strict):
    name: str
    url: str
    state: str   # running | starting | failed | stopped | disabled | probe_only
    label: str
    startable: bool
    bulk: bool
    required: bool
    detail: str
    hint: str
    pid: int | None
    elapsed_sec: float | None
    timeout_sec: float | None
    log: str


class LaunchIssue(Strict):
    name: str
    category: str
    state: str
    label: str
    detail: str
    required: bool


class LaunchOut(Strict):
    services: list[LaunchService]
    issues: list[LaunchIssue]
    ready: bool
    starting: bool
    startable: list[str]


class FlowOut(Strict):
    steps: list[dict[str, Any]]


class LockBody(BaseModel):
    locked: bool


class SpeakerBody(BaseModel):
    character: str = Field(min_length=1)


# -- workshop ---------------------------------------------------------------------------------------
class WorkshopField(Strict):
    name: str
    target: str
    type: str
    media: str
    required: bool


class WorkshopModel(Strict):
    ref: str
    provider: str
    version: str
    heavy: bool
    est_memory_gib: float
    license_tag: str
    commercial_ok: bool
    available: bool
    note: str


class WorkshopKind(Strict):
    kind: str
    model_ref: str
    default_model: str
    tier: str
    models: list[WorkshopModel]
    base_params: dict[str, Any]
    preset_params: dict[str, Any]
    schema_: dict[str, dict[str, Any]] = Field(alias="schema", serialization_alias="schema")
    inputs: list[WorkshopField]
    output: str

    model_config = ConfigDict(populate_by_name=True, json_schema_serialization_defaults_required=True)


class WorkshopModuleOut(Strict):
    id: str
    kinds: list[WorkshopKind]


class ParamsOut(Strict):
    base: dict[str, Any]
    preset: dict[str, Any]
    effective: dict[str, Any]
    schema_: dict[str, dict[str, Any]] = Field(alias="schema", serialization_alias="schema")

    model_config = ConfigDict(populate_by_name=True, json_schema_serialization_defaults_required=True)


class PresetBody(BaseModel):
    model_ref: str = ""
    tier: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class RunBody(BaseModel):
    kind: MediaKind
    inputs: dict[str, Any] = Field(default_factory=dict)
    model_ref: str = ""
    tier: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None
    count: int = Field(default=1, ge=1, le=8)


class PipelineStep(BaseModel):
    kind: MediaKind
    inputs: dict[str, Any] = Field(default_factory=dict)
    model_ref: str = ""
    tier: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None


class PipelineBody(BaseModel):
    steps: list[PipelineStep] = Field(min_length=1, max_length=12)


class PipelineOut(Strict):
    pipeline_id: str
    jobs: list[JobOut]


class WorkshopHistoryItem(Strict):
    job: JobOut
    assets: list[AssetOut]
