"""
One-Click Orchestrator — 一键全流程管线
创意构思 → 导演分析 → 编剧 → 角色 → 场景 → 美术 → 音乐 → 音效 → 渲染 → 导出
所有中间结果保存到 DB，支持断点续做
"""
import json
import time
import os
from typing import Optional, Generator
from pathlib import Path

from core.database import (
    create_project, update_project, get_project,
    create_episode, create_shot, update_shot, delete_shots_by_project,
    add_prompt_log, list_characters,
    list_scene_assets, list_music, list_sfx, list_scripts, list_shots,
)
from core.ollama_client import DEFAULT_MODEL, resolve_model_profile

# ─── 管线阶段定义 ──────────────────────────────────────

STAGES = [
    ("premise",    "📝 分析创作构想",      0.02),
    ("script",     "✍️ 生成剧本大纲",       0.10),
    ("characters", "👤 设计角色",          0.20),
    ("scenes",     "🏞️ 设计场景",          0.32),
    ("art",        "🎨 美术指导",          0.44),
    ("music",      "🎵 生成音乐概念",       0.55),
    ("sfx",        "🔊 设计音效",          0.65),
    ("render",     "🎬 ComfyUI 渲染",      0.78),
    ("export",     "📦 导出视频",          0.92),
]


# ─── 工具函数 ──────────────────────────────────────────

def _ensure_comfyui() -> bool:
    """检查 ComfyUI，如果离线则尝试启动。返回是否在线。"""
    import requests
    try:
        r = requests.get("http://127.0.0.1:8188/queue", timeout=5)
        return r.status_code == 200
    except:
        pass

    # 尝试启动
    comfy_dir = Path(os.path.expanduser("~/Documents/ComfyUI"))
    venv_python = comfy_dir / ".venv" / "bin" / "python3"
    main_py = comfy_dir / "main.py"
    if venv_python.exists() and main_py.exists():
        import subprocess
        logfile = "/tmp/comfyui_auto.log"
        cmd = f"cd {comfy_dir} && nohup {venv_python} main.py --listen 127.0.0.1 > {logfile} 2>&1 &"
        subprocess.run(cmd, shell=True, timeout=10)
        # 等待启动
        for _ in range(30):
            time.sleep(2)
            try:
                r = requests.get("http://127.0.0.1:8188/queue", timeout=3)
                if r.status_code == 200:
                    return True
            except:
                pass
    return False


def _format_log(entries: list[str]) -> str:
    lines = "\n".join(entries) if entries else "等待启动..."
    return f"### 📋 管线日志\n```\n{lines}\n```"


def _normalize_name_map(items) -> dict[str, object]:
    mapping = {}
    for item in items:
        mapping[getattr(item, "name", "").strip()] = item
    return mapping


def _build_render_payload(scene: dict, scene_asset, character_map: dict, style_guide: str) -> dict:
    characters = scene.get("characters", []) or []
    character_refs = []
    for name in characters:
        char = character_map.get(name)
        if not char:
            continue
        character_refs.append({
            "name": char.name,
            "appearance": char.appearance,
            "voice_profile": char.voice_profile,
            "prompt_template": char.prompt_template,
        })

    return {
        "location": scene.get("location", "未知场景"),
        "time_of_day": scene.get("time_of_day", "白天"),
        "weather": scene.get("weather", "晴"),
        "mood": scene.get("mood", "平静"),
        "narration": scene.get("narration", ""),
        "camera_angle": scene.get("camera_angle", "中景"),
        "bgm_mood": scene.get("bgm_mood", ""),
        "dialogue_snippets": scene.get("dialogue_snippets", []),
        "scene_asset": {
            "name": getattr(scene_asset, "name", scene.get("location", "未知场景")),
            "description": getattr(scene_asset, "description", ""),
            "lighting": getattr(scene_asset, "lighting", ""),
            "color_palette": getattr(scene_asset, "color_palette", ""),
            "atmosphere": getattr(scene_asset, "atmosphere", ""),
            "prompt_template": getattr(scene_asset, "prompt_template", ""),
        },
        "characters": character_refs,
        "style_guide": style_guide,
    }


