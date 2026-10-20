from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(logs_dir: Path | None = None, level: int = logging.INFO) -> None:
    """Console + rotating file (``<logs_dir>/storyagent.log``). Safe to call repeatedly."""
    root = logging.getLogger("storyagent")
    if getattr(root, "_storyagent_configured", False):
        root.setLevel(level)
        return
    root.setLevel(level)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(console)
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            logs_dir / "storyagent.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(fh)
    root._storyagent_configured = True  # type: ignore[attr-defined]
