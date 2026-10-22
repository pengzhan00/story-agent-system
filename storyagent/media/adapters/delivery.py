"""compose.deliver: re-encode an assembled episode for a platform (``compose/delivery.py``) as a background job.

Inputs:  ``episode`` (number), ``platform`` (a ``platforms.toml`` name; "" = the default), ``languages`` (extra subtitle
         languages, translated by the translator agent), ``content_language``, ``confirm_restricted``.
Output:  ``delivery.zip`` (film, cover, teaser, subtitles, ``delivery.json``); the slot is ``<platform>_ep<NN>`` and the
         delivery record (warnings, measured result) is the asset's metadata.
The work runs in a thread of the worker process (ffmpeg children and, for translations, the language model); the
poll reads its progress. A cancel stops it between the steps (a running ffmpeg encode finishes first). A restart runs
it again.
"""
from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from storyagent.compose.delivery import DeliveryError, deliver
from storyagent.config import Settings
from storyagent.content import ContentError
from storyagent.db import Database
from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode
from storyagent.llm import LLMError
from storyagent.media.ledger import Ledger
from storyagent.media.probe import find_tool
from storyagent.util import new_id
from storyagent.workflow import WorkflowEngine

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

_CODES = {
    "episode_not_ready": ErrorCode.INVALID_INPUT, "unknown_platform": ErrorCode.INVALID_INPUT,
    "ffmpeg_missing": ErrorCode.MISSING_TOOL, "encode_failed": ErrorCode.TOOL_FAILED,
}


class _Stopped(Exception):
    pass


@dataclass
class _Run:
    thread: threading.Thread | None = None
    fraction: float = 0.0
    step: str = ""
    record: dict[str, Any] | None = None
    error: AdapterError | None = None
    stop: threading.Event = field(default_factory=threading.Event)


class DeliveryAdapter(AdapterBase):
    provider = "delivery"

    def __init__(self, poll_interval_sec: float = 1.0,
                 engine_factory: Callable[[Settings, Database], WorkflowEngine] | None = None) -> None:
        self.poll_interval_sec = poll_interval_sec
        self._engine = engine_factory or (lambda settings, db: WorkflowEngine(settings, db))
        self._runs: dict[str, _Run] = {}

    def capabilities(self) -> Capabilities:
        return Capabilities(kinds=(MediaKind.COMPOSE_DELIVER,), heavy=False, supports_cancel=True,
                            supports_reattach=False, poll_interval_sec=self.poll_interval_sec)

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        if find_tool(ctx.settings, "ffmpeg") is None:
            return [ValidationIssue(ErrorCode.MISSING_TOOL, "ffmpeg not found", {"tool": "ffmpeg"})]
        platform = str(job.inputs.get("platform") or ctx.settings.platforms.default)
        if platform not in ctx.settings.platforms.items:
            return [ValidationIssue(ErrorCode.INVALID_INPUT, f"unknown platform {platform}", {"detail": platform})]
        if job.project_id is None:
            return [ValidationIssue(ErrorCode.INVALID_INPUT, "a delivery belongs to a project", {"detail": "project"})]
        return []

    # -- the work -------------------------------------------------------------------------------------
    def _work(self, run: _Run, job: JobRecord, ctx: JobContext, out: Path) -> None:
        settings = ctx.settings
        db = Database(settings.db_path)
        assert job.project_id is not None
        try:
            ledger = Ledger(db, settings)
            engine = self._engine(settings, db)

            def progress(fraction: float, step: str) -> None:
                if run.stop.is_set():
                    raise _Stopped()
                run.fraction, run.step = fraction, step

            project = engine.projects.get(job.project_id)
            run.record = deliver(
                settings, ledger, job.project_id, int(job.inputs.get("episode") or 1),
                str(job.inputs.get("platform") or "") or None, out,
                languages=[str(x) for x in job.inputs.get("languages", [])],
                translate=lambda lines, lang: engine.translate_lines(job.project_id or 0, lines, lang),
                content_language=str(job.inputs.get("content_language") or project.content_language),
                confirm_restricted=bool(job.inputs.get("confirm_restricted")), progress=progress)
        except _Stopped:
            run.error = AdapterError(ErrorCode.CANCELLED)
        except DeliveryError as exc:
            run.error = AdapterError(_CODES.get(exc.code, ErrorCode.WORKFLOW_FAILED), f"{exc.code}: {exc.detail}",
                                     detail=exc.detail or exc.code)
        except ContentError as exc:
            run.error = AdapterError(ErrorCode.CONTENT_BLOCKED, f"{exc.code}", reason=exc.code)
        except LLMError as exc:
            run.error = AdapterError(ErrorCode.SERVICE_UNREACHABLE, f"{exc.code}: {exc.message}", retryable=True)
        except Exception as exc:  # noqa: BLE001 - a crash in the thread must end the job, not hang it
            run.error = AdapterError(ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}")
        finally:
            db.close()

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        ext = new_id()
        run = _Run()
        out = ctx.work_dir / "out"
        out.mkdir(parents=True, exist_ok=True)
        run.thread = threading.Thread(target=self._work, args=(run, job, ctx, out), daemon=True,
                                      name=f"delivery-{job.id[:8]}")
        self._runs[ext] = run
        run.thread.start()
        return ext

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        run = self._runs.get(external_id)
        if run is None:
            return Progress("failed", error=AdapterError(ErrorCode.WORKER_LOST, "the delivery thread is gone",
                                                         retryable=True))
        if run.thread is not None and run.thread.is_alive():
            return Progress("running", run.fraction, run.step)
        if run.error is not None:
            return Progress("failed", error=run.error)
        return Progress("succeeded", 1.0, "done")

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        run = self._runs.pop(external_id, None)
        if run is None or run.record is None:
            raise AdapterError(ErrorCode.OUTPUT_MISSING, "the delivery finished without a record")
        platform = run.record["platform"]["name"]
        archive = shutil.make_archive(str(ctx.work_dir / "delivery"), "zip", ctx.work_dir / "out" / platform)
        return [ArtifactSpec(Path(archive), metadata=run.record)]

    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None:
        run = self._runs.get(external_id)
        if run is not None:
            run.stop.set()