def _build_style_guide(result: dict) -> str:
    palette = result.get("art_style", {}).get("palette", {})
    camera = result.get("art_style", {}).get("camera", {})
    parts = []
    for color in palette.get("primary_colors", [])[:4]:
        if isinstance(color, dict):
            parts.append(f"{color.get('name','')} {color.get('hex','')}".strip())
    if palette.get("lighting_style"):
        parts.append(palette["lighting_style"])
    if palette.get("atmosphere"):
        parts.append(palette["atmosphere"])
    if camera.get("overall_style"):
        parts.append(camera["overall_style"])
    return " | ".join(p for p in parts if p)


def _create_shot_plan(project_id: int, script_id: int, acts_list: list, result: dict) -> list[dict]:
    delete_shots_by_project(project_id)
    episode_id = create_episode({
        "project_id": project_id,
        "number": 1,
        "title": "第1集",
        "summary": result.get("script_synopsis", ""),
        "status": "planned",
    })

    scene_map = _normalize_name_map(list_scene_assets(project_id))
    character_map = _normalize_name_map(list_characters(project_id))
    style_guide = _build_style_guide(result)

    created = []
    for act in acts_list:
        for scene in act.get("scenes", []):
            payload = _build_render_payload(
                scene,
                scene_map.get(scene.get("location", "").strip()),
                character_map,
                style_guide,
            )
            scene_id = (
                f"act{act.get('number', 1):02d}_"
                f"scene{scene.get('number', 1):02d}_shot01"
            )
            payload["scene_id"] = scene_id
            shot_id = create_shot({
                "project_id": project_id,
                "episode_id": episode_id,
                "script_id": script_id,
                "act_number": act.get("number", 1),
                "scene_number": scene.get("number", 1),
                "shot_number": 1,
                "location": scene.get("location", "未知场景"),
                "shot_type": scene.get("camera_angle", "中景"),
                "mood": scene.get("mood", ""),
                "time_of_day": scene.get("time_of_day", "白天"),
                "weather": scene.get("weather", "晴"),
                "characters": scene.get("characters", []),
                "narration": scene.get("narration", ""),
                "dialogue": scene.get("dialogue_snippets", []),
                "camera_notes": scene.get("camera_angle", "中景"),
                "visual_prompt": style_guide,
                "render_payload": payload,
                "status": "ready",
            })
            payload["shot_id"] = shot_id
            update_shot(shot_id, {"render_payload": payload})
            created.append({
                "id": shot_id,
                "episode_id": episode_id,
                "act_number": act.get("number", 1),
                "scene_number": scene.get("number", 1),
                "location": scene.get("location", "未知场景"),
                "shot_type": scene.get("camera_angle", "中景"),
                "scene_id": scene_id,
                "status": "ready",
            })
    return created


# ─── 独立阶段 Generator（每个阶段可单独运行）─────────

def run_stage_story(
    project_id: int,
    premise: str,
    project_name: str = "",
    genre: str = "玄幻",
    tone: str = "热血",
    acts: int = 3,
    model: str = DEFAULT_MODEL,
) -> Generator[tuple[float, str, dict], None, dict]:
    """阶段1: 导演分析 + 创建项目 + 生成剧本。已有剧本则跳过。"""
    log_entries: list[str] = []
    result: dict = {"project_id": project_id, "script_id": 0, "acts_list": []}

    def emit(pct, msg):
        ts = time.strftime("%H:%M:%S")
        log_entries.append(f"[{ts}] {msg}")
        yield pct, _format_log(log_entries), result

    try:
        from core.ollama_client import refresh_models
        refresh_models()

        from agents.director.core import analyze_request
        yield from emit(0.05, "🎬 导演分析创作构想...")
        analysis = analyze_request(premise, model=model)
        if not project_name:
            project_name = analysis.get("project_name", "未命名项目")
        genre = genre or analysis.get("genre", "玄幻")
        tone = tone or analysis.get("tone", "热血")
        yield from emit(0.15, f"✅ 分析完成 → {project_name} ({genre}/{tone})")

        if project_id:
            proj = get_project(project_id)
            if proj:
                yield from emit(0.18, f"📁 使用已有项目: {proj.name} (ID:{project_id})")
                project_name = proj.name
            else:
                project_id = 0
        if not project_id:
            project_id = create_project({
                "name": project_name,
                "description": f"{premise[:200]}...",
                "genre": genre,
                "status": "active",
            })
            result["project_id"] = project_id
            yield from emit(0.20, f"📁 项目已创建: {project_name} (ID:{project_id})")
        result["project_id"] = project_id

        existing = list_scripts(project_id)
        if existing:
            result["script_id"] = existing[0].id
            try:
                result["acts_list"] = json.loads(existing[0].acts or "[]")
            except Exception:
                result["acts_list"] = []
            yield from emit(1.0, f"✅ 已有剧本「{existing[0].title}」，跳过生成")
            return result

        from agents.writer.core import generate_storyline
        yield from emit(0.30, "✍️ 编剧生成剧本大纲...")
        script_data = generate_storyline(
            premise=premise, genre=genre, tone=tone,
            acts=acts, project_id=project_id, model=model,
        )
        if script_data and "title" in script_data:
            result["script_id"] = script_data.get("id", 0)
            result["acts_list"] = script_data.get("acts", [])
            yield from emit(1.0, f"✅ 剧本完成: {script_data['title']} ({len(result['acts_list'])} 幕)")
        else:
            yield from emit(1.0, "⚠️ 剧本生成失败，可重试")
    except Exception as e:
        import traceback
        yield from emit(0.0, f"❌ 剧本阶段出错: {e}\n{traceback.format_exc()[:400]}")
    return result


