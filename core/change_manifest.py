"""
变更清单 — AI 联动编辑系统的核心数据结构
支持事务性提交（全成功或全回滚）+ 回滚日志
"""
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import json
import jsonpath_ng
from jsonpath_ng.ext import parse as jp_parse


@dataclass
class Change:
    table: str          # characters | scripts | shots | scenes | music_themes | sound_effects
    record_id: int
    field: str          # name | acts | characters | dialogue | render_payload | ...
    json_path: str      # "" 表示整个字段; "$.acts[0].scenes[1].characters[0]" 表示 JSON 内路径
    old_value: Any
    new_value: Any
    ai_confidence: float = 1.0   # 0.0–1.0, < 0.8 需要用户手动确认
    skip_reason: str = ""        # 非空时表示 AI 决定跳过此变更


@dataclass
class ChangeManifest:
    project_id: int
    instruction: str
    changes: list[Change] = field(default_factory=list)
    skipped: list[Change] = field(default_factory=list)

    def needs_confirmation(self) -> list[Change]:
        return [c for c in self.changes if c.ai_confidence < 0.8]

    def auto_changes(self) -> list[Change]:
        return [c for c in self.changes if c.ai_confidence >= 0.8]

    def summary_text(self) -> str:
        lines = [f"📋 编辑指令: {self.instruction}", f"变更 {len(self.changes)} 处，跳过 {len(self.skipped)} 处"]
        for i, c in enumerate(self.changes):
            conf = f"({c.ai_confidence:.0%})"
            path = f" @ {c.json_path}" if c.json_path else ""
            lines.append(f"  [{i+1}] {c.table}#{c.record_id}.{c.field}{path}: "
                         f"{str(c.old_value)[:30]!r} → {str(c.new_value)[:30]!r} {conf}")
        if self.skipped:
            lines.append("跳过:")
            for c in self.skipped:
                lines.append(f"  ✗ {c.table}#{c.record_id}.{c.field}: {c.skip_reason}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "instruction": self.instruction,
            "changes": [asdict(c) for c in self.changes],
            "skipped": [asdict(c) for c in self.skipped],
        }


def apply_json_path(data: Any, path: str, new_value: Any) -> Any:
    """在 JSON 数据中按路径替换值，返回修改后的副本。"""
    if not path or path == "$":
        return new_value
    import copy
    data = copy.deepcopy(data)
    expr = jp_parse(path)
    expr.update(data, new_value)
    return data


def get_json_path_value(data: Any, path: str) -> Any:
    """从 JSON 数据中按路径读取值。"""
    if not path or path == "$":
        return data
    expr = jp_parse(path)
    matches = expr.find(data)
    return matches[0].value if matches else None


def execute_manifest(manifest: ChangeManifest, db) -> tuple[bool, str]:
    """
    执行变更清单，写入数据库（事务保证）。
    返回 (success, message)。
    db 参数：core.database 模块。
    """
    import sqlite3
    from datetime import datetime, timezone

    if not manifest.changes:
        return True, "无变更需要执行"

    conn = db._get_conn()
    now = datetime.now(timezone.utc).isoformat()

    try:
        with db._lock:
            conn.execute("BEGIN")
            for change in manifest.changes:
                _apply_change_to_db(conn, change)

            # 写入 edit_log
            for change in manifest.changes:
                conn.execute(
                    "INSERT INTO edit_log (project_id, instruction, table_name, record_id, field, "
                    "json_path, old_value, new_value, ai_confidence, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        manifest.project_id, manifest.instruction,
                        change.table, change.record_id, change.field,
                        change.json_path,
                        json.dumps(change.old_value, ensure_ascii=False),
                        json.dumps(change.new_value, ensure_ascii=False),
                        change.ai_confidence, now,
                    )
                )
            conn.execute("COMMIT")
        return True, f"✅ 已执行 {len(manifest.changes)} 处变更"
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return False, f"❌ 执行失败，已回滚: {e}"


def _apply_change_to_db(conn, change: Change):
    """将单条变更写入 SQLite（在已开启的事务内调用）。"""
    table = change.table
    rid = change.record_id
    field = change.field
    path = change.json_path
    new_val = change.new_value

    if path:
        # 需要先读出 JSON 字段，修改指定路径，再写回
        row = conn.execute(f"SELECT {field} FROM {table} WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise ValueError(f"记录不存在: {table}#{rid}")
        current_str = row[0]
        try:
            current_data = json.loads(current_str) if current_str else {}
        except json.JSONDecodeError:
            current_data = current_str
        updated = apply_json_path(current_data, path, new_val)
        serialized = json.dumps(updated, ensure_ascii=False)
        conn.execute(f"UPDATE {table} SET {field}=?, updated_at=datetime('now') WHERE id=?",
                     (serialized, rid))
    else:
        # 直接替换整个字段
        if isinstance(new_val, (dict, list)):
            new_val = json.dumps(new_val, ensure_ascii=False)
        conn.execute(f"UPDATE {table} SET {field}=?, updated_at=datetime('now') WHERE id=?",
                     (new_val, rid))


def rollback_last(project_id: int, db, n: int = 1) -> tuple[bool, str]:
    """
    回滚最近 n 次编辑（按 edit_log 逆序）。
    返回 (success, message)。
    """
    conn = db._get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM edit_log WHERE project_id=? ORDER BY id DESC LIMIT ?",
            (project_id, n)
        ).fetchall()
        if not rows:
            return False, "没有可回滚的编辑记录"

        with db._lock:
            conn.execute("BEGIN")
            for row in rows:
                old_val_str = row["old_value"]
                try:
                    old_val = json.loads(old_val_str)
                except Exception:
                    old_val = old_val_str

                fake_change = Change(
                    table=row["table_name"],
                    record_id=row["record_id"],
                    field=row["field"],
                    json_path=row["json_path"] or "",
                    old_value=None,
                    new_value=old_val,
                    ai_confidence=1.0,
                )
                _apply_change_to_db(conn, fake_change)
                conn.execute("DELETE FROM edit_log WHERE id=?", (row["id"],))
            conn.execute("COMMIT")
        return True, f"✅ 已回滚 {len(rows)} 条变更"
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return False, f"❌ 回滚失败: {e}"
