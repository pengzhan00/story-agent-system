"""Run progress that a person can read: an overall percentage, a bar per stage, what is being done right now, how long
it has taken and how long it is expected to take.

Stage weights are the share of a typical run each stage takes (LLM-heavy stages weigh more); they only shape the
overall bar. Inside a stage the bar is exact (``done`` of ``total`` items), and a stage that has not reported yet
shows as running with an indeterminate bar - never as a stalled number.
"""
from __future__ import annotations

import time
from typing import Any

# Rough shares of one episode run (measured live: analyze 49 s, cast 68 s, script 766 s on gemma4:31b). Tunable.
STAGE_WEIGHTS: dict[str, float] = {
    "analyze": 4, "cast": 5, "script": 50, "scenes": 8, "music": 8, "shotplan": 1, "specs": 24,
}
ETA_MIN_PERCENT = 10.0          # below this an extrapolated ETA is noise (LLM stages are lumpy)


class RunProgress:
    """Thread-safe enough for one writer (the stage thread) and one reader (the heartbeat thread): ``snapshot`` builds
    a fresh dict from plain values."""

    def __init__(self, stages: list[str], weights: dict[str, float] | None = None, clock: Any = time.monotonic) -> None:
        self.order = list(stages)
        self.weights = {s: float((weights or STAGE_WEIGHTS).get(s, 1.0)) for s in self.order}
        self._clock = clock
        self._t0 = clock()
        self.current: str | None = None
        self.done = self.total = 0
        self.message = ""
        self.finished: set[str] = set()
        self.ended: tuple[str, str] | None = None       # (stage, 'failed' | 'cancelled')
        self.stage_started: dict[str, float] = {}
        self.stage_seconds: dict[str, float] = {}

    # -- writer side ------------------------------------------------------------------------------
    def update(self, stage: str, done: int, total: int, message: str = "") -> None:
        if stage != self.current:
            if self.current is not None:
                self._close(self.current)
            self.current = stage
            self.stage_started.setdefault(stage, self._clock())
        self.done, self.total, self.message = done, max(total, 0), message

    def _close(self, stage: str) -> None:
        self.finished.add(stage)
        self.stage_seconds[stage] = self._clock() - self.stage_started.get(stage, self._clock())

    def finish(self, outcome: str = "completed") -> None:
        """``completed`` | ``failed`` | ``cancelled``: the stage that was running is marked accordingly."""
        if outcome == "completed":
            if self.current is not None:
                self._close(self.current)
            self.finished.update(self.order)
        elif self.current is not None:
            self.ended = (self.current, outcome)

    # -- reader side ------------------------------------------------------------------------------
    def _stage_fraction(self, stage: str) -> float | None:
        if stage in self.finished:
            return 1.0
        if stage == self.current and self.total > 0:
            return min(self.done / self.total, 1.0)
        return None if stage == self.current else 0.0

    def percent(self) -> float:
        total = sum(self.weights.values()) or 1.0
        got = sum(w * (self._stage_fraction(s) or 0.0) for s, w in self.weights.items())
        return round(100.0 * got / total, 1)

    def snapshot(self) -> dict[str, Any]:
        elapsed = self._clock() - self._t0
        pct = self.percent()
        eta = round(elapsed * (100.0 - pct) / pct, 1) if pct >= ETA_MIN_PERCENT and pct < 100 else None
        stages = []
        for s in self.order:
            frac = self._stage_fraction(s)
            if self.ended and s == self.ended[0]:
                status = self.ended[1]
            elif s in self.finished:
                status = "completed"
            elif s == self.current:
                status = "running"
            else:
                status = "pending"
            stages.append({"stage": s, "status": status, "percent": None if frac is None else round(100 * frac, 1),
                           "seconds": round(self.stage_seconds[s], 1) if s in self.stage_seconds else None})
        idx = self.order.index(self.current) + 1 if self.current in self.order else 0
        return {"stage": self.current, "stage_index": idx, "stage_count": len(self.order), "done": self.done,
                "total": self.total, "message": self.message, "percent": pct, "elapsed_sec": round(elapsed, 1),
                "eta_sec": eta, "stages": stages}