def run_stage_characters(
    project_id: int,
    model: str = DEFAULT_MODEL,
) -> Generator[tuple[float, str, dict], None, dict]:
    """阶段2: 从剧本提取角色名 → 设计角色。已有角色则跳过。"""
    log_entries: list[str] = []
    result: dict = {"project_id": project_id, "characters": []}

    def emit(pct, msg):
        ts = time.strftime("%H:%M:%S")
        log_entries.append(f"[{ts}] {msg}")
        yield pct, _format_log(log_entries), result

    try:
        proj = get_project(project_id)
        if not proj:
            yield from emit(0.0, "❌ 项目不存在"); return result

        existing_chars = list_characters(project_id)
        if existing_chars:
            result["characters"] = [{"id": c.id, "name": c.name} for c in existing_chars]
            yield from emit(1.0, f"✅ 已有 {len(existing_chars)} 个角色，跳过设计")
            return result

        scripts = list_scripts(project_id)
        if not scripts:
            yield from emit(0.0, "❌ 未找到剧本，请先运行剧本阶段"); return result

        acts_list = json.loads(scripts[0].acts or "[]")
        all_chars: set[str] = set()
        for act in acts_list:
            for sc in act.get("scenes", []):
                all_chars.update(sc.get("characters", []))

        story_context = (
            f"项目：{proj.name}\n类型：{proj.genre}\n"
            f"故事大纲：{scripts[0].synopsis or ''}[:500]"
        )
        from agents.character_designer.core import design_character
        yield from emit(0.05, f"👤 设计 {len(all_chars)} 个角色...")
        for i, name in enumerate(sorted(all_chars)[:8]):
            role = "主角" if i == 0 else "配角"
            data = design_character(
                name=name, role=role,
                story_context=story_context, project_id=project_id, model=model,
            )
            if data and "name" in data:
                result["characters"].append({"id": data.get("id", 0), "name": data["name"]})
                pct = 0.1 + 0.85 * (i + 1) / max(len(all_chars), 1)
                yield from emit(pct, f"  → {data['name']} ({role})")

        yield from emit(1.0, f"✅ 角色设计完成: {len(result['characters'])} 个")
    except Exception as e:
        import traceback
        yield from emit(0.0, f"❌ 角色阶段出错: {e}\n{traceback.format_exc()[:400]}")
    return result


