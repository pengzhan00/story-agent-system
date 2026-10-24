from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    """UTC ISO-8601 with millisecond precision, lexicographically sortable."""
    t = datetime.now(UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def iso_at(epoch_seconds: float) -> str:
    t = datetime.fromtimestamp(epoch_seconds, UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def new_id() -> str:
    return uuid.uuid4().hex


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    return json.loads(text)


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def home_file(home: Path, rel: str) -> Path | None:
    """``home/rel`` when the ledger path stays inside the data home *by name* (no ``..``, not absolute); symlinks are
    followed on purpose - the vault may be a link to an encrypted volume. ``None`` otherwise."""
    root = os.path.normpath(home)
    full = os.path.normpath(home / rel)
    return Path(full) if full != root and full.startswith(root + os.sep) else None


_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")


def has_cjk(text: str) -> bool:
    """Chinese, Japanese or Korean characters in ``text`` (what an English-only model cannot read)."""
    return bool(_CJK.search(text or ""))
