"""
Edit Agent — AI 联动编辑系统
接受自然语言指令，扫描数据库影响范围，生成 ChangeManifest。
"""
import json
import re
from typing import Optional
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.change_manifest import Change, ChangeManifest, get_json_path_value
import core.database as db
from core.ollama_client import generate_json, DEFAULT_MODEL


# ── 系统提示 ──────────────────────────────────────────────────

_SCAN_SYSTEM = """你是一个专业的故事编辑 AI。
用户会告诉你编辑指令，你需要分析数据库内容，找出所有需要修改的地方。

输出格式（严格 JSON）：
{
  "changes": [
    {
      "table": "characters|scripts|shots|scenes|music_themes|sound_effects",
      "record_id": 整数,
      "field": "字段名",
      "json_path": "jsonpath表达式或空字符串",
      "old_value": "旧值",
      "new_value": "新值",
      "ai_confidence": 0.0到1.0的浮点数,
      "skip_reason": ""
    }
  ],
  "skipped": [
    {
      "table": "...", "record_id": 整数, "field": "...", "json_path": "",
      "old_value": "...", "new_value": "", "ai_confidence": 0.0, "skip_reason": "跳过原因"
    }
  ]
}

规则：
1. ai_confidence: 0.9=完全确定，0.7=较确定需确认，<0.5=不确定
2. json_path: 如果字段是JSON字符串且只修改其中一部分，使用 $.path.to.value 格式；否则留空
3. 语义匹配而非字符串匹配（全名vs昵称、带称号等都要考虑）
4. 旁白引用/叙述性提及不同于角色出场，酌情跳过
5. 只输出JSON，不要解释"""


def _collect_db_snapshot(project_id: int) -> dict:
    """收集项目数据库快照，供 AI 分析。"""
    snap = {"project_id": project_id}

    chars = db.list_characters(project_id)
    snap["characters"] = [
        {"id": c.id, "name": c.name, "role": c.role, "age": c.age,
         "appearance": c.appearance[:100], "personality": c.personality[:100],
         "background": c.background[:80], "voice_profile": c.voice_profile,
         "relationships": c.relationships}
        for c in chars
    ]

    scripts = db.list_scripts(project_id)
    snap["scripts"] = []
    for s in scripts:
        try:
            acts_raw = json.loads(s.acts) if s.acts else []
        except Exception:
            acts_raw = []
        # 只截取关键信息避免 prompt 过长
        acts_summary = []
        for ai, act in enumerate(acts_raw):
            for si, scene in enumerate(act.get("scenes", [])):
                acts_summary.append({
                    "act": ai, "scene": si,
                    "characters": scene.get("characters", []),
                    "dialogue_chars": [d.get("character") for d in scene.get("dialogue", [])],
                    "narration_snippet": scene.get("narration", "")[:60],
                })
        snap["scripts"].append({"id": s.id, "title": s.title, "acts_summary": acts_summary})

    scenes = db.list_scene_assets(project_id)
    snap["scenes"] = [
        {"id": s.id, "name": s.name, "description": s.description[:80],
         "atmosphere": s.atmosphere}
        for s in scenes
    ]

    shots = db.list_shots(project_id=project_id)
    snap["shots"] = [
        {"id": sh.id, "location": sh.location,
         "characters": sh.characters,  # JSON string
         "dialogue": sh.dialogue[:200] if sh.dialogue else "[]"}
        for sh in shots
    ]

    return snap