def run_stage_scenes(
    project_id: int,
    model: str = DEFAULT_MODEL,
) -> Generator[tuple[float, str, dict], None, dict]:
    """阶段3: 从剧本提取场景位置 → 设计场景资产。已有场景则跳过。"""
    log_entries: list[str] = []
    result: dict = {"project_id": project_id, "scenes": []}

    def emit(pct, msg):
        ts = time.strftime("%H:%M:%S")
        log_entries.append(f"[{ts}] {msg}")
        yield pct, _format_log(log_entries), result

    try:
        proj = get_project(project_id)
        if not proj:
            yield from emit(0.0, "❌ 项目不存在"); return result

        existing = list_scene_assets(project_id)
        if existing:
            result["scenes"] = [{"id": s.id, "name": s.name} for s in existing]
            yield from emit(1.0, f"✅ 已有 {len(existing)} 个场景，跳过设计")
            return result

        scripts = list_scripts(project_id)
        if not scripts:
            yield from emit(0.0, "❌ 未找到剧本，请先运行剧本阶段"); return result

        acts_list = json.loads(scripts[0].acts or "[]")
        all_scenes: dict[str, dict] = {}
        for act in acts_list:
            for sc in act.get("scenes", []):
                loc = sc.get("location", "")
                if loc and loc not in all_scenes:
                    all_scenes[loc] = sc

        from agents.scene_designer.core import design_scene
        yield from emit(0.05, f"🏞️ 设计 {len(all_scenes)} 个场景...")
        for i, (loc, sc_data) in enumerate(all_scenes.items()):
            data = design_scene(
                scene_name=loc,
                story_context=json.dumps(sc_data, ensure_ascii=False),
                project_id=project_id, model=model,
            )
            if data and "name" in data:
                result["scenes"].append({"id": data.get("id", 0), "name": data["name"]})
                pct = 0.1 + 0.85 * (i + 1) / max(len(all_scenes), 1)
                yield from emit(pct, f"  → {data['name']}")

        yield from emit(1.0, f"✅ 场景设计完成: {len(result['scenes'])} 个")
    except Exception as e:
        import traceback
        yield from emit(0.0, f"❌ 场景阶段出错: {e}\n{traceback.format_exc()[:400]}")
    return result


def run_stage_art_music_sfx(
    project_id: int,
    model: str = DEFAULT_MODEL,
) -> Generator[tuple[float, str, dict], None, dict]:
    """阶段4: 美术指导 + 音乐主题 + 音效。已有数据则跳过。"""
    log_entries: list[str] = []
    result: dict = {"project_id": project_id, "art_style": {}, "music": [], "sfx": []}

    def emit(pct, msg):
        ts = time.strftime("%H:%M:%S")
        log_entries.append(f"[{ts}] {msg}")
        yield pct, _format_log(log_entries), result

    try:
        proj = get_project(project_id)
        if not proj:
            yield from emit(0.0, "❌ 项目不存在"); return result

        scripts = list_scripts(project_id)
        acts_list = json.loads(scripts[0].acts or "[]") if scripts else []

        existing_music = list_music(project_id)
        existing_sfx = list_sfx(project_id)
        if existing_music and existing_sfx:
            result["music"] = [{"id": m.id, "name": m.name} for m in existing_music]
            result["sfx"] = [{"id": s.id, "name": s.name} for s in existing_sfx]
            yield from emit(1.0, f"✅ 已有音乐 {len(existing_music)} 首 / 音效 {len(existing_sfx)} 条，跳过")
            return result

        from agents.art_director.core import define_color_palette, design_camera_language
        yield from emit(0.05, "🎨 美术指导定义视觉风格...")
        palette = define_color_palette(
            project_name=proj.name, genre=proj.genre,
            tone="热血", project_id=project_id, model=model,
        )
        if palette and "name" in palette:
            result["art_style"]["palette"] = palette
            yield from emit(0.15, f"  → 色调: {palette.get('name')}")

        moods = [sc.get("mood", "") for act in acts_list for sc in act.get("scenes", []) if sc.get("mood")]
        if moods:
            camera = design_camera_language(
                genre=proj.genre, mood_sequence=moods[:5],
                project_id=project_id, model=model,
            )
            if camera and "overall_style" in camera:
                result["art_style"]["camera"] = camera
                yield from emit(0.22, f"  → 镜头风格: {camera.get('overall_style','')[:60]}")

        from agents.composer.core import compose_theme, compose_bgm
        yield from emit(0.30, "🎵 作曲师创作音乐...")
        theme = compose_theme(
            project_name=proj.name, genre=proj.genre,
            tone="热血", mood="epic", project_id=project_id, model=model,
        )
        if theme and "name" in theme:
            result["music"].append({"id": theme.get("id", 0), "name": theme["name"], "type": "theme"})
            yield from emit(0.40, f"  → 主题曲: {theme['name']}")

        for act in acts_list[:1]:
            for sc in act.get("scenes", [])[:3]:
                bgm = compose_bgm(
                    scene_description=sc.get("location", ""),
                    scene_mood=sc.get("mood", "平静"),
                    characters_present=sc.get("characters", []),
                    project_id=project_id, model=model,
                )
                if bgm and "name" in bgm:
                    result["music"].append({"id": bgm.get("id", 0), "name": bgm["name"], "type": "bgm"})

        yield from emit(0.55, f"音乐完成: {len(result['music'])} 首")

        from agents.sound_designer.core import design_soundscape
        yield from emit(0.60, "🔊 音效设计师规划音效...")
        all_scenes_desc = "; ".join(
            f"{act_sc.get('location','')}({act_sc.get('mood','')})"
            for act in acts_list for act_sc in act.get("scenes", [])[:4]
        )
        design_soundscape(
            scene_description=f"项目「{proj.name}」({proj.genre}) {all_scenes_desc}",
            location="多场景", weather="晴", time_of_day="全天",
            actions=["对话", "动作", "环境"], project_id=project_id, model=model,
        )
        sfx_items = list_sfx(project_id)
        result["sfx"] = [{"id": s.id, "name": s.name} for s in sfx_items]
        yield from emit(1.0, f"✅ 美术/音乐/音效完成，音效 {len(sfx_items)} 条")
    except Exception as e:
        import traceback
        yield from emit(0.0, f"❌ 美术/音乐/音效阶段出错: {e}\n{traceback.format_exc()[:400]}")
    return result


