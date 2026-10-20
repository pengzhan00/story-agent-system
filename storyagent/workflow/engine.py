from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from storyagent.agents import AgentRunner
from storyagent.agents.registry import AgentSpecError
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.llm import LLMError, LLMRouter
from storyagent.media.ledger import Ledger
from storyagent.media.specs import SpecStore
from storyagent.projects import ProjectStore
from storyagent.story import StoryStore

from .progress import RunProgress
from .stages import STAGE_FUNCS, Ctx, Progress, StageError, WorkflowCancelled
from .state import WorkflowState

log = logging.getLogger(__name__)

PROGRESS_SECONDS = 1.0     # how often a running run writes its progress (the lease is renewed at the same time)


class WorkflowEngine:
    def __init__(
        self, settings: Settings, db: Database, router: LLMRouter | None = None, *, agents: AgentRunner | None = None,
        spec_store: SpecStore | None = None,
    ) -> None:
        self.settings, self.db = settings, db
        self.router = router or LLMRouter(settings, db)
        self.agents = agents or AgentRunner(settings, self.router)
        self.store, self.state, self.projects = StoryStore(db), WorkflowState(db), ProjectStore(db, settings)
        self.spec_store = spec_store or SpecStore(db, settings, Ledger(db, settings))

    def stages_to_run(self, project_id: int, requested: list[str] | None) -> list[str]:
        """``None`` = resume: every stage that is not completed (stale ones included), in pipeline order."""
        return self.state.pending_stages(project_id, requested)

    def run_stage(
        self, project_id: int, stage: str, params: dict[str, Any] | None = None, *, progress: Progress | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        ctx = Ctx(
            self.settings, self.db, self.store, self.state, self.agents, self.projects, self.projects.get(project_id),
            dict(params or {}), progress or (lambda *_: None), cancelled or (lambda: False), self.spec_store, stage,
        )
        self.state.set_stage(project_id, stage, "running")
        if progress:
            progress(stage, 0, 0, "")           # the stage is visibly running before its first item completes
        try:
            summary = STAGE_FUNCS[stage](ctx)
        except WorkflowCancelled:
            self.state.set_stage(project_id, stage, "failed", error_code="cancelled")
            raise
        except LLMError as exc:
            self.state.set_stage(project_id, stage, "failed", error_code=f"llm.{exc.code}", error_message=exc.message)
            raise
        except (StageError, AgentSpecError) as exc:
            code = exc.code if isinstance(exc, StageError) else "agent_spec"
            self.state.set_stage(project_id, stage, "failed", error_code=f"workflow.{code}", error_message=str(exc))
            raise
        except Exception as exc:
            log.exception("workflow stage %s failed", stage)
            self.state.set_stage(project_id, stage, "failed", error_code="internal", error_message=str(exc))
            raise
        self.state.set_stage(project_id, stage, "completed", summary=summary)
        return summary

    def run(
        self, project_id: int, stages: list[str] | None = None, params: dict[str, Any] | None = None, *,
        progress: Progress | None = None, cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for stage in self.stages_to_run(project_id, stages):
            out[stage] = self.run_stage(project_id, stage, params, progress=progress, cancelled=cancelled)
        return out

    # -- queue execution (called by the worker) -------------------------------------------------------
    def execute_run(self, run: dict[str, Any], owner: str, lease_seconds: float, beat_seconds: float) -> None:
        run_id, pid = run["id"], run["project_id"]
        tracker = RunProgress(self.stages_to_run(pid, run["stages"] or None))
        cancel, lost, stop = threading.Event(), threading.Event(), threading.Event()

        def beat() -> None:
            while not stop.wait(min(beat_seconds, PROGRESS_SECONDS)):
                owned, cancel_req = self.state.heartbeat(run_id, owner, lease_seconds, tracker.snapshot())
                if not owned:
                    lost.set()
                    return
                if cancel_req:
                    cancel.set()

        def progress(stage: str, done: int, total: int, msg: str) -> None:
            tracker.update(stage, done, total, msg)

        def end(status: str, **error: str) -> None:
            tracker.finish(status)
            self.state.finish(run_id, status, progress=tracker.snapshot(), **error)

        t = threading.Thread(target=beat, daemon=True, name=f"wf-hb-{run_id[:6]}")
        t.start()
        self.state.heartbeat(run_id, owner, lease_seconds, tracker.snapshot())   # the bar exists from the start
        try:
            self.run(pid, run["stages"] or None, run["params"], progress=progress,
                     cancelled=lambda: cancel.is_set() or lost.is_set())
            if not lost.is_set():
                end("completed")
        except WorkflowCancelled:
            if not lost.is_set():
                end("cancelled")
        except LLMError as exc:
            end("failed", error_code=f"llm.{exc.code}", error_message=exc.message)
        except StageError as exc:
            end("failed", error_code=f"workflow.{exc.code}", error_message=str(exc))
        except Exception as exc:  # noqa: BLE001 - already recorded on the stage
            end("failed", error_code="internal", error_message=str(exc))
        finally:
            stop.set()
            t.join(timeout=5)
