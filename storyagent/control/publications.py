"""Manual publication ledger. Exporting an asset never marks it published."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from storyagent.db import Database
from storyagent.media.ledger import Ledger
from storyagent.util import new_id, now_iso

from .catalog import ControlError, digest

TRANSITIONS = {
    "draft": {"ready", "scheduled", "cancelled", "published"},
    "ready": {"draft", "scheduled", "published", "cancelled"},
    "scheduled": {"ready", "published", "cancelled"},
    "published": {"withdrawn", "deleted"},
    "withdrawn": {"published", "deleted"},
    "deleted": set(),
    "cancelled": {"draft"},
}


class Publications:
    def __init__(self, db: Database, ledger: Ledger):
        self.db, self.ledger = db, ledger

    def targets(self) -> list[dict[str, Any]]:
        return [
            dict(r) for r in self.db.conn.execute("SELECT * FROM publication_targets ORDER BY platform,account,channel")
        ]

    def target(self, platform: str, account: str, channel: str, name: str) -> dict[str, Any]:
        if not platform.strip() or not account.strip():
            raise ControlError("Platform and account are required")
        with self.db.transaction() as c:
            c.execute(
                "INSERT OR IGNORE INTO publication_targets(id,platform,account,channel,name) VALUES (?,?,?,?,?)",
                (new_id(), platform.strip(), account.strip(), channel.strip(), name.strip() or account.strip()),
            )
            row = c.execute(
                "SELECT * FROM publication_targets WHERE platform=? AND account=? AND channel=?",
                (platform.strip(), account.strip(), channel.strip()),
            ).fetchone()
        assert row
        return dict(row)

    def categories(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.conn.execute("SELECT * FROM publication_categories ORDER BY name")]

    def category(self, name: str) -> dict[str, Any]:
        if not name.strip():
            raise ControlError("Category name is required")
        ident = new_id()
        self.db.conn.execute("INSERT INTO publication_categories(id,name) VALUES (?,?)", (ident, name.strip()))
        return {"id": ident, "name": name.strip(), "enabled": 1}

    def checked_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not 1 <= len(items) <= 100:
            raise ControlError("A publication requires 1..100 assets")
        result = []
        for pos, item in enumerate(items):
            asset = self.ledger.get_asset(item["asset_id"])
            if asset.trashed_at or not (self.ledger.settings.home / asset.path).is_file():
                raise ControlError("Publication asset is missing or trashed")
            result.append(
                {
                    "asset_id": asset.id,
                    "role": item.get("role") or "main",
                    "position": pos,
                    "sha256": asset.sha256,
                    "kind": asset.kind,
                    "duration_sec": asset.duration_sec,
                    "width": asset.width,
                    "height": asset.height,
                }
            )
        return result

    def get(self, ident: str) -> dict[str, Any]:
        row = self.db.conn.execute("SELECT * FROM publications WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise ControlError("Unknown publication", "not_found", 404)
        result = dict(row)
        revisions = []
        for rev in self.db.conn.execute(
            "SELECT * FROM publication_revisions WHERE publication_id=? ORDER BY revision", (ident,)
        ):
            revisions.append(
                {
                    **dict(rev),
                    "items": json.loads(rev["items"]),
                    "tags": json.loads(rev["tags"]),
                    "classification": json.loads(rev["classification"]),
                }
            )
        result["revisions"] = revisions
        result["content"] = revisions[-1]
        result["events"] = [
            dict(r)
            for r in self.db.conn.execute(
                "SELECT * FROM publication_events WHERE publication_id=? ORDER BY id", (ident,)
            )
        ]
        return result

    def save(
        self, data: dict[str, Any], actor: str, ident: str | None = None, revision: int | None = None
    ) -> dict[str, Any]:
        items = self.checked_items(data["items"])
        if not self.db.conn.execute(
            "SELECT 1 FROM publication_targets WHERE id=? AND enabled=1", (data["target_id"],)
        ).fetchone():
            raise ControlError("Unknown or disabled target")
        if data.get("url") and not str(data["url"]).startswith(("https://", "http://")):
            raise ControlError("Publication URL must use HTTP or HTTPS")
        now = now_iso()
        request_key = data.get("request_key")
        with self.db.transaction() as c:
            if ident is None and request_key:
                duplicate = c.execute("SELECT id FROM publications WHERE request_key=?", (request_key,)).fetchone()
                if duplicate:
                    previous = self.get(duplicate[0])
                    submitted = {
                        "target_id": data["target_id"],
                        "items": items,
                        "title": data["title"],
                        "body": data.get("body", ""),
                        "tags": data.get("tags", []),
                    }
                    old = previous["revisions"][0]
                    expected = {
                        "target_id": previous["target_id"],
                        "items": old["items"],
                        "title": old["title"],
                        "body": old["body"],
                        "tags": old["tags"],
                    }
                    if digest(submitted) != digest(expected):
                        raise ControlError("Idempotency key reused", "control_conflict", 409)
                    return previous
            if ident:
                old = self.get(ident)
                if revision != old["revision"] or data["target_id"] != old["target_id"]:
                    raise ControlError("Stale revision or changed target", "control_conflict", 409)
                version = old["revision"] + 1
                c.execute(
                    "UPDATE publications SET revision=?,category_id=?,updated_at=?,actor=? WHERE id=?",
                    (version, data.get("category_id"), now, actor, ident),
                )
            else:
                ident, version = new_id(), 1
                c.execute(
                    "INSERT INTO publications(id,target_id,project_id,category_id,actor,request_key,created_at,"
                    "updated_at,scheduled_at,timezone) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        ident,
                        data["target_id"],
                        data.get("project_id"),
                        data.get("category_id"),
                        actor,
                        request_key,
                        now,
                        now,
                        data.get("scheduled_at"),
                        data.get("timezone", "UTC"),
                    ),
                )
            c.execute(
                "INSERT INTO publication_revisions VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    version,
                    data["title"],
                    data.get("body", ""),
                    json.dumps(data.get("tags", [])),
                    json.dumps(items),
                    actor,
                    now,
                    json.dumps(data.get("classification", {})),
                ),
            )
            c.executemany(
                "INSERT INTO publication_items VALUES (?,?,?,?,?,?)",
                [(ident, version, i["position"], i["asset_id"], i["role"], i["sha256"]) for i in items],
            )
            self.event(ident, version, "created" if version == 1 else "revised", actor)
        return self.get(ident)

    def event(self, ident: str, revision: int, event: str, actor: str, **detail: Any) -> None:
        self.db.conn.execute(
            "INSERT INTO publication_events(publication_id,revision,event,detail,actor,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (ident, revision, event, json.dumps(detail), actor, now_iso()),
        )

    def transition(
        self,
        ident: str,
        status: str,
        revision: int,
        actor: str,
        *,
        external_id: str = "",
        url: str = "",
        actual_published_at: str = "",
        evidence: str = "",
        scheduled_at: str = "",
    ) -> dict[str, Any]:
        with self.db.transaction() as c:
            old = self.get(ident)
            if revision != old["revision"]:
                raise ControlError("Stale publication revision", "control_conflict", 409)
            if status not in TRANSITIONS.get(old["status"], set()):
                raise ControlError("Invalid publication transition", "control_conflict", 409)
            if url and not url.startswith(("https://", "http://")):
                raise ControlError("Publication URL must use HTTP or HTTPS")
            actual = old["actual_published_at"]
            scheduled = old["scheduled_at"]
            if status == "scheduled":
                try:
                    when = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        raise ValueError("Timezone required")
                except ValueError as exc:
                    raise ControlError("Scheduled publication requires a time with timezone") from exc
                scheduled = when.isoformat()
            if status == "published":
                if not evidence.strip():
                    raise ControlError("Manual confirmation evidence is required")
                try:
                    parsed = datetime.fromisoformat(actual_published_at.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError("Timezone required")
                except ValueError as exc:
                    raise ControlError("Actual publication time requires timezone") from exc
                actual = parsed.isoformat()
            try:
                c.execute(
                    "UPDATE publications SET status=?,revision=revision+1,external_id=?,url=?,"
                    "actual_published_at=?,scheduled_at=?,confirmation=?,updated_at=?,actor=? WHERE id=?",
                    (
                        status,
                        external_id or old["external_id"],
                        url or old["url"],
                        actual,
                        scheduled,
                        "manual_unverified" if status == "published" else old["confirmation"],
                        now_iso(),
                        actor,
                        ident,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ControlError("Post already recorded for this target", "control_conflict", 409) from exc
            self.event(ident, revision + 1, status, actor, evidence=evidence, external_id=external_id, url=url)
        return self.get(ident)

    def listing(
        self,
        *,
        target_id: str = "",
        status: str = "",
        category_id: str = "",
        project_id: int | None = None,
        asset_id: str = "",
        platform: str = "",
        account: str = "",
        content_type: str = "",
        purpose: str = "",
        series: str = "",
        episode: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        where, args = [], []
        for column, value in (
            ("target_id", target_id),
            ("status", status),
            ("category_id", category_id),
            ("project_id", project_id),
        ):
            if value not in (None, ""):
                where.append(f"p.{column}=?")
                args.append(value)
        if asset_id:
            where.append("EXISTS(SELECT 1 FROM publication_items i WHERE i.publication_id=p.id AND i.asset_id=?)")
            args.append(asset_id)
        for column, value in (("platform", platform), ("account", account)):
            if value:
                where.append(f"EXISTS(SELECT 1 FROM publication_targets t WHERE t.id=p.target_id AND t.{column}=?)")
                args.append(value)
        for column, value in (
            ("content_type", content_type),
            ("purpose", purpose),
            ("series", series),
            ("episode", episode),
        ):
            if value:
                where.append(
                    "EXISTS(SELECT 1 FROM publication_revisions r WHERE r.publication_id=p.id AND "
                    "r.revision=(SELECT MAX(r2.revision) FROM publication_revisions r2 "
                    "WHERE r2.publication_id=p.id) "
                    f"AND json_extract(r.classification,'$.{column}')=?)"
                )
                args.append(value)
        clause = " WHERE " + " AND ".join(where) if where else ""
        count = self.db.conn.execute("SELECT COUNT(*) FROM publications p" + clause, args).fetchone()[0]
        rows = self.db.conn.execute(
            "SELECT p.id FROM publications p" + clause + " ORDER BY p.created_at DESC,p.id LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
        return {"total": count, "items": [self.get(r[0]) for r in rows]}