def run_stage_shots(
    project_id: int,
) -> Generator[tuple[float, str, dict], None, dict]:
    """阶段5: 根据剧本+角色+场景生成分镜计划。每次运行都会重新生成（幂等）。"""
    log_entries: list[str] = []
    result: dict = {"project_id": project_id, "shots": []}

    def emit(pct, msg):
        ts = time.strftime("%H:%M:%S")
        log_entries.append(f"[{ts}] {msg}")
        yield pct, _format_log(log_entries), result

    try:
        proj = get_project(project_id)
        if not proj:
            yield from emit(0.0, "❌ 项目不存在"); return result

        scripts = list_scripts(project_id)
        if not scripts:
            yield from emit(0.0, "❌ 未找到剧本，请先运行剧本阶段"); return result

        acts_list = json.loads(scripts[0].acts or "[]")
        art_result: dict = {}  # art_style not persisted simply, reconstruct from DB if needed

        yield from emit(0.10, "🎞️ 生成分镜计划...")
        shots = _create_shot_plan(
            project_id=project_id,
            script_id=scripts[0].id,
            acts_list=acts_list,
            result=art_result,
        )
        result["shots"] = shots
        yield from emit(1.0, f"✅ 分镜规划完成: {len(shots)} 个镜头")
    except Exception as e:
        import traceback
        yield from emit(0.0, f"❌ 分镜阶段出错: {e}\n{traceback.format_exc()[:400]}")
    return result


def _stage_status(project_id: int) -> dict:
    """快速检查各阶段完成状态，返回 dict。"""
    scripts = list_scripts(project_id)
    chars = list_characters(project_id)
    scenes = list_scene_assets(project_id)
    music = list_music(project_id)
    sfx = list_sfx(project_id)
    shots = list_shots(project_id=project_id)
    return {
        "story":    bool(scripts),
        "chars":    bool(chars),
        "scenes":   bool(scenes),
        "art_music_sfx": bool(music) and bool(sfx),
        "shots":    bool(shots),
        "script_title": scripts[0].title if scripts else "",
        "n_chars":  len(chars),
        "n_scenes": len(scenes),
        "n_shots":  len(shots),
    }


# ─── 核心管线（Generator 版本，支持流式输出）──────────

