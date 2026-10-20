"""Deterministic adapter for tests and demos: writes small files, can fail, hang or lose its state.

Behaviour is driven by the job's ``inputs`` so tests need no special wiring:
  fake_steps      polls before success (default 1)
  fake_fail       ErrorCode value to raise from poll
  fake_validate   ErrorCode value to return from validate
  fake_no_output  claim success but produce no file
  fake_empty      produce a 0-byte file
  fake_multi      also produce a second artifact (slot 'thumb')
  fake_ext        output extension (default 'bin')
  fake_hang       never finish (for timeout/cancel tests)
Class attribute ``reattachable`` toggles reattach support. ``calls`` records what happened.
"""
from __future__ import annotations

import threading
from collections import defaultdict

from storyagent.domain import MediaKind
from storyagent.domain.errors import ErrorCode

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


class FakeAdapter(AdapterBase):
    provider = "fake"

    def __init__(self, *, heavy: bool = False, reattachable: bool = True, schema: dict | None = None) -> None:
        self.heavy = heavy
        self.reattachable = reattachable
        self.schema = schema or {}
        self.calls: list[tuple[str, str]] = []
        self._polls: dict[str, int] = defaultdict(int)
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def capabilities(self) -> Capabilities:
        return Capabilities(
            kinds=tuple(MediaKind), heavy=self.heavy, supports_reattach=self.reattachable,
            param_schema=self.schema, poll_interval_sec=0.01,
        )

    def _rec(self, what: str, key: str) -> None:
        with self._lock:
            self.calls.append((what, key))

    def validate(self, job: JobRecord, ctx: JobContext) -> list[ValidationIssue]:
        code = job.inputs.get("fake_validate")
        return [ValidationIssue(ErrorCode(code), "fake validation", {"detail": "fake"})] if code else []

    def submit(self, job: JobRecord, ctx: JobContext) -> str:
        ext = f"fake-{job.id[:8]}"
        self._rec("submit", job.id)
        return ext

    def reattach(self, external_id: str, job: JobRecord, ctx: JobContext) -> bool:
        self._rec("reattach", job.id)
        return self.reattachable

    def poll(self, external_id: str, job: JobRecord, ctx: JobContext) -> Progress:
        if job.inputs.get("fake_hang"):
            return Progress("running", 0.1, "hanging")
        with self._lock:
            self._polls[external_id] += 1
            n = self._polls[external_id]
        if external_id in self._cancelled:
            return Progress("failed", error=AdapterError(ErrorCode.CANCELLED))
        if job.inputs.get("fake_fail"):
            code = ErrorCode(job.inputs["fake_fail"])
            return Progress("failed", error=AdapterError(code, "fake failure", retryable=False))
        steps = int(job.inputs.get("fake_steps", 1))
        if n <= steps:
            return Progress("running", n / (steps + 1), f"step {n}/{steps}")
        return Progress("succeeded", 1.0)

    def fetch(self, external_id: str, job: JobRecord, ctx: JobContext) -> list[ArtifactSpec]:
        self._rec("fetch", job.id)
        if job.inputs.get("fake_no_output"):
            return []
        ext = job.inputs.get("fake_ext", "bin")
        out = ctx.work_dir / f"out.{ext}"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"" if job.inputs.get("fake_empty") else f"fake:{job.id}:{job.seed}".encode())
        arts = [ArtifactSpec(out, metadata={"fake": True})]
        if job.inputs.get("fake_multi"):
            thumb = ctx.work_dir / "thumb.bin"
            thumb.write_bytes(b"thumb")
            arts.append(ArtifactSpec(thumb, slot="thumb"))
        return arts

    def cancel(self, external_id: str, job: JobRecord, ctx: JobContext) -> None:
        self._rec("cancel", job.id)
        self._cancelled.add(external_id)