def build_manifest(
    project_id: int,
    instruction: str,
    model: str = DEFAULT_MODEL,
) -> ChangeManifest:
    """
    主入口：输入自然语言指令，返回 ChangeManifest。
    """
    snap = _collect_db_snapshot(project_id)

    prompt = f"""编辑指令: {instruction}

当前数据库内容（精简版）:
{json.dumps(snap, ensure_ascii=False, indent=2)[:6000]}

请扫描所有表中受影响的字段，生成变更清单。"""

    raw = generate_json(
        prompt=prompt,
        system=_SCAN_SYSTEM,
        model=model,
        temperature=0.2,
        max_tokens=4096,
        project_id=project_id,
        agent_type="edit_agent",
    )

    manifest = ChangeManifest(project_id=project_id, instruction=instruction)

    for item in raw.get("changes", []):
        manifest.changes.append(Change(
            table=item.get("table", ""),
            record_id=int(item.get("record_id", 0)),
            field=item.get("field", ""),
            json_path=item.get("json_path", ""),
            old_value=item.get("old_value"),
            new_value=item.get("new_value"),
            ai_confidence=float(item.get("ai_confidence", 0.9)),
            skip_reason="",
        ))

    for item in raw.get("skipped", []):
        manifest.skipped.append(Change(
            table=item.get("table", ""),
            record_id=int(item.get("record_id", 0)),
            field=item.get("field", ""),
            json_path=item.get("json_path", ""),
            old_value=item.get("old_value"),
            new_value=item.get("new_value", ""),
            ai_confidence=float(item.get("ai_confidence", 0.0)),
            skip_reason=item.get("skip_reason", ""),
        ))

    return manifest


def quick_field_edit(
    project_id: int,
    table: str,
    record_id: int,
    field: str,
    new_value: str,
    instruction: str = "",
    model: str = DEFAULT_MODEL,
) -> ChangeManifest:
    """
    快速单字段编辑（UI 表单直接编辑时使用），
    但仍通过 AI 检查是否有联动影响。
    """
    # 读取旧值
    mapping = {
        "characters": (db.get_character, "Character"),
        "scenes": (db.get_scene_asset, "SceneAsset"),
    }
    old_value = ""
    if table == "characters":
        obj = db.get_character(record_id)
        if obj:
            old_value = getattr(obj, field, "")
    elif table == "scenes":
        obj = db.get_scene_asset(record_id)
        if obj:
            old_value = getattr(obj, field, "")

    primary = Change(
        table=table, record_id=record_id, field=field,
        json_path="", old_value=old_value, new_value=new_value,
        ai_confidence=1.0,
    )

    desc = instruction or f"将 {table}#{record_id}.{field} 改为 {new_value!r}"
    manifest = ChangeManifest(project_id=project_id, instruction=desc)
    manifest.changes.append(primary)

    # 如果修改的是角色名，通过 AI 查找联动影响
    if table == "characters" and field == "name" and old_value:
        try:
            linked = _find_name_references(project_id, old_value, new_value, model)
            manifest.changes.extend(linked)
        except Exception as e:
            print(f"[EditAgent] 联动扫描失败（不影响主变更）: {e}")

    return manifest


_NAME_REF_SYSTEM = """你是编辑助手。找出数据库中所有对旧角色名的引用，生成变更列表。
只输出 JSON，格式同之前的 changes 数组。"""


def _find_name_references(
    project_id: int, old_name: str, new_name: str, model: str
) -> list[Change]:
    """专门查找角色名引用变更。"""
    snap = _collect_db_snapshot(project_id)
    prompt = (
        f"角色名从 {old_name!r} 改为 {new_name!r}。\n"
        f"数据库快照:\n{json.dumps(snap, ensure_ascii=False)[:4000]}\n"
        "找出除 characters.name 之外所有引用到此名字的地方（scripts.acts, shots.characters, "
        "shots.dialogue, characters.relationships 等），生成 changes 数组。"
        "跳过旁白中纯叙述性提及（skip_reason='旁白叙述'）。"
    )
    raw = generate_json(
        prompt=prompt, system=_NAME_REF_SYSTEM,
        model=model, temperature=0.1,
        project_id=project_id, agent_type="edit_agent/name_ref",
    )
    changes = []
    for item in (raw.get("changes") or raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        changes.append(Change(
            table=item.get("table", ""),
            record_id=int(item.get("record_id", 0)),
            field=item.get("field", ""),
            json_path=item.get("json_path", ""),
            old_value=item.get("old_value"),
            new_value=item.get("new_value"),
            ai_confidence=float(item.get("ai_confidence", 0.8)),
            skip_reason="",
        ))
    return [c for c in changes if c.table and c.record_id]
