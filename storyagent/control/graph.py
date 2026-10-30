"""Durable fan-in/fan-out orchestration. Only the existing media worker runs adapters.

Each node/job association is committed atomically with enqueue. Scheduler ticks are short
transactions; review and dependency waits never occupy a thread or hold a GPU reservation.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from storyagent.config import Settings
from storyagent.db import Database
from storyagent.domain import JobStatus, MediaKind, QualityTier
from storyagent.media.contracts import MediaJob
from storyagent.media.ledger import Ledger, LedgerError
from storyagent.media.params import resolve_params
from storyagent.util import new_id, now_iso
from storyagent.workshop import build_inputs, kind_spec, output_media

from .catalog import ControlError, descriptor, digest
from .sequence import advance as advance_sequence
from .sequence import sequence_options

TERMINAL = {"succeeded", "failed", "blocked", "cancelled", "skipped"}
NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def references(value: Any) -> list[dict[str, str]]:
    if isinstance(value, dict):
        if "$node" in value:
            if set(value) != {"$node", "port"} or not all(isinstance(v, str) for v in value.values()):
                raise ControlError("A node reference requires exactly $node and port")
            return [value]
        return [r for v in value.values() for r in references(v)]
    if isinstance(value, list):
        return [r for v in value for r in references(v)]
    return []


def asset_ids(value: Any) -> set[str]:
    if isinstance(value, str) and value.startswith("asset:"):
        return {value[6:]}
    if isinstance(value, dict):
        return {a for v in value.values() for a in asset_ids(v)}
    if isinstance(value, list):
        return {a for v in value for a in asset_ids(v)}
    return set()


def content_identity(ledger: Ledger, value: Any) -> Any:
    if isinstance(value, str) and value.startswith("asset:"):
        asset = ledger.get_asset(value[6:])
        return {"sha256": asset.sha256, "rating": asset.rating}
    if isinstance(value, dict):
        return {k: content_identity(ledger, v) for k, v in value.items()}
    if isinstance(value, list):
        return [content_identity(ledger, v) for v in value]
    return value


class GraphStore:
    def __init__(self, settings: Settings, db: Database, ledger: Ledger | None = None):
        self.settings, self.db = settings, db
        self.ledger = ledger or Ledger(db, settings)

    def compile(self, definition: dict[str, Any]) -> dict[str, Any]:
        raw = definition.get("nodes", [])
        if not isinstance(raw, list) or not 1 <= len(raw) <= 200:
            raise ControlError("A plan requires 1..200 nodes")
        project_id = definition.get("project_id")
        if (
            project_id is not None
            and not self.db.conn.execute(
                "SELECT 1 FROM projects WHERE id=? AND status != 'trashed'", (project_id,)
            ).fetchone()
        ):
            raise ControlError("Unknown project")
        cfg = self.ledger.settings_for(project_id)
        project_settings: dict[str, Any] = {}
        if project_id:
            project_settings = json.loads(
                self.db.conn.execute("SELECT settings FROM projects WHERE id=?", (project_id,)).fetchone()[0] or "{}"
            )
        nodes: dict[str, Any] = {}
        for item in raw:
            nid = str(item.get("id", ""))
            if not NODE_ID.fullmatch(nid) or nid.startswith("__") or nid in nodes:
                raise ControlError("Node IDs must be unique, bounded identifiers")
            kind = str(item.get("kind", ""))
            cap = descriptor(cfg, kind)
            ref = str(item.get("model_ref") or cfg.default_model(kind).ref)
            if ref not in cfg.models or cfg.models[ref].kind != kind:
                raise ControlError(f"Model {ref} does not provide {kind}")
            tier = QualityTier(item.get("tier", "final"))
            values = item.get("inputs") or {}
            if not isinstance(values, dict) or set(values) - {f["name"] for f in cap["inputs"]}:
                raise ControlError(f"Unknown input on {nid}")
            for field in cap["inputs"]:
                if field["required"] and values.get(field["name"]) in (None, "", []):
                    raise ControlError(f"{nid} requires {field['name']}")
                value = values.get(field["name"])
                if field["type"] in {"media", "medialist"}:
                    for v in value if isinstance(value, list) else [value]:
                        if isinstance(v, str) and v.startswith("asset:"):
                            a = self.ledger.get_asset(v[6:])
                            media = a.kind.split(".", 1)[1] if a.kind.startswith("upload.") else output_media(a.kind)
                            if media == "model":
                                media = "lora"
                            if media != field["media"]:
                                raise ControlError(f"Wrong media type on {nid}.{field['name']}")
            mode = (item.get("params") or {}).get("mode")
            if mode == "t2v" and values.get("image"):
                raise ControlError("t2v must not carry an image")
            if mode == "i2v" and not values.get("image"):
                raise ControlError("i2v requires an image")
            params = resolve_params(
                cfg,
                ref,
                tier,
                job_params=item.get("params") or {},
                frame=self.ledger.frame_of(project_id),
                project_settings=project_settings,
            )
            if ref == "control-stitch":
                try:
                    lengths = [float(s["seconds"]) for s in values.get("shots", [])]
                    duration = float(params["duration"])
                except (KeyError, ValueError, TypeError, OverflowError) as exc:
                    raise ControlError("Invalid assembly interval") from exc
                if (
                    not lengths
                    or not math.isfinite(duration)
                    or duration <= 0
                    or any(not math.isfinite(v) or v <= 0 for v in lengths)
                    or abs(sum(lengths) - duration) > 0.01
                ):
                    raise ControlError("Assembly shots must cover the locked audio interval")
            binding = next(b for b in cap["bindings"] if b["id"] == ref)
            nodes[nid] = {
                "id": nid,
                "kind": kind,
                "model_ref": ref,
                "tier": tier.value,
                "inputs": values,
                "params": params,
                "seed": item.get("seed"),
                "binding_digest": binding["digest"],
                "outputs": binding["outputs"],
                "review": bool(item.get("review", False)),
                "skip": bool(item.get("skip", False)),
                "depends_on": list(item.get("depends_on") or []),
            }
        for nid, node in nodes.items():
            for field in descriptor(cfg, node["kind"])["inputs"]:
                for r in references(node["inputs"].get(field["name"])):
                    parent = nodes.get(r["$node"])
                    if parent is None or r["port"] not in parent["outputs"]:
                        raise ControlError(f"Unknown output reference on {nid}")
                    if field["media"] != parent["outputs"][r["port"]]:
                        raise ControlError(f"Incompatible output on {nid}.{field['name']}")
            for r in references(node["params"]):
                parent = nodes.get(r["$node"])
                if parent is None or r["port"] not in parent["outputs"]:
                    raise ControlError(f"Unknown parameter output on {nid}")
            deps = (
                set(node["depends_on"])
                | {r["$node"] for r in references(node["inputs"])}
                | {r["$node"] for r in references(node["params"])}
            )
            if deps - nodes.keys():
                raise ControlError(f"Unknown dependency on {nid}")
            node["depends_on"] = sorted(deps)
        ordered: list[str] = []
        while len(ordered) < len(nodes):
            ready = [n for n in nodes if n not in ordered and set(nodes[n]["depends_on"]) <= set(ordered)]
            if not ready:
                raise ControlError("Dependency cycle")
            ordered.extend(ready)
        result: dict[str, Any] = {
            "schema_version": 1,
            "name": str(definition.get("name") or ""),
            "project_id": project_id,
            "nodes": [nodes[n] for n in ordered],
        }
        for node in list(result["nodes"]):
            sequence = sequence_options(node)
            if sequence is not None:
                node["sequence"] = sequence
                if not node["params"].get("audio_stem") and node["params"].get("audio_stem_auto", True):
                    audio = node["inputs"]["audio"]
                    stem_id = "__stem_" + digest(audio)[:16]
                    if not any(n["id"] == stem_id for n in result["nodes"]):
                        stem_ref = cfg.default_model("audio.separate").ref
                        stem_cap = descriptor(cfg, "audio.separate")
                        binding = next(b for b in stem_cap["bindings"] if b["id"] == stem_ref)
                        stem_node = {
                            "id": stem_id,
                            "kind": "audio.separate",
                            "model_ref": stem_ref,
                            "tier": node["tier"],
                            "inputs": {"audio": audio},
                            "params": resolve_params(cfg, stem_ref, node["tier"]),
                            "seed": None,
                            "binding_digest": binding["digest"],
                            "outputs": binding["outputs"],
                            "review": False,
                            "skip": False,
                            "depends_on": [r["$node"] for r in references(audio)],
                        }
                        result["nodes"].append(stem_node)
                    node["depends_on"].append(stem_id)
                    node["params"]["audio_stem"] = {"$node": stem_id, "port": "main"}
                    node["params"]["audio_stem_auto"] = False
        return result

    def submit(
        self, definition: dict[str, Any], actor: str, request_key: str | None = None, expected_digest: str | None = None
    ) -> dict[str, Any]:
        plan = self.compile(definition)
        fingerprint = digest(plan)
        if expected_digest and fingerprint != expected_digest:
            raise ControlError("The reviewed plan changed; preview again", "control_conflict", 409)
        with self.db.transaction() as c:
            if request_key:
                row = c.execute("SELECT id,digest FROM control_runs WHERE request_key=?", (request_key,)).fetchone()
                if row:
                    if row["digest"] != fingerprint:
                        raise ControlError("Idempotency key reused for a different plan", "control_conflict", 409)
                    return self.get(row["id"])
            rid, now = new_id(), now_iso()
            c.execute(
                "INSERT INTO control_runs(id,project_id,name,plan,digest,request_key,actor,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (rid, plan["project_id"], plan["name"], json.dumps(plan), fingerprint, request_key, actor, now, now),
            )
            c.executemany(
                "INSERT INTO control_nodes(run_id,node_id) VALUES (?,?)", [(rid, n["id"]) for n in plan["nodes"]]
            )
            self.event(rid, None, "submitted", actor)
        return self.get(rid)

    def event(self, rid: str, nid: str | None, event: str, actor: str = "worker", **data: Any) -> None:
        self.db.conn.execute(
            "INSERT INTO control_events(run_id,node_id,event,data,actor,created_at) VALUES (?,?,?,?,?,?)",
            (rid, nid, event, json.dumps(data), actor, now_iso()),
        )

    def get(self, rid: str) -> dict[str, Any]:
        row = self.db.conn.execute("SELECT * FROM control_runs WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise ControlError("Unknown run", "not_found", 404)
        result = dict(row)
        result["plan"] = json.loads(result["plan"])
        result["nodes"] = []
        for r in self.db.conn.execute("SELECT * FROM control_nodes WHERE run_id=? ORDER BY rowid", (rid,)):
            result["nodes"].append({**dict(r), "outputs": json.loads(r["outputs"])})
        result["events"] = [
            dict(r)
            for r in self.db.conn.execute(
                "SELECT * FROM control_events WHERE run_id=? ORDER BY id DESC LIMIT 100", (rid,)
            )
        ]
        return result

    def action(
        self,
        rid: str,
        action: str,
        actor: str,
        nid: str | None = None,
        revision: int | None = None,
        external_id: str = "",
        evidence: str = "",
    ) -> dict[str, Any]:
        with self.db.transaction() as c:
            run = self.get(rid)
            if action == "cancel":
                c.execute("UPDATE control_runs SET cancel_requested=1,revision=revision+1 WHERE id=?", (rid,))
            elif nid:
                node = next((n for n in run["nodes"] if n["node_id"] == nid), None)
                if node is None:
                    raise ControlError("Unknown node", "not_found", 404)
                if revision != node["revision"]:
                    raise ControlError("Stale node revision", "control_conflict", 409)
                if action == "approve" and node["status"] == "waiting_review":
                    c.execute(
                        "UPDATE control_nodes SET status='succeeded',revision=revision+1 WHERE run_id=? AND node_id=?",
                        (rid, nid),
                    )
                elif (
                    action == "reconcile" and node["status"] == "failed" and node["reason"] == "reconciliation_required"
                ):
                    if not external_id.strip() or not evidence.strip() or not node["job_id"]:
                        raise ControlError("Reconciliation requires a verified external task ID and evidence")
                    rec = self.ledger.retry(node["job_id"])
                    c.execute("UPDATE media_jobs SET external_id=? WHERE id=?", (external_id.strip(), rec.id))
                    c.execute(
                        "UPDATE control_nodes SET job_id=?,status='running',reason='',attempt=attempt+1,"
                        "revision=revision+1 WHERE run_id=? AND node_id=?",
                        (rec.id, rid, nid),
                    )
                    c.execute(
                        "INSERT INTO control_attempts VALUES (?,?,?,?,?,?)",
                        (rid, nid, node["attempt"] + 1, rec.id, "reconciled", now_iso()),
                    )
                    c.execute(
                        "UPDATE control_nodes SET status='pending',reason='',revision=revision+1 "
                        "WHERE run_id=? AND status='blocked' AND reason='dependency_failed'",
                        (rid,),
                    )
                    c.execute("UPDATE control_runs SET status='queued',revision=revision+1 WHERE id=?", (rid,))
                elif action == "retry" and node["status"] in {"failed", "blocked"}:
                    if node["reason"] == "reconciliation_required":
                        raise ControlError(
                            "Uncertain external submission requires reconciliation", "control_conflict", 409
                        )
                    if node["attempt"] >= 3:
                        raise ControlError("Attempt limit reached", "control_conflict", 409)
                    c.execute(
                        "UPDATE control_nodes SET status='pending',job_id=NULL,outputs='{}',reason='',"
                        "attempt=attempt+1,revision=revision+1 WHERE run_id=? AND node_id=?",
                        (rid, nid),
                    )
                    c.execute(
                        "UPDATE control_nodes SET status='pending',reason='',revision=revision+1 "
                        "WHERE run_id=? AND status='blocked' AND reason='dependency_failed'",
                        (rid,),
                    )
                    c.execute("UPDATE control_runs SET status='queued',revision=revision+1 WHERE id=?", (rid,))
                else:
                    raise ControlError("Invalid node transition", "control_conflict", 409)
            else:
                raise ControlError("Unknown action")
            self.event(rid, nid, action, actor, external_id=external_id, evidence=evidence)
        return self.get(rid)

    @staticmethod
    def resolve(value: Any, states: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            if "$node" in value:
                aid = states[value["$node"]]["outputs"].get(value["port"])
                if aid is None:
                    raise ControlError("Required upstream output missing")
                return f"asset:{aid}"
            return {k: GraphStore.resolve(v, states) for k, v in value.items()}
        if isinstance(value, list):
            return [GraphStore.resolve(v, states) for v in value]
        return value

    def tick(self) -> None:
        ids = [
            r[0]
            for r in self.db.conn.execute(
                "SELECT id FROM control_runs WHERE status IN ('queued','running','waiting') "
                "ORDER BY updated_at,created_at "
                "LIMIT 50"
            )
        ]
        for rid in ids:
            try:
                with self.db.transaction():
                    self._advance(rid)
            except ControlError as exc:
                with self.db.transaction():
                    self.db.conn.execute(
                        "UPDATE control_runs SET status='failed',updated_at=? WHERE id=?", (now_iso(), rid)
                    )
                    self.event(rid, "", "failed", reason="invalid_input", detail=str(exc))

    def _set(self, rid: str, nid: str, status: str, reason: str = "", outputs: dict[str, str] | None = None) -> None:
        self.db.conn.execute(
            "UPDATE control_nodes SET status=?,reason=?,outputs=COALESCE(?,outputs),"
            "revision=revision+1 WHERE run_id=? AND node_id=?",
            (status, reason, json.dumps(outputs) if outputs is not None else None, rid, nid),
        )
        self.event(rid, nid, status, reason=reason)

    def _advance(self, rid: str) -> None:
        run = self.get(rid)
        states = {n["node_id"]: n for n in run["nodes"]}
        for node in list(run["plan"]["nodes"]):
            nid, state = node["id"], states[node["id"]]
            if run["cancel_requested"]:
                if state["status"] not in TERMINAL:
                    if state["job_id"]:
                        self.ledger.request_cancel(state["job_id"])
                    self._set(rid, nid, "cancelled")
                continue
            if state["status"] in TERMINAL or state["status"] == "waiting_review":
                continue
            if node.get("sequence"):
                advance_sequence(self, run, node, states)
                continue
            if state["job_id"]:
                job = self.ledger.get_job(state["job_id"])
                if job.status is JobStatus.COMPLETED:
                    assets = self.ledger.list_assets(job_id=job.id)
                    out = {("main" if a.slot == job.slot else a.slot): a.id for a in assets if not a.trashed_at}
                    if not out.get("main"):
                        self._set(rid, nid, "failed", "output_missing")
                    else:
                        if node["model_ref"] == "control-stitch":
                            video = self.ledger.get_asset(out["main"])
                            if (
                                video.duration_sec is None
                                or not video.has_audio
                                or abs(video.duration_sec - float(node["params"]["duration"])) >= 0.1
                            ):
                                self._set(rid, nid, "failed", "output_invalid")
                                continue
                        self._set(rid, nid, "waiting_review" if node["review"] else "succeeded", outputs=out)
                        if node["kind"] == "audio.separate":
                            self.db.conn.execute(
                                "INSERT OR REPLACE INTO control_cache VALUES (?,?,?)",
                                (self.cache_key(run["project_id"], node, job.inputs), json.dumps(out), now_iso()),
                            )
                        parents = asset_ids(job.inputs) | asset_ids(job.params)
                        for aid in out.values():
                            for parent in parents - {aid}:
                                self.db.conn.execute(
                                    "INSERT OR IGNORE INTO asset_relations VALUES (?,?,?,?,?)",
                                    (parent, aid, "input", "{}", now_iso()),
                                )
                elif job.status.is_terminal:
                    reason = (
                        "reconciliation_required"
                        if job.error_code in {"service_unreachable", "timeout", "worker_lost"}
                        else (job.error_code or job.status.value)
                    )
                    self._set(rid, nid, "failed", reason)
                continue
            deps = [states[d] for d in node["depends_on"]]
            if any(d["status"] in {"failed", "blocked", "cancelled", "skipped"} for d in deps):
                self._set(rid, nid, "blocked", "dependency_failed")
                continue
            if not all(d["status"] == "succeeded" for d in deps):
                continue
            if node["skip"]:
                self._set(rid, nid, "skipped")
                continue
            cap = descriptor(self.ledger.settings_for(run["project_id"]), node["kind"])
            binding = next((b for b in cap["bindings"] if b["id"] == node["model_ref"]), None)
            if binding is None or binding["digest"] != node["binding_digest"]:
                self._set(rid, nid, "blocked", "binding_changed")
                continue
            try:
                values = self.resolve(node["inputs"], states)
                inputs = values if node.get("prepared") else build_inputs(kind_spec(node["kind"]), values)
                effective = self.resolve(node["params"], states)
                if node["kind"] == "audio.separate":
                    key = self.cache_key(run["project_id"], node, inputs)
                    cached = self.db.conn.execute(
                        "SELECT outputs FROM control_cache WHERE cache_key=?", (key,)
                    ).fetchone()
                    if cached:
                        outputs = json.loads(cached[0])
                        try:
                            assets = [self.ledger.get_asset(a) for a in outputs.values()]
                        except LedgerError:
                            assets = []  # Purged cached artifacts are a cache miss.
                        if assets and all(not a.trashed_at and (self.settings.home / a.path).is_file() for a in assets):
                            self._set(rid, nid, "waiting_review" if node["review"] else "succeeded", outputs=outputs)
                            self.event(rid, nid, "cache_hit", cache_key=key)
                            continue
                if node["model_ref"] == "control-stitch" and not node.get("prepared"):
                    shots = values.get("shots") or []
                    inputs = {"clips": [s["video"] for s in shots], "source_audio": values.get("bgm")}
                    effective["lengths"] = [float(s["seconds"]) for s in shots]
                for aid in asset_ids(inputs):
                    asset = self.ledger.get_asset(aid)
                    if asset.trashed_at or not (self.settings.home / asset.path).is_file():
                        raise ControlError("Input asset missing")
                request = MediaJob(
                    kind=MediaKind(node["kind"]),
                    model_ref=node["model_ref"],
                    inputs=inputs,
                    params=effective,
                    tier=QualityTier(node["tier"]),
                    project_id=run["project_id"],
                    seed=node["seed"],
                    slot=f"graph:{rid}:{nid}",
                    pipeline_id=rid,
                    idempotency_key=f"graph:{rid}:{nid}:{state['attempt']}",
                    max_attempts=3,
                )
                rec, _ = self.ledger.enqueue(request)
                self.db.conn.execute(
                    "UPDATE control_nodes SET job_id=?,status='running',revision=revision+1 "
                    "WHERE run_id=? AND node_id=?",
                    (rec.id, rid, nid),
                )
                self.db.conn.execute(
                    "INSERT INTO control_attempts VALUES (?,?,?,?,?,?)",
                    (rid, nid, state["attempt"], rec.id, "submitted", now_iso()),
                )
                self.event(rid, nid, "submitted", job_id=rec.id)
            except (ControlError, ValueError) as exc:
                self._set(rid, nid, "failed", "control_invalid")
                self.event(rid, nid, "validation_failed", detail=str(exc))
        latest = self.get(rid)["nodes"]
        statuses = {n["status"] for n in latest}
        status = "running"
        if run["cancel_requested"]:
            status = "cancelled"
        elif statuses <= TERMINAL:
            status = "failed" if statuses & {"failed", "blocked", "cancelled"} else "succeeded"
        elif "running" not in statuses:
            status = "waiting" if "waiting_review" in statuses else "queued"
        self.db.conn.execute("UPDATE control_runs SET status=?,updated_at=? WHERE id=?", (status, now_iso(), rid))

    def cache_key(self, project_id: int | None, node: dict[str, Any], inputs: dict[str, Any]) -> str:
        return digest(
            {
                "project_id": project_id,
                "inputs": content_identity(self.ledger, inputs),
                "binding": node["binding_digest"],
                "params": node["params"],
            }
        )