def run_pipeline_generator(
    premise: str,
    project_name: str = "",
    genre: str = "玄幻",
    tone: str = "热血",
    acts: int = 3,
    model: str = DEFAULT_MODEL,
    model_profile: Optional[dict] = None,
    enable_render: bool = False,
    project_id: int = 0,
) -> Generator[tuple[float, str, Optional[dict]], None, dict]:
    """
    一键全流程管线 — 串行调用各阶段 Stage Generator。
    model_profile: {stage: model_name} 字典，优先于 model 参数。
    project_id: 指定已有项目则继续，未指定则新建。
    """
    log_entries: list[str] = []
    result = {
        "project_id": project_id, "script_id": 0,
        "characters": [], "scenes": [], "shots": [],
        "art_style": {}, "music": [], "sfx": [],
        "render": [], "export": None, "error": None,
    }

    if model_profile is None:
        model_profile = resolve_model_profile(model)

    def emit(pct, msg):
        ts = time.strftime("%H:%M:%S")
        log_entries.append(f"[{ts}] {msg}")
        yield pct, _format_log(log_entries), result

    def relay(stage_gen, pct_offset: float = 0.0, pct_scale: float = 1.0):
        """Relay a stage generator's progress into this generator's log."""
        try:
            while True:
                pct, _, partial = next(stage_gen)
                ts = time.strftime("%H:%M:%S")
                # absorb stage's last log line into our log
                if partial:
                    result.update({k: v for k, v in partial.items() if v})
                yield pct_offset + pct * pct_scale, _format_log(log_entries), result
        except StopIteration as e:
            return e.value if e.value else {}

    try:
        from core.ollama_client import refresh_models, list_models
        refresh_models()
        models = list_models()
        yield from emit(0.01, f"🤖 Ollama: {len(models)} 个模型可用")

        # Stage 1: 剧本
        yield from emit(0.02, "=== 阶段1: 剧本生成 ===")
        for pct, log_md, partial in run_stage_story(
            project_id=project_id, premise=premise, project_name=project_name,
            genre=genre, tone=tone, acts=acts, model=model_profile.get("writer", model),
        ):
            result.update({k: v for k, v in partial.items() if v})
            yield 0.02 + pct * 0.18, log_md, result
        project_id = result.get("project_id", project_id)
        if not project_id:
            yield from emit(0.20, "❌ 项目创建失败，中止")
            return result

        # Stage 2: 角色
        yield from emit(0.21, "=== 阶段2: 角色设计 ===")
        for pct, log_md, partial in run_stage_characters(
            project_id=project_id, model=model_profile.get("character", model),
        ):
            result.update({k: v for k, v in partial.items() if v})
            yield 0.21 + pct * 0.14, log_md, result

        # Stage 3: 场景
        yield from emit(0.36, "=== 阶段3: 场景设计 ===")
        for pct, log_md, partial in run_stage_scenes(
            project_id=project_id, model=model_profile.get("scene", model),
        ):
            result.update({k: v for k, v in partial.items() if v})
            yield 0.36 + pct * 0.14, log_md, result

        # Stage 4: 美术/音乐/音效
        yield from emit(0.51, "=== 阶段4: 美术/音乐/音效 ===")
        for pct, log_md, partial in run_stage_art_music_sfx(
            project_id=project_id, model=model_profile.get("art", model),
        ):
            result.update({k: v for k, v in partial.items() if v})
            yield 0.51 + pct * 0.14, log_md, result

        # Stage 5: 分镜
        yield from emit(0.66, "=== 阶段5: 分镜规划 ===")
        for pct, log_md, partial in run_stage_shots(project_id=project_id):
            result.update({k: v for k, v in partial.items() if v})
            yield 0.66 + pct * 0.08, log_md, result

        proj = get_project(project_id)
        if proj:
            update_project(project_id, {"status": "completed"})
        yield from emit(0.75, "🎉 内容生成完成！")

        # Stage 6: 渲染（可选）
        if enable_render:
            yield from emit(0.76, "=== 阶段6: 渲染 ===")
            comfy_online = _ensure_comfyui()
            if comfy_online:
                from pipelines.batch_renderer import BatchRenderer
                proj = get_project(project_id)
                pname = proj.name if proj else "未知项目"
                render_scenes = []
                for shot in list_shots(project_id=project_id):
                    payload = shot.render_payload
                    if isinstance(payload, str):
                        payload = json.loads(payload) if payload else {}
                    render_scenes.append(payload)
                renderer = BatchRenderer(pname, project_id=project_id)
                render_results = renderer.render_multi_scene(render_scenes)
                result["render"] = render_results
                yield from emit(0.92, f"渲染完成: {len(render_results)}/{len(render_scenes)}")
            else:
                yield from emit(0.80, "⚠️ ComfyUI 离线，跳过渲染")
        else:
            yield from emit(0.75, "⏭️ 跳过渲染（Phase 2 单独运行）")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()[:500]
        log_entries.append(f"[{time.strftime('%H:%M:%S')}] ❌ 管线出错: {e}\n{tb}")
        _pipeline_status["running"] = False
        result["error"] = str(e)
        yield 0.0, _format_log(log_entries), result

    return result


