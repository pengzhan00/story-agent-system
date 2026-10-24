"""Request/response models. They are the OpenAPI contract the web client is generated from."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from storyagent.domain import JobStatus, MediaKind, QualityTier

from .visibility import hidden


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
    kind: str
    pipeline: str
    brief: dict[str, Any]
    created_at: str
    updated_at: str
    rating: str = "general"


class ProjectCard(Strict):
    """One tile of the project list: the project, how far it is, and the finished episode if there is one."""

    id: int
    name: str
    description: str
    genre: str
    status: str
    kind: str
    pipeline: str
    rating: str
    locked: bool
    stages_done: int
    stages_total: int
    running: bool
    episode_asset_id: str | None
    episode_seconds: float | None
    episode_number: int | None
    updated_at: str


class BriefAnswers(BaseModel):
    """The plan made up front. Anything left out takes the default of the kind of work."""

    seasons: int | None = Field(default=None, ge=1)
    episodes_per_season: int | None = Field(default=None, ge=1)
    episode_sec: float | None = Field(default=None, gt=0, description="target length of one episode in seconds")
    audience: str = ""
    platform: str = ""
    logline: str = ""


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    genre: str = ""
    description: str = ""
    content_language: str = ""
    delivery_profile: str = Field(default="", description="the picture frame (vertical / landscape ...), decided now")
    kind: str = Field(default="", description="kind of work (drama_series, advert ...): recommends the pipeline plan")
    pipeline: str = Field(default="", description="the pipeline plan: one complete set of models; default = the kind's")
    brief: BriefAnswers | None = None
    rating: str = Field(default="", description="content rating (content.toml); empty = the default, decided now")


class EstimateRequest(BaseModel):
    kind: str = ""
    pipeline: str = ""
    brief: BriefAnswers | None = None


class EstimateTaskOut(Strict):
    kind: str
    model_ref: str
    count: int
    each_sec: int
    known: bool
    total_sec: int


class EstimateOut(Strict):
    brief: dict[str, Any]
    pipeline: str
    tier: str
    episodes: int
    shots: int
    tasks: list[EstimateTaskOut]
    total_sec: int
    hours: float
    video_budget_hours: float | None
    over_budget: bool


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    genre: str | None = None
    content_language: str | None = None
    delivery_profile: str | None = None
    aspect_ratio: str | None = None
    kind: str | None = None
    pipeline: str | None = Field(default=None, description="change the plan: the whole set of models changes with it")
    rating: str | None = Field(default=None, description="content rating; only while the project has no files yet")


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
    video_share: float | None = Field(
        default=None, ge=0, le=1, description="share of an episode's shots that get a video clip (0-1); null = default")
    video_budget_hours: float | None = Field(
        default=None, ge=0, description="machine hours for video clips in the series (0 = no limit); null = default")
    edit: dict[str, Any] | None = Field(
        default=None,
        description="cards and fades of the finished episode: title_card / recap_card / next_card (bool), "
                    "title_sec / recap_sec / next_sec / scene_fade_sec (seconds); null = the delivery defaults")
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
    error_params: dict[str, str]
    cancel_requested: bool
    created_at: str
    started_at: str | None
    finished_at: str | None
    actual_seconds: float | None
    retried_by: str | None = None       # the newest retry of this job (its id) ...
    retried_status: str | None = None   # ... and that retry's status
    retries: int = 0                    # how many times it was retried
    locked: bool = False

    @model_validator(mode="after")
    def _mask(self) -> JobOut:
        if hidden(None, self.project_id):
            self.locked, self.inputs = True, {}
        return self
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


class PriorityRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    move: Literal["first", "last"] | None = None
    priority: int | None = Field(default=None, ge=-1000, le=1000)


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
    rating: str = "general"
    locked: bool = False

    @model_validator(mode="after")
    def _mask(self) -> AssetOut:
        if hidden(self.rating, self.project_id):
            self.locked, self.path, self.sha256, self.metadata, self.provenance = True, "", "", {}, {}
        return self


class StaleAsset(BaseModel):
    asset: AssetOut
    latest_spec_version: int


# -- wiki -------------------------------------------------------------------------------------------
class WikiBrief(Strict):
    slug: str
    title: str
    source: str
    path: str
    rating: str
    tags: list[str]
    summary: str
    snippet: str = Field(description="the text around the first search hit; empty when no query was given")
    updated: str | None


class WikiCount(Strict):
    name: str
    count: int


class WikiList(Strict):
    pages: list[WikiBrief]
    total: int = Field(description="pages this caller may see, before the query and the filters")
    hidden: int = Field(description="restricted pages this caller may not see right now (locked or too young)")
    sources: list[WikiCount]
    tags: list[WikiCount]
    synced_at: str | None
    configured: bool = Field(description="false until a source other than the built-in docs is listed in wiki.toml")


class WikiPageOut(Strict):
    page: WikiBrief
    body: str
    meta: dict[str, Any]
    backlinks: list[WikiBrief]
    links: list[WikiBrief]


class WikiSyncOut(Strict):
    added: int
    updated: int
    unchanged: int
    removed: int
    sources: dict[str, int]
    warnings: list[dict[str, str]]


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
    masked: bool = False

    @model_validator(mode="after")
    def _mask(self) -> SpecOut:
        if hidden(None, self.project_id):
            self.masked, self.prompt, self.negative_prompt, self.text_payload, self.note = True, "", "", "", ""
        return self


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


class RangeOut(Strict):
    min: float
    default: float
    max: float


class WorkKindOut(Strict):
    name: str
    label: str
    note: str
    pipeline: str
    seasons: bool
    episodes: RangeOut
    episode_sec: RangeOut
    ask: list[str]


class PlanOut(Strict):
    name: str
    label: str
    note: str
    rating: str            # the content rating a project of this plan starts with ("" = the project's own choice)
    delivery_profile: str
    width: int
    height: int
    tier: str
    models: dict[str, str]
    requires: list[str]


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
    work_kinds: list[WorkKindOut]
    default_work_kind: str
    pipelines: list[PlanOut]
    default_pipeline: str
    delivery_profiles: list[DeliveryProfileOut]
    delivery_default: str
    compliance_enabled: bool
    llm_unload_policy: str
    auth_roles: list[str]
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
    state: str = Field(description="new | stale | done | blocked | skip")
    reason: str = Field(description="why a blocked task cannot be made (no_model | no_voice)")
    est_seconds: int
    needs: list[str]


class PlanTotals(Strict):
    count: int = Field(description="tasks that would run (new + stale)")
    est_seconds: int
    by_state: dict[str, int]


class VideoSummary(Strict):
    shots: int = Field(description="shots that have a clip task")
    chosen: int = Field(description="of those, the ones the tiering policy (or the creator) gives a clip")
    est_seconds: int = Field(description="estimated machine time of the clips still to make")
    budget_seconds: int | None = Field(description="series-wide clip budget, null = none")


class ProducePlanOut(Strict):
    tier: str
    tasks: list[PlannedTaskOut]
    totals: PlanTotals
    video: VideoSummary


class SeriesEpisodeOut(Strict):
    number: int = Field(description="series-wide episode number (the key everything uses)")
    number_in_season: int
    title: str
    summary: str
    hook: str
    cliffhanger: str
    exists: bool = Field(description="false = planned by the season but not started")
    stages: dict[str, str] = Field(description="stage -> pending | running | completed | failed | stale")
    shots: int
    keyframes: int = Field(description="shots that have a keyframe (outdated ones included; see outdated)")
    videos: int
    outdated: int = Field(description="shots whose keyframe or video was made from an older spec")
    film_asset_id: str | None
    film_seconds: float | None


class SeriesSeasonOut(Strict):
    number: int
    title: str
    summary: str
    planned_episodes: int
    episodes: list[SeriesEpisodeOut]
    filmed: int


class SeriesTotals(Strict):
    episodes: int
    planned: int
    scripted: int
    filmed: int
    shots: int
    keyframes: int
    videos: int


class SeriesBoardOut(Strict):
    seasons: list[SeriesSeasonOut]
    totals: SeriesTotals


class ProjectFromText(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    asset_id: str | None = Field(default=None, description="a text asset (story, script, transcript, uploaded text)")
    text: str = ""
    genre: str = ""
    content_language: str = ""
    delivery_profile: str = Field(default="", description="the picture frame (vertical / landscape ...), decided now")
    kind: str = Field(default="", description="kind of work (drama_series, advert ...)")
    pipeline: str = Field(default="", description="the pipeline plan: one complete set of models")
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
    stoppable: bool        # the operator allowed ending it from here (`stop = "port"` in services.toml)
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


class MemoryOut(Strict):
    total_gib: float | None
    free_gib: float | None            # what a new job could still get (free + inactive + purgeable)
    swap_total_gib: float | None
    swap_used_gib: float | None
    swap_percent: int | None          # 80 or more: free memory before a heavy job


class StopBody(BaseModel):
    force: bool = False


class LaunchOut(Strict):
    memory: MemoryOut
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
    in_plan: bool = Field(description="false = outside the chosen pipeline plan: a trial only, not for a project")


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


# -- team access ------------------------------------------------------------------------------------------
class UserOut(Strict):
    id: int
    username: str
    display_name: str
    role: str
    active: bool
    last_login_at: str | None
    created_at: str
    age: int | None                                                  # full years today; None until an age is approved
    age_status: Literal["none", "pending", "approved", "rejected"]
    age_claim: str | None                                            # the birth date the person gave (waits for review)
    birth_date: str | None                                           # the approved one
    age_note: str
    age_reviewed_by: str | None
    age_reviewed_at: str | None


class AuthModeOut(Strict):
    mode: Literal["off", "token"]
    first_user: bool       # token mode and no account exists yet: the first admin must be created with the CLI
    allow_registration: bool    # the sign-in page offers "register"
    locales: list[str]     # public, so the sign-in page can offer the language choice before anyone is signed in


class MeOut(Strict):
    mode: Literal["off", "token"]
    user: UserOut | None
    can_write: bool
    can_create_project: bool
    is_admin: bool


class LoginBody(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=80)


class LoginOut(Strict):
    token: str
    expires_at: str
    user: UserOut


class PasswordBody(Strict):
    old_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class UserCreateBody(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    role: str
    display_name: str = Field(default="", max_length=80)
    birth_date: str = Field(default="", max_length=10, description="YYYY-MM-DD; given by an admin it counts at once")


class RegisterBody(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    birth_date: str = Field(min_length=1, max_length=10, description="YYYY-MM-DD; a claim an admin has to approve")
    display_name: str = Field(default="", max_length=80)
    label: str = Field(default="", max_length=80)


class SetupBody(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)
    birth_date: str = Field(min_length=1, max_length=10)
    display_name: str = Field(default="", max_length=80)


class AgeClaimBody(Strict):
    birth_date: str = Field(min_length=1, max_length=10)


class AgeReviewBody(Strict):
    approve: bool
    birth_date: str = Field(default="", max_length=10, description="the corrected birth date; empty = the claimed one")
    note: str = Field(default="", max_length=300)


class UserPatchBody(BaseModel):
    role: str | None = None
    active: bool | None = None
    display_name: str | None = Field(default=None, max_length=80)
    password: str | None = Field(default=None, max_length=200)


class AuditOut(Strict):
    id: int
    at: str
    user_id: int | None
    actor: str
    action: str
    path: str
    project_id: int | None
    status: int | None
    detail: dict[str, Any]
    client: str


# -- review loop ------------------------------------------------------------------------------------------
NoteStatus = Literal["open", "redoing", "resolved", "dismissed"]


class NoteOut(Strict):
    id: int
    project_id: int
    subject_type: str
    subject_id: str
    slot: str
    shot_id: int | None
    asset_id: str | None
    body: str
    status: NoteStatus
    author: str
    resolution: str
    resolved_by: str
    resolved_at: str | None
    redo_spec_version: int | None
    redo_job_ids: list[str]
    created_at: str
    updated_at: str


class NoteCreate(BaseModel):
    subject_type: str = Field(min_length=1, max_length=80)
    subject_id: str = Field(min_length=1, max_length=80)
    slot: str = Field(default="main", max_length=40)
    shot_id: int | None = None
    asset_id: str | None = Field(default=None, max_length=64)
    body: str = Field(min_length=1, max_length=4000)


class NoteStatusBody(BaseModel):
    status: Literal["open", "resolved", "dismissed"]
    resolution: str = Field(default="", max_length=1000)


class NoteRedoBody(BaseModel):
    """What the reviewer wants different (only what is sent changes the spec; nothing = same spec, new seeds)."""

    prompt: str | None = None
    negative_prompt: str | None = None
    text_payload: str | None = None
    model_ref: str | None = None
    seed: int | None = None
    params_patch: dict[str, Any] | None = None
    n_candidates: int = Field(default=1, ge=1, le=8)
    tier: QualityTier = QualityTier.FINAL


class NoteRedoOut(Strict):
    note: NoteOut
    jobs: list[JobOut]


class NoteCompareOut(Strict):
    note: NoteOut
    before: AssetOut | None
    after: list[AssetOut]
    jobs: list[JobOut]


class ReviewShotOut(Strict):
    shot_id: int
    episode: int
    act: int
    scene: int
    shot: int
    location: str
    assets: list[AssetOut]
    open_notes: int
    redoing_notes: int
    resolved_notes: int


# -- asset packs -----------------------------------------------------------------------------------
class PackExportOut(Strict):
    name: str
    bytes: int
    scope: str
    counts: dict[str, int]
    warnings: list[dict[str, Any]]


class PackReportOut(Strict):
    dry_run: bool
    project_id: int | None
    created_project: bool
    story: dict[str, dict[str, int]]
    specs: dict[str, int]
    assets: dict[str, int]
    warnings: list[dict[str, Any]]
    unassigned: list[str]


# -- shot grammar ----------------------------------------------------------------------------------
class GrammarViolationOut(Strict):
    rule: str
    severity: str
    shot_id: int
    episode: int
    act: int
    scene: int
    shot: int
    params: dict[str, Any]
    fixable: bool
    fix: dict[str, Any] | None


class GrammarReportOut(Strict):
    shots: int
    unclassified: int
    counts: dict[str, int]
    violations: list[GrammarViolationOut]


class GrammarFixBody(BaseModel):
    episode: int | None = None
    rules: list[str] | None = None
    shot_ids: list[int] | None = None


class GrammarFixOut(Strict):
    fixed: list[dict[str, Any]]
    left: list[dict[str, Any]]
    report: GrammarReportOut


# -- edit interchange ------------------------------------------------------------------------------
class EditShotOut(Strict):
    shot_id: int
    status: str
    duration: float
    new_duration: float


class EditAddedOut(Strict):
    name: str
    media: str
    duration: float


class EditDiffOut(Strict):
    original_sec: float
    edited_sec: float
    shots: list[EditShotOut]
    added: list[EditAddedOut]
    changed: int


# -- delivery platforms ----------------------------------------------------------------------------
class PlatformOut(Strict):
    name: str
    label_key: str
    note_key: str
    width: int
    height: int
    fps: float
    reframe: str
    loudnorm_i: float
    loudnorm_tp: float
    loudnorm_lra: float
    crf: int
    max_mb: float
    max_duration_sec: float
    audio_bitrate: str
    subtitles: str
    cover_sec: float
    trailer_sec: float
    default: bool


class DeliveryBody(Strict):
    platform: str = ""
    languages: list[str] = []
    confirm_restricted: bool = False


class DeliveryJobOut(Strict):
    job_id: str
    created: bool


class DeliveryOut(Strict):
    asset: AssetOut
    platform: str
    episode: int
    warnings: list[str]
    subtitles: list[str]
    result: dict[str, Any]


# -- content ratings ---------------------------------------------------------------------------------
class RatingOut(Strict):
    name: str
    label_key: str
    restricted: bool
    mask_ui: bool
    vault: bool
    allow_export: bool
    min_age: int           # the approved age a viewer needs to see this rating at all (0 = anyone)
    allowed: bool          # the caller's age reaches it


class ContentStatusOut(Strict):
    unlocked: bool
    expires_at: str | None
    ttl_sec: int
    needs_password: bool
    vault_ready: bool
    encryption: str
    default_rating: str
    min_adult_age: int
    viewer_age: int | None   # the caller's approved age; None when accounts are on and no age is approved
    age_status: Literal["off", "none", "pending", "approved", "rejected"]   # "off" = accounts are off
    ratings: list[RatingOut]


class UnlockBody(Strict):
    confirm_adult: bool = False
    password: str = ""


# -- language models (which model each role uses) and the AI buttons beside prompts ------------------------
class TextEdit(BaseModel):
    text: str = Field(max_length=2_000_000)


class LlmBackendOut(Strict):
    name: str
    kind: str
    url: str
    state: str = Field(description="ok | unreachable | disabled")
    models: list[str]
    error: str


class LlmRoleOut(Strict):
    name: str
    group: str
    backend: str
    model: str
    source: str = Field(description="config (llm.toml) | global (chosen on the screen) | project (chosen there)")
    config_backend: str
    config_model: str


class LlmOut(Strict):
    backends: list[LlmBackendOut]
    roles: list[LlmRoleOut]
    project_id: int | None


class LlmRoleSet(BaseModel):
    backend: str
    model: str
    project_id: int | None = Field(default=None, description="null = every project")


class LlmTest(BaseModel):
    backend: str
    model: str


class LlmTestOut(Strict):
    ok: bool
    seconds: float
    reply: str


class PromptAssistIn(BaseModel):
    action: Literal["suggest", "translate"]
    text: str = Field(min_length=1, max_length=20000)
    purpose: str = Field(default="image", description="what it is for: image | video | music | voice | text")
    style: str = Field(default="", description="the prompt style the model expects (models.toml prompt_profile)")
    instruction: str = Field(default="", description="suggest: what the author wants changed")
    source: str = Field(default="", description="translate: language code of the text; empty = detect")
    target: str = Field(default="en", description="translate: into which language; suggest: language of the answer")
    project_id: int | None = None


class PromptAssistOut(Strict):
    text: str
    action: str
    backend: str
    model: str


class PipelineCandidate(Strict):
    ref: str
    family: str
    rating: str
    paired: bool           # false = another family than the picture / video model in use: not a recommended match
    remote: bool
    commercial_ok: bool
    license_tag: str


class PipelineRow(Strict):
    kind: str
    default: str           # what the plan recommends
    chosen: str            # what the project runs with now
    overridden: bool       # changed by hand
    auto: bool             # changed by pairing with a hand-picked model
    candidates: list[PipelineCandidate]


class PipelineNote(Strict):
    code: Literal["auto_paired", "dropped", "family_mismatch", "rated_model"]
    kind: str
    model: str


class PipelineView(Strict):
    plan: str
    rows: list[PipelineRow]
    notes: list[PipelineNote]
    overridden: bool


class PipelineChoices(Strict):
    models: dict[str, str] = Field(
        description="kind -> model ref; replaces all hand-picked choices (empty = the plan's defaults)")


# -- tools: connect outside software ----------------------------------------------------------------
class ToolImportRequest(BaseModel):
    workflow: dict[str, Any]          # the parsed JSON file (ComfyUI "Save (API Format)")
    kind: str


class ToolBinding(Strict):
    name: str
    targets: list[str]                # "<node id>.<input>"
    classes: list[str]
    value: Any = None


class ToolWarning(Strict):
    code: str
    message: str


class ToolImportPreview(Strict):
    bindings: list[ToolBinding]
    warnings: list[ToolWarning]
    output_node: str
    output_type: list[str]
    defaults: dict[str, Any]


class ToolSaveRequest(ToolImportRequest):
    name: str
    ref: str = ""
    family: str = ""
    heavy: bool = False
    timeout_sec: int = Field(default=1800, ge=10, le=86400)
    force: bool = False


class ReloadResult(Strict):
    reloaded: bool
    error: str
    models: int


class ToolSaved(Strict):
    ref: str
    workflow: str
    models_file: str
    reloaded: bool                    # the server uses it now; the worker swaps it in when it is between jobs
    error: str


class ToolTemplate(Strict):
    name: str
    models: str
    services: str


class ToolSnippet(BaseModel):
    models: str
    services: str = ""
    force: bool = False


class ToolIssue(Strict):
    level: str                        # "error" | "warning"
    code: str
    message: str


class ToolChecked(Strict):
    ref: str
    kind: str
    provider: str
    ok: bool
    issues: list[ToolIssue]


class ToolConnectorSaved(Strict):
    ref: str
    reloaded: bool
    error: str
    issues: list[ToolIssue]           # warnings that remain after saving


class ToolModel(Strict):
    ref: str
    kind: str
    provider: str
    family: str
    workflow: str                     # template name ("" = none)
    mine: bool                        # the template is in the operator's own folder



# -- setup guide -------------------------------------------------------------------------------------
class SetupCheckOut(Strict):
    label: str
    state: str                  # ok | missing | unknown
    detail: str


class SetupStepOut(Strict):
    id: str
    component: str
    kind: str                   # command | download | manual
    shell: str                  # one copy-pasteable line
    note_key: str
    source: str
    file: str
    size_gib: float | None
    needs_service: str
    runnable: bool
    done: bool
    error: str


class SetupComponentOut(Strict):
    name: str
    capability: str
    need: str                   # required | recommended | optional
    state: str                  # ok | partial | missing | unverified
    label_key: str
    note_key: str
    size_gib: float
    measured: bool
    license: str
    url: str
    checks: list[SetupCheckOut]
    after: list[str]
    blocked_by: list[str]
    steps: list[SetupStepOut]


class SetupCapabilityOut(Strict):
    name: str
    need: str
    state: str
    label_key: str
    note_key: str
    components: list[str]


class SetupMachineOut(Strict):
    key: str
    state: str                  # ok | warn | fail
    params: dict[str, str]


class SetupProfileOut(Strict):
    name: str
    label_key: str
    note_key: str
    required: list[str]
    recommended: list[str]


class SetupOut(Strict):
    profile: str
    root: str
    default_root: str
    profiles: list[SetupProfileOut]
    capabilities_all: list[str]
    machine: list[SetupMachineOut]
    capabilities: list[SetupCapabilityOut]
    components: list[SetupComponentOut]
    downloads: list[SetupStepOut]
    missing_gib: float
    download_gib: float
    ready: bool
    health_required: list[str]
    next_step: str


class SetupConfigFileOut(Strict):
    name: str
    path: str
    exists: bool
    changed: bool
    changes: list[str]
    text: str


class SetupConfigOut(Strict):
    files: list[SetupConfigFileOut]


class SetupConfigBody(BaseModel):
    profile: str = "full"
    root: str = ""
    extra: list[str] = []
    llm_model: str = ""
    write: bool = False


class SetupWritten(Strict):
    path: str
    backup: str


class SetupConfigWritten(Strict):
    files: list[SetupConfigFileOut]
    written: list[SetupWritten]


class SetupVerifyEntry(Strict):
    name: str
    category: str
    state: str
    label: str
    detail: str
    ok: bool
    required: bool


class SetupVerifyOut(Strict):
    entries: list[SetupVerifyEntry]
    ok: bool
