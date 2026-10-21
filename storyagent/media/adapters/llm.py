"""text.story / text.script: a language model writes the text; the artifact is a markdown file.

Runs in-process (a thread of the worker): the call goes through the ``LLMRouter`` (roles of ``llm.toml``), so the
backend and model are the ones configured there unless ``params.model`` pins one. The prompt is an agent spec
(``params.agent``, editable like every other prompt).

Inputs  story: ``idea``, ``language``, ``genre``, ``tone``, ``length``, ``notes``
        script: ``story`` (a text file, e.g. ``job:<id>`` of a story) or ``story_text``, ``language``, ``format``,
        ``scenes``, ``notes``
Params  ``agent`` (prompt name), ``role`` (llm.toml role), ``model`` (pin), ``temperature``, ``max_tokens``
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from storyagent.agents.registry import AgentSpecError
from storyagent.agents.runner import AgentRunner, language_name
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.llm import LLMError, LLMRouter

from ..contracts import (
    AdapterBase,
    AdapterError,
    ArtifactSpec,
    Capabilities,
    JobContext,
    JobRecord,
    Progress,
    ValidationIssue,
)

SCHEMA: dict[str, dict[str, Any]] = {
    "agent": {"type": "str"}, "role": {"type": "str"}, "model": {"type": "str"},
    "temperature": {"type": "float", "min": 0.0, "max": 2.0}, "max_tokens": {"type": "int", "min": 256, "max": 32000},
}
_AGENTS = {MediaKind.TEXT_STORY: "workshop.story", MediaKind.TEXT_SCRIPT: "workshop.script"}
_DEFAULTS: dict[MediaKind, dict[str, str]] = {
    MediaKind.TEXT_STORY: {"genre": "drama", "tone": "tense, emotional", "length": "600", "notes": ""},
    MediaKind.TEXT_SCRIPT: {"format": "screenplay: scene headings, action lines, dialogue", "scenes": "8",
                            "notes": ""},
}
_LLM_ERRORS = {"heavy_busy": ErrorCode.SERVICE_UNREACHABLE, "unreachable": ErrorCode.SERVICE_UNREACHABLE,
               "no_backend": ErrorCode.MISSING_MODEL, "missing_model": ErrorCode.MISSING_MODEL}


def read_text_input(settings: Settings, raw: object) -> str:
    """A text input is either the text itself or the path of a text file (what ``job:``/``asset:`` resolve to)."""
    value = str(raw or "").strip()
    if value and "\n" not in value and len(value) < 1024:
        path = Path(value).expanduser()
        path = path if path.is_absolute() else settings.home / path
        try:
            if path.is_file():
                return path.read_text("utf-8").strip()
        except OSError:
            pass
    return value


class _Run:
    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.text = ""
        self.error: AdapterError | None = None
        self.cancelled = False


class LlmAdapter(AdapterBase):
    provider = "llm"

    def __init__(self, router: LLMRouter | None = None, poll_interval_sec: float = 1.0) -> None:
        self._router = router
        self.poll_interval_sec = poll_interval_sec
        self._runs: dict[str, _Run] = {}
        self._lock = threading.Lock()

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=tuple(_AGENTS), heavy=False, supports_cancel=True, supports_reattach=False,
            param_schema=SCHEMA, poll_interval_sec=self.poll_interval_sec,
        )

    def _router_for(self, settings: Settings) -> LLMRouter:
        if self._router is None:
            self._router = LLMRouter(settings, Database(settings.db_path))
        return self._router

    def _prompt(self, job: JobRecord, ctx: JobContext) -> tuple[str, str, dict[str, Any]]:
        runner = AgentRunner(ctx.settings, self._router_for(ctx.settings))
        name = str(job.params.get("agent") or _AGENTS[job.kind])
        spec = runner.spec(name)
        values: dict[str, Any] = {**_DEFAULTS[job.kind]}
        values.update({k: v for k, v in job.inputs.items() if isinstance(v, str) and v.strip()})
        values["language"] = values.get("language") or language_name(ctx.settings.locale)
        if job.kind is MediaKind.TEXT_SCRIPT:
            values["story"] = read_text_input(ctx.settings, job.inputs.get("story") or job.inputs.get("story_text"))
        else:
            values["idea"] = read_text_input(ctx.settings, job.inputs.get("idea"))
        for key in spec.inputs:
            values.setdefault(key, "")
        system, prompt = runner.render(name, values)
        opts: dict[str, Any] = {"temperature": float(job.params.get("temperature", spec.temperature)),
                                "max_tokens": int(job.params.get("max_tokens", spec.max_tokens))}
        model = str(job.params.get("model") or spec.model or "")
        if model:
            opts["model"] = model
        return system, prompt, {"role": str(job.params.get("role") or spec.role), "label": name, **opts}

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        key = "idea" if job.kind is MediaKind.TEXT_STORY else "story"
        have = (job.inputs.get(key) or job.inputs.get("story_text")) if key == "story" else job.inputs.get(key)
        if not read_text_input(ctx.settings, have):
            return [ValidationIssue(ErrorCode.INVALID_INPUT, f"{key} is empty", {"detail": key})]
        try:
            self._prompt(job, ctx)
        except AgentSpecError as exc:
            return [ValidationIssue(ErrorCode.INVALID_PARAMS, str(exc), {"detail": str(exc)})]
        return []

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        system, prompt, opts = self._prompt(job, ctx)
        router, run = self._router_for(ctx.settings), _Run()
        role = opts.pop("role")

        def work() -> None:
            try:
                run.text = router.text(role, prompt, system=system, project_id=job.project_id, **opts).strip()
            except LLMError as exc:
                code = _LLM_ERRORS.get(exc.code, ErrorCode.WORKFLOW_FAILED)
                run.error = AdapterError(code, f"{exc.code}: {exc.message}",
                                         retryable=code is ErrorCode.SERVICE_UNREACHABLE)
            except Exception as exc:  # noqa: BLE001 - reported as the job's failure
                run.error = AdapterError(ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}")

        run.thread = threading.Thread(target=work, name=f"llm-{job.id[:8]}", daemon=True)
        external = f"llm-{job.id}"
        with self._lock:
            self._runs[external] = run
        run.thread.start()
        return external

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        with self._lock:
            run = self._runs.get(external_id)
        if run is None:
            return Progress("failed", error=AdapterError(ErrorCode.WORKER_LOST, "the writing run was lost"))
        if run.thread is not None and run.thread.is_alive():
            return Progress("running", 0.5, "")
        if run.cancelled:
            return Progress("failed", error=AdapterError(ErrorCode.CANCELLED))
        if run.error is not None:
            return Progress("failed", error=run.error)
        if not run.text:
            return Progress("failed", error=AdapterError(ErrorCode.OUTPUT_MISSING, "the model returned no text"))
        return Progress("succeeded", 1.0)

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        with self._lock:
            run = self._runs.pop(external_id, None)
        if run is None or not run.text:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "no text to collect")
        out = ctx.work_dir / "text.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(run.text + "\n", "utf-8")
        return [ArtifactSpec(out, metadata={"chars": len(run.text), "preview": run.text[:400]})]

    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None:
        with self._lock:
            run = self._runs.get(external_id)
        if run is not None:
            run.cancelled = True  # the model call itself cannot be interrupted; its text is dropped