def run_render_export_generator(
    project_id: int,
    project_name: str,
    start_index: int = 0,
    render_config: Optional[dict] = None,
) -> Generator[tuple[float, str, dict], None, dict]:
    """Phase 2：从 DB 读取内容，执行渲染+导出。
    yield (progress, log_markdown, partial_result)
    """
    log_entries: list[str] = []
    result = {"project_id": project_id, "render": [], "export": None, "error": None}

    def emit(pct: float, msg: str):
        ts = time.strftime("%H:%M:%S")
        log_entries.append(f"[{ts}] {msg}")
        return (pct, _format_log(log_entries), result)

    try:
        # ── 1. 从 DB 读取最新数据 ────────────────
        proj = get_project(project_id)
        if not proj:
            yield emit(0.0, "❌ 项目不存在")
            return result
        project_name = project_name or proj.name

        scripts = list_scripts(project_id)
        if not scripts:
            yield emit(0.05, "❌ 没有剧本数据，请先生成内容")
            return result
        script = scripts[0]
        shots = list_shots(project_id=project_id)
        if not shots:
            yield emit(0.08, "❌ 没有分镜数据，请先运行内容生成")
            return result
        yield emit(0.10, f"📖 已加载剧本: {script.title} | 分镜: {len(shots)}")

        # ── 2. 检查 ComfyUI ──────────────────────
        yield emit(0.15, "🎬 检查 ComfyUI 状态...")
        comfy_online = _ensure_comfyui()
        if not comfy_online:
            yield emit(0.20, "❌ ComfyUI 无法启动，请手动启动后重试")
            result["error"] = "ComfyUI offline"
            return result
        yield emit(0.25, "✅ ComfyUI 在线")

        # ── 3. 构建渲染场景数据 ──────────────────
        from pipelines.batch_renderer import BatchRenderer
        render_scenes = []
        for shot in shots:
            payload = shot.render_payload
            if isinstance(payload, str):
                payload = json.loads(payload) if payload else {}
            # 注入渲染配置（checkpoint/lora 等 UI 选择）
            if render_config:
                payload["_render_config"] = render_config
            render_scenes.append(payload)
        cfg_info = f"  checkpoint={render_config.get('checkpoint','默认')}" if render_config else ""
        yield emit(0.30, f"🎯 准备渲染 {len(render_scenes)} 个场景{cfg_info}")

        # ── 4. 批量渲染 ──────────────────────────
        if render_scenes:
            renderer = BatchRenderer(project_name, project_id=project_id)
            render_results = renderer.render_multi_scene(render_scenes, start_index=start_index)
            if render_results:
                result["render"] = render_results
                yield emit(0.80, f"✅ 渲染完成: {len(render_results)}/{len(render_scenes)}")
            else:
                yield emit(0.70, "⚠️ 渲染未生成视频（ComfyUI 处理中或异常）")
        else:
            yield emit(0.40, "⚠️ 没有场景数据可渲染")

        # ── 5. 音频生成（TTS + BGM）────────────────
        if result.get("render"):
            yield emit(0.82, "🎵 生成语音 + BGM...")
            try:
                from pipelines.audio_pipeline import run_audio_pipeline
                audio_result = run_audio_pipeline(
                    project_id,
                    progress_fn=lambda m, p: None,
                )
                n_tts = len(audio_result.get("tts", []))
                n_mus = sum(1 for m in audio_result.get("music", []) if m.get("success") or m.get("skipped"))
                yield emit(0.87, f"✅ 音频完成: TTS {n_tts} 条 · BGM {n_mus} 首")
            except Exception as e:
                yield emit(0.84, f"⚠️ 音频生成部分失败: {e}")

        # ── 6. 音视频合成 + 导出 ─────────────────
        if result.get("render"):
            yield emit(0.88, "🎬 合成音视频...")
            try:
                from pipelines.compositor import run_compositor_pipeline
                comp = run_compositor_pipeline(
                    project_id=project_id,
                    episode=1,
                    burn_subs=True,
                    crossfade=0.5,
                )
                if comp.get("success") and comp.get("episode_file"):
                    final = comp["episode_file"]
                    result["export"] = final
                    size_mb = os.path.getsize(final) / 1024 / 1024
                    yield emit(0.96, f"✅ 导出完成: {final} ({size_mb:.1f}MB)")
                else:
                    # 合成失败回退到简单拼接
                    yield emit(0.91, "⚠️ 带音频合成失败，回退简单拼接...")
                    from pipelines.output_manager import merge_episode
                    merged = merge_episode(project_name, episode=1, overwrite=True)
                    if merged and os.path.exists(merged):
                        result["export"] = merged
                        size_mb = os.path.getsize(merged) / 1024 / 1024
                        yield emit(0.96, f"✅ 导出完成（无音频）: {merged} ({size_mb:.1f}MB)")
                    else:
                        yield emit(0.93, "⚠️ 合并失败")
            except Exception as e:
                yield emit(0.90, f"⚠️ 合成异常: {e}")
                from pipelines.output_manager import merge_episode
                try:
                    merged = merge_episode(project_name, episode=1, overwrite=True)
                    if merged and os.path.exists(merged):
                        result["export"] = merged
                        yield emit(0.95, f"✅ 导出完成（无音频）: {merged}")
                except Exception:
                    pass
        else:
            yield emit(0.82, "⏭️ 跳过导出（无渲染结果）")

        # ── 完成 ────────────────────────────────
        update_project(project_id, {"status": "rendered"})
        yield emit(1.0, "🎉 渲染导出完成！")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()[:500]
        log_entries.append(f"[{time.strftime('%H:%M:%S')}] ❌ 渲染出错: {e}")
        log_entries.append(tb)
        result["error"] = str(e)
        yield (0.0, _format_log(log_entries), result)

    return result

