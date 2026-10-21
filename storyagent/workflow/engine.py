from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from storyagent.agents import AgentRunner
from storyagent.agents.registry import AgentSpecError
from storyagent.agents.runner import language_name
from storyagent.config import Settings
from storyagent.db import Database
from storyagent.llm import LLMError, LLMRouter
from storyagent.media.ledger import Ledger
from storyagent.media.specs import SpecStore
from storyagent.projects import ProjectStore
from storyagent.story import StoryStore

from .progress import RunProgress, stage_key
from .stages import STAGE_FUNCS, Ctx, Progress, StageError, WorkflowCancelled
from .state import SEASON_STAGES, SERIES_STAGES, WorkflowState, WorkflowStateError

log = logging.getLogger(__name__)

PROGRESS_SECONDS = 1.0     # how often a running run writes its progress (the lease is renewed at the same time)


class _EpisodeProgress:
    """Progress sink of one stage inside a run over several episodes: bars are keyed ``script@2``."""

    def __init__(self, inner: Progress, episode: int, several: bool) -> None:
        self.inner, self.episode, self.several = inner, episode, several

    def key(self, stage: str) -> str:
        return stage_key(stage, self.episode) if self.several and stage not in SERIES_STAGES + SEASON_STAGES else stage

    def __call__(self, stage: str, done: int, total: int, msg: str) -> None:
        self.inner(self.key(stage), done, total, msg)

    def set_weight(self, stage: str, units: float) -> None:
        hook = getattr(self.inner, "set_weight", None)
        if callable(hook):
            hook(self.key(stage), units)


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

    def stages_to_run(self, project_id: int, requested: list[str] | None, episode: int = 1) -> list[str]:
        """``None`` = resume: every stage that is not completed (stale ones included), in pipeline order."""
        return self.state.pending_stages(project_id, requested, episode)

    def review_script(self, project_id: int, episode: int = 1) -> dict[str, Any]:
        """Ask the script editor agent to critique the current script (on demand; nothing is stored or changed)."""
        project = self.projects.get(project_id)
        script = self.store.get_script(project_id, episode)
        if not script or not script["acts"]:
            raise WorkflowStateError("the script stage has not produced a script yet")
        chars = [{"name": c["name"], "role": c["role"], "personality": c["personality"]}
                 for c in self.store.list_characters(project_id)]
        outline = {"title": script["title"], "synopsis": script["synopsis"], "acts": script["acts"]}
        data = self.agents.run("reviewer.script", {
            "language": language_name(project.content_language), "script": json.dumps(outline, ensure_ascii=False),
            "characters": json.dumps(chars, ensure_ascii=False)}, project_id=project_id).data
        return dict(data)

    def run_stage(
        self, project_id: int, stage: str, params: dict[str, Any] | None = None, *, progress: Progress | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        episode = int((params or {}).get("episode") or 1)
        ctx = Ctx(
            self.settings, self.db, self.store, self.state, self.agents, self.projects, self.projects.get(project_id),
            dict(params or {}), progress or (lambda *_: None), cancelled or (lambda: False), self.spec_store, stage,
        )
        self.state.set_stage(project_id, stage, "running", episode=episode)
        if progress:
            progress(stage, 0, 0, "")           # the stage is visibly running before its first item completes
        try:
            summary = STAGE_FUNCS[stage](ctx)
        except WorkflowCancelled:
            self.state.set_stage(project_id, stage, "failed", error_code="cancelled", episode=episode)
            raise
        except LLMError as exc:
            self.state.set_stage(project_id, stage, "failed", error_code=f"llm.{exc.code}", error_message=exc.message,
                                 episode=episode)
            raise
        except (StageError, AgentSpecError) as exc:
            code = exc.code if isinstance(exc, StageError) else "agent_spec"
            self.state.set_stage(project_id, stage, "failed", error_code=f"workflow.{code}", error_message=str(exc),
                                 episode=episode)
            raise
        except Exception as exc:
            log.exception("workflow stage %s failed", stage)
            self.state.set_stage(project_id, stage, "failed", error_code="internal", error_message=str(exc),
                                 episode=episode)
            raise
        self.state.set_stage(project_id, stage, "completed", summary=summary, episode=episode)
        return summary

    def plan(self, project_id: int, stages: list[str] | None, params: dict[str, Any] | None) -> tuple[
            list[tuple[int, str]], bool]:
        """(episode, stage) pairs a run does, and whether it covers several episodes."""
        return (self.state.plan(project_id, stages, params),
                len(self.state.episodes_for(project_id, params)) > 1)

    def run(
        self, project_id: int, stages: list[str] | None = None, params: dict[str, Any] | None = None, *,
        progress: Progress | None = None, cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, dict[str, Any]]:
        params = dict(params or {})
        plan, several = self.plan(project_id, stages, params)
        first = self.state.episodes_for(project_id, params)[0]
        out: dict[str, dict[str, Any]] = {}
        for ep, stage in plan:
            sink = _EpisodeProgress(progress, ep, several) if progress else None
            out[stage_key(stage, ep) if several and ep else stage] = self.run_stage(
                project_id, stage, {**params, "episode": ep or first}, progress=sink, cancelled=cancelled)
        return out

    # -- queue execution (called by the worker) -------------------------------------------------------
    def execute_run(self, run: dict[str, Any], owner: str, lease_seconds: float, beat_seconds: float) -> None:
        run_id, pid = run["id"], run["project_id"]
        plan, several = self.plan(pid, run["stages"] or None, run["params"])
        tracker = RunProgress([stage_key(s, ep) if several and ep else s for ep, s in plan])
        cancel, lost, stop = threading.Event(), threading.Event(), threading.Event()

        def beat() -> None:
            while not stop.wait(min(beat_seconds, PROGRESS_SECONDS)):
                owned, cancel_req = self.state.heartbeat(run_id, owner, lease_seconds, tracker.snapshot())
                if not owned:
                    lost.set()
                    return
                if cancel_req:
                    cancel.set()

        class Reporter:
            """Callable progress sink that also lets a stage reshape its weight (``Ctx.hint_weight``)."""

            @staticmethod
            def __call__(stage: str, done: int, total: int, msg: str) -> None:
                tracker.update(stage, done, total, msg)

            @staticmethod
            def set_weight(stage: str, units: float) -> None:
                tracker.set_weight(stage, units)

        progress = Reporter()

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
