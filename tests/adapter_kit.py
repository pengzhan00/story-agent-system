"""Helpers shared by the adapter tests: real settings + ledger + worker around one adapter."""
from __future__ import annotations

import io
import math
import struct
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from storyagent.config import Settings, load_settings
from storyagent.db import Database, migrate
from storyagent.domain import MediaKind, QualityTier
from storyagent.media.contracts import Adapter, AssetRecord, JobRecord, MediaJob
from storyagent.media.ledger import Ledger
from storyagent.media.registry import AdapterRegistry
from storyagent.media.worker import Worker

FAST = """
[runtime]
poll_interval_seconds = 0.01
heartbeat_seconds = 0.05
lease_seconds = 5
caffeinate = false
memory_budget_gib = 128
"""


def wav_bytes(seconds: float = 0.3, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(rate * seconds))))
    return buf.getvalue()


def make_settings(tmp_path: Path, *, services: str = "", models: str = "") -> Settings:
    home = tmp_path / "home"
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "services.toml").write_text(FAST + services)
    (cfg / "models.toml").write_text(models)
    return load_settings({"STORY_AGENT_HOME": str(home), "STORY_AGENT_LOCALE": "en"})


@dataclass
class Rig:
    settings: Settings
    db: Database
    ledger: Ledger
    worker: Worker

    def submit(self, job: MediaJob) -> JobRecord:
        rec, _ = self.ledger.enqueue(job)
        return rec

    def run(self, job: MediaJob, timeout: float = 20) -> tuple[JobRecord, list[AssetRecord]]:
        rec = self.submit(job)
        assert self.worker.drain(timeout)
        return self.ledger.get_job(rec.id), self.ledger.list_assets(project_id=job.project_id)


def make_rig(settings: Settings, adapter: Adapter) -> Rig:
    db = Database(settings.db_path)
    migrate(db)
    db.conn.execute("INSERT INTO projects(slug, name) VALUES ('demo', 'Demo')")
    ledger = Ledger(db, settings)
    reg = AdapterRegistry()
    reg.register(adapter)
    return Rig(settings, db, ledger, Worker(settings, db, reg, ledger=ledger, probes=[]))


def job(kind: MediaKind, model: str, inputs: dict[str, Any] | None = None, **kw: Any) -> MediaJob:
    kw.setdefault("project_id", 1)
    kw.setdefault("tier", QualityTier.DRAFT)
    return MediaJob(kind, inputs or {}, model_ref=model, **kw)