# ─── 旧版兼容（返回 dict，内部调用 generator）─────────

def run_one_click_pipeline(
    premise: str,
    project_name: str = "",
    genre: str = "玄幻",
    tone: str = "热血",
    acts: int = 3,
    model: str = DEFAULT_MODEL,
) -> dict:
    """旧版接口：遍历 generator 收集最终结果。"""
    gen = run_pipeline_generator(
        premise, project_name, genre, tone, acts, model, enable_render=False,
    )
    final_result = None
    try:
        while True:
            _, _, partial = next(gen)
            final_result = partial
    except StopIteration as e:
        if e.value:
            final_result = e.value
    return final_result or {}


# ─── 状态追踪（供旧 UI 轮询用）──────────────────────

_pipeline_status = {"running": False, "current_stage": "", "progress": 0.0, "log": []}

def get_pipeline_status() -> dict:
    return dict(_pipeline_status)

def reset_pipeline_status():
    _pipeline_status["running"] = False
    _pipeline_status["current_stage"] = ""
    _pipeline_status["progress"] = 0.0
    _pipeline_status["log"] = []


# ─── 断点续做 ────────────────────────────────────────

def resume_pipeline(project_id: int) -> dict:
    result = {"project_id": project_id, "summary": ""}
    project = get_project(project_id)
    if not project:
        return {"error": "项目不存在"}
    scripts = list_scripts(project_id)
    chars = list_characters(project_id)
    scenes = list_scene_assets(project_id)
    music = list_music(project_id)
    sfx_list = list_sfx(project_id)
    status = []
    status.append(f"📁 项目: {project.name} ({project.status})")
    status.append(f"✍️  剧本: {'✅' if scripts else '❌'} ({len(scripts)}个)")
    status.append(f"👤 角色: {'✅' if chars else '❌'} ({len(chars)}个)")
    status.append(f"🏞️ 场景: {'✅' if scenes else '❌'} ({len(scenes)}个)")
    status.append(f"🎵 音乐: {'✅' if music else '❌'} ({len(music)}个)")
    status.append(f"🔊 音效: {'✅' if sfx_list else '❌'} ({len(sfx_list)}个)")
    result["summary"] = "\n".join(status)
    result["has_script"] = len(scripts) > 0
    result["has_characters"] = len(chars) > 0
    result["has_scenes"] = len(scenes) > 0
    result["has_music"] = len(music) > 0
    result["has_sfx"] = len(sfx_list) > 0
    return result
