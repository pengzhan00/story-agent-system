"""Has the operator's configuration changed on disk? A cheap fingerprint of the config folder, so the long-running
server and worker can load a changed configuration (a model added, a template saved, a plugin dropped in) without a
restart: they compare it every few seconds and rebuild their settings when it differs."""
from __future__ import annotations

import hashlib
from pathlib import Path

WATCHED = (".toml", ".json", ".py")


def fingerprint(config_dir: Path) -> str:
    """Hash of (path, size, mtime) of every configuration file under ``config_dir`` (``""`` when it does not exist)."""
    if not config_dir.is_dir():
        return ""
    digest = hashlib.sha256()
    for path in sorted(config_dir.rglob("*")):
        if path.suffix not in WATCHED or not path.is_file():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(config_dir)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return digest.hexdigest()
