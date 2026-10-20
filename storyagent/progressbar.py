"""One-line text progress for the terminal (CLI); the API serves the same data as numbers for the web UI."""
from __future__ import annotations

from typing import Any

from storyagent.i18n import t

WIDTH = 24


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m{s:02d}s" if m < 60 else f"{m // 60}h{m % 60:02d}m"


def bar(percent: float, width: int = WIDTH) -> str:
    filled = int(round(width * max(0.0, min(percent, 100.0)) / 100))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def render_run(run: dict[str, Any]) -> str:
    """``[#####-------] 42.0%  Step 3 of 7 Script 4 of 8 - message  | Elapsed 1m20s, About 2m left``"""
    pr = run.get("progress") or {}
    pct = float(pr.get("percent", 0.0))
    if run["status"] == "queued":
        return f"{bar(0)}   0.0%  {t('workflow.progress.queued')}"
    parts = [f"{bar(pct)} {pct:5.1f}%"]
    if pr.get("stage"):
        parts.append(t("workflow.progress.stage_of", index=pr.get("stage_index", 0), count=pr.get("stage_count", 0)))
        parts.append(t(f"workflow.stage.{pr['stage']}"))
        if pr.get("total"):
            parts.append(t("workflow.progress.items", done=pr["done"], total=pr["total"]))
        if pr.get("message"):
            parts.append("- " + str(pr["message"]))
    else:
        parts.append(t("workflow.progress.working"))
    tail = [t("workflow.progress.elapsed", time=duration(pr.get("elapsed_sec")))]
    if run["status"] == "running":
        eta = pr.get("eta_sec")
        tail.append(t("workflow.progress.eta", time=duration(eta)) if eta is not None
                    else t("workflow.progress.eta_unknown"))
    return "  ".join(parts) + "  | " + ", ".join(tail)


def render_stages(run: dict[str, Any]) -> list[str]:
    """One bar per stage, for ``workflow status``."""
    lines = []
    for st in (run.get("progress") or {}).get("stages", []):
        pct = st["percent"]
        shown = bar(pct if pct is not None else 0) + (f" {pct:5.1f}%" if pct is not None else "   ...  ")
        sec = f"  {duration(st['seconds'])}" if st.get("seconds") is not None else ""
        lines.append(f"  {t('workflow.stage.' + st['stage']):<14} {shown}  {t('workflow.status.' + st['status'])}{sec}")
    return lines
