"""
AI 编辑面板 — Gradio 组件函数
供 ui/app.py 调用，实现角色/场景的 AI 联动编辑。
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import core.database as db
from core.ollama_client import DEFAULT_MODEL


# ── AI 联动编辑（自然语言指令）─────────────────────────────

def ai_edit_preview(project_id: int, instruction: str, model: str = "") -> tuple[str, str]:
    """
    执行 AI 编辑扫描，返回 (预览文本, manifest_json)。
    不写数据库，只返回预览供用户确认。
    """
    if not project_id:
        return "❌ 请先生成项目", ""
    if not instruction or not instruction.strip():
        return "❌ 请输入编辑指令", ""

    try:
        from core.edit_agent import build_manifest
        manifest = build_manifest(
            project_id=int(project_id),
            instruction=instruction.strip(),
            model=model or DEFAULT_MODEL,
        )
        preview = manifest.summary_text()
        manifest_json = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
        return preview, manifest_json
    except Exception as e:
        import traceback
        return f"❌ AI 扫描失败: {e}\n{traceback.format_exc()[-300:]}", ""


def ai_edit_execute(project_id: int, manifest_json: str) -> str:
    """
    执行 manifest_json 中的变更，写入数据库。
    返回执行结果消息。
    """
    if not project_id:
        return "❌ 请先生成项目"
    if not manifest_json or manifest_json.strip() == "":
        return "❌ 没有待执行的变更清单（请先执行预览）"

    try:
        from core.change_manifest import ChangeManifest, Change, execute_manifest
        raw = json.loads(manifest_json)
        manifest = ChangeManifest(
            project_id=raw["project_id"],
            instruction=raw["instruction"],
            changes=[Change(**c) for c in raw.get("changes", [])],
            skipped=[Change(**c) for c in raw.get("skipped", [])],
        )
        success, msg = execute_manifest(manifest, db)
        return msg
    except Exception as e:
        return f"❌ 执行失败: {e}"


def ai_edit_rollback(project_id: int, n: int = 1) -> str:
    """回滚最近 n 次编辑。"""
    if not project_id:
        return "❌ 请先生成项目"
    try:
        from core.change_manifest import rollback_last
        success, msg = rollback_last(int(project_id), db, n=int(n))
        return msg
    except Exception as e:
        return f"❌ 回滚失败: {e}"


def get_edit_history(project_id: int) -> list[list]:
    """获取编辑历史（用于 Dataframe 显示）。"""
    if not project_id:
        return []
    try:
        logs = db.list_edit_log(int(project_id), limit=30)
        rows = []
        for log in logs:
            rows.append([
                log.get("id", ""),
                log.get("created_at", "")[:19],
                log.get("instruction", "")[:40],
                log.get("table_name", ""),
                log.get("field", ""),
                str(log.get("old_value", ""))[:30],
                str(log.get("new_value", ""))[:30],
                f"{float(log.get('ai_confidence', 1.0)):.0%}",
            ])
        return rows
    except Exception:
        return []


# ── 快速字段编辑（表单方式）────────────────────────────────

def quick_char_edit(
    project_id: int,
    char_id: int,
    field: str,
    new_value: str,
    check_cascade: bool = True,
    model: str = "",
) -> tuple[str, str]:
    """
    直接编辑角色某字段，可选联动检查。
    返回 (preview_text, manifest_json)。
    """
    if not project_id or not char_id:
        return "❌ 参数无效", ""
    try:
        from core.edit_agent import quick_field_edit
        manifest = quick_field_edit(
            project_id=int(project_id),
            table="characters",
            record_id=int(char_id),
            field=field,
            new_value=new_value,
            model=model or DEFAULT_MODEL,
        ) if check_cascade else None

        if not manifest:
            from core.change_manifest import ChangeManifest, Change
            char = db.get_character(int(char_id))
            old_val = getattr(char, field, "") if char else ""
            manifest = ChangeManifest(
                project_id=int(project_id),
                instruction=f"直接修改角色 #{char_id}.{field}",
                changes=[Change(
                    table="characters", record_id=int(char_id),
                    field=field, json_path="",
                    old_value=old_val, new_value=new_value,
                    ai_confidence=1.0,
                )],
            )

        return manifest.summary_text(), json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
    except Exception as e:
        return f"❌ 失败: {e}", ""


def load_char_list(project_id: int) -> list[tuple[str, int]]:
    """角色下拉列表数据 (name, id)。"""
    if not project_id:
        return []
    chars = db.list_characters(int(project_id))
    return [(f"{c.name} ({c.role})", c.id) for c in chars]


def load_scene_list(project_id: int) -> list[tuple[str, int]]:
    """场景下拉列表数据 (name, id)。"""
    if not project_id:
        return []
    scenes = db.list_scene_assets(int(project_id))
    return [(s.name, s.id) for s in scenes]
