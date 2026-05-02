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

from core.comfyui_env import COMFYUI_DIR, resolve_comfyui_python, comfyui_main_py
from core.database import (
    create_project, update_project, get_project,
    create_episode, create_shot, update_shot, delete_shots_by_project,
    add_prompt_log, list_characters,
    list_scene_assets, list_music, list_sfx, list_scripts, list_shots,
    transaction,
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
    ("tts",        "🎤 TTS 配音生成",      0.84),
    ("bgm",        "🎶 背景音乐生成",      0.88),
    ("compose",    "🎞️ 音视频合成",        0.92),
    ("export",     "📦 导出视频",          0.96),
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
    comfy_dir = COMFYUI_DIR
    venv_python = resolve_comfyui_python()
    main_py = comfyui_main_py()
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

    location = scene.get("location", "未知场景")
    time_of_day = scene.get("time_of_day", "白天")
    weather = scene.get("weather", "晴")
    mood = scene.get("mood", "平静")
    narration = scene.get("narration", "")
    camera_angle = scene.get("camera_angle", "中景")
    bgm_mood = scene.get("bgm_mood", "")
    dialogue = scene.get("dialogue_snippets", [])
    continuity_anchor = "|".join(sorted([name.strip() for name in characters if name.strip()])) or location
    scene_asset_payload = {
        "name": getattr(scene_asset, "name", location),
        "description": getattr(scene_asset, "description", ""),
        "lighting": getattr(scene_asset, "lighting", ""),
        "color_palette": getattr(scene_asset, "color_palette", ""),
        "atmosphere": getattr(scene_asset, "atmosphere", ""),
        "prompt_template": getattr(scene_asset, "prompt_template", ""),
    }
    subject_refs = []
    for ref in character_refs:
        subject_refs.append({
            "name": ref["name"],
            "appearance": ref.get("appearance", ""),
            "costume": "",
            "expression": scene.get("expression", ""),
            "action": scene.get("action", ""),
            "voice_profile": ref.get("voice_profile", ""),
            "prompt_template": ref.get("prompt_template", ""),
        })
    negative_prompt = (
        "low quality, blurry, bad anatomy, extra fingers, duplicated person, "
        "broken face, broken eyes, text, watermark, logo, inconsistent costume, "
        "off-model character, flat lighting, overexposed, underexposed"
    )

    return {
        "schema_version": "shot.v2",
        "subject": {
            "characters": subject_refs,
            "primary_character": subject_refs[0]["name"] if subject_refs else "",
            "emotion": scene.get("emotion", mood),
            "action": scene.get("action", ""),
            "expression": scene.get("expression", ""),
        },
        "scene": {
            "location": location,
            "time_of_day": time_of_day,
            "weather": weather,
            "lighting": scene_asset_payload.get("lighting", ""),
            "atmosphere": scene_asset_payload.get("atmosphere", ""),
            "props": scene.get("props", []),
        },
        "story": {
            "beat": narration,
            "mood": mood,
            "narration": narration,
            "dialogue_snippets": dialogue,
            "intent": scene.get("intent", ""),
        },
        "camera": {
            "shot_type": camera_angle,
            "camera_angle": camera_angle,
            "framing": scene.get("framing", camera_angle),
            "movement": scene.get("camera_movement", ""),
            "lens_language": scene.get("lens_language", ""),
            "duration_sec": float(scene.get("duration_sec", 3.0)),
        },
        "audio": {
            "bgm_mood": bgm_mood,
            "tts_required": bool(dialogue),
            "sfx_cues": scene.get("sfx_cues", []),
        },
        "references": {
            "scene_asset": scene_asset_payload,
            "characters": character_refs,
            "reference_strategy": scene.get("reference_strategy", "auto_keyframe"),
            "reference_image_path": scene.get("reference_image_path", ""),
            "face_image": scene.get("face_image", ""),
        },
        "style": {
            "style_guide": style_guide,
            "visual_style": scene.get("visual_style", "anime storyboard"),
            "color_script": scene_asset_payload.get("color_palette", ""),
            "negative_prompt": negative_prompt,
            "quality_target": scene.get("quality_target", "production"),
        },
        "continuity": {
            "character_anchor": continuity_anchor,
            "scene_anchor": scene_asset_payload.get("name", location),
            "previous_shot_summary": scene.get("previous_shot_summary", ""),
            "costume_lock": scene.get("costume_lock", ""),
        },
        "output_spec": {
            "width": int(scene.get("width", 832)),
            "height": int(scene.get("height", 480)),
            "frames": int(scene.get("frames", 49)),
            "fps": int(scene.get("fps", 16)),
            "quality_tier": scene.get("quality_tier", "production"),
        },
        # Legacy flat fields kept for backward compatibility.
        "location": location,
        "time_of_day": time_of_day,
        "weather": weather,
        "mood": mood,
        "narration": narration,
        "camera_angle": camera_angle,
        "bgm_mood": bgm_mood,
        "dialogue_snippets": dialogue,
        "scene_asset": scene_asset_payload,
        "characters": character_refs,
        "style_guide": style_guide,
        "negative_prompt": negative_prompt,
        "width": int(scene.get("width", 832)),
        "height": int(scene.get("height", 480)),
        "frames": int(scene.get("frames", 49)),
        "fps": int(scene.get("fps", 16)),
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
    with transaction():
        return _create_shot_plan_txn(project_id, script_id, acts_list, result)


def _create_shot_plan_txn(project_id: int, script_id: int, acts_list: list, result: dict, shots_per_scene_default: int = 3) -> list[dict]:
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
        act_num = act.get("number", 1)
        for scene in act.get("scenes", []):
            scene_num = scene.get("number", 1)
            # Check if scene has shots list (new format)
            scene_shots = scene.get("shots", [])
            if scene_shots:
                num_shots = len(scene_shots)
            else:
                # Legacy compatibility: no shots defined, use default
                num_shots = shots_per_scene_default
                scene_shots = [{} for _ in range(num_shots)]

            for shot_idx in range(num_shots):
                shot_def = scene_shots[shot_idx] if shot_idx < len(scene_shots) else {}
                shot_number = shot_idx + 1

                # Build dialogue from shot definition or fall back to scene level
                dialogue_data = shot_def.get("dialogue", scene.get("dialogue_snippets", []))
                narration_text = shot_def.get("narration", scene.get("narration", ""))
                camera_angle = shot_def.get("camera_angle", scene.get("camera_angle", "中景"))
                mood_val = shot_def.get("mood", scene.get("mood", ""))

                payload = _build_render_payload(
                    scene,
                    scene_map.get(scene.get("location", "").strip()),
                    character_map,
                    style_guide,
                )
                # Override payload with shot-specific data
                payload["narration"] = narration_text
                payload["camera_angle"] = camera_angle
                payload["mood"] = mood_val
                payload["dialogue_snippets"] = dialogue_data
                payload["subject"]["action"] = shot_def.get("action", scene.get("action", ""))
                payload["subject"]["expression"] = shot_def.get("expression", scene.get("expression", ""))
                payload["subject"]["emotion"] = shot_def.get("emotion", mood_val)
                payload["scene"]["location"] = scene.get("location", "未知场景")
                payload["scene"]["time_of_day"] = scene.get("time_of_day", "白天")
                payload["scene"]["weather"] = scene.get("weather", "晴")
                payload["story"]["beat"] = narration_text
                payload["story"]["mood"] = mood_val
                payload["story"]["narration"] = narration_text
                payload["story"]["dialogue_snippets"] = dialogue_data
                payload["camera"]["shot_type"] = camera_angle
                payload["camera"]["camera_angle"] = camera_angle
                payload["camera"]["framing"] = shot_def.get("framing", camera_angle)
                payload["camera"]["movement"] = shot_def.get("camera_movement", scene.get("camera_movement", ""))
                payload["camera"]["lens_language"] = shot_def.get("lens_language", scene.get("lens_language", ""))
                payload["camera"]["duration_sec"] = float(shot_def.get("duration_sec", scene.get("duration_sec", 3.0)))
                payload["audio"]["bgm_mood"] = scene.get("bgm_mood", "")
                payload["audio"]["tts_required"] = bool(dialogue_data)
                payload["audio"]["sfx_cues"] = shot_def.get("sfx_cues", scene.get("sfx_cues", []))
                payload["output_spec"]["width"] = int(shot_def.get("width", scene.get("width", 832)))
                payload["output_spec"]["height"] = int(shot_def.get("height", scene.get("height", 480)))
                payload["output_spec"]["frames"] = int(shot_def.get("frames", scene.get("frames", 49)))
                payload["output_spec"]["fps"] = int(shot_def.get("fps", scene.get("fps", 16)))

                scene_id = (
                    f"act{act_num:02d}_"
                    f"scene{scene_num:02d}_"
                    f"shot{shot_number:02d}"
                )
                payload["scene_id"] = scene_id
                shot_id = create_shot({
                    "project_id": project_id,
                    "episode_id": episode_id,
                    "script_id": script_id,
                    "act_number": act_num,
                    "scene_number": scene_num,
                    "shot_number": shot_number,
                    "location": scene.get("location", "未知场景"),
                    "shot_type": camera_angle,
                    "mood": mood_val,
                    "time_of_day": scene.get("time_of_day", "白天"),
                    "weather": scene.get("weather", "晴"),
                    "characters": shot_def.get("characters_present", scene.get("characters", [])),
                    "narration": narration_text,
                    "dialogue": dialogue_data,
                    "camera_notes": camera_angle,
                    "visual_prompt": style_guide,
                    "render_payload": payload,
                    "status": "ready",
                })
                payload["shot_id"] = shot_id
                update_shot(shot_id, {"render_payload": payload})
                created.append({
                    "id": shot_id,
                    "episode_id": episode_id,
                    "act_number": act_num,
                    "scene_number": scene_num,
                    "shot_number": shot_number,
                    "location": scene.get("location", "未知场景"),
                    "shot_type": camera_angle,
                    "scene_id": scene_id,
                    "status": "ready",
                })
    return created


# ─── 核心管线（Generator 版本，支持流式输出）──────────

def run_pipeline_generator(
    premise: str,
    project_name: str = "",
    genre: str = "玄幻",
    tone: str = "热血",
    acts: int = 5,
    model: str = DEFAULT_MODEL,
    model_profile: Optional[dict] = None,
    enable_render: bool = False,
    total_episodes: int = 5,
) -> Generator[tuple[float, str, Optional[dict]], None, dict]:
    """
    Generator 版本的一键全流程管线，支持多集循环。
    每次 yield (progress, log_markdown, partial_result_or_None)。
    最终 return 完整结果 dict。
    """
    log_entries: list[str] = []
    result = {
        "project_id": 0, "script_id": 0,
        "characters": [], "scenes": [],
        "shots": [],
        "art_style": {}, "music": [], "sfx": [],
        "render": [], "export": None,
        "model_profile": {},
        "error": None,
    }
    pid = 0

    def emit(pct: float, msg: str):
        ts = time.strftime("%H:%M:%S")
        log_entries.append(f"[{ts}] {msg}")
        yield pct, _format_log(log_entries), result

    try:
        # ── 0. 探测 Ollama ──────────────────────
        from core.ollama_client import refresh_models, list_models
        refresh_models()
        models = list_models()
        yield from emit(0.01, f"🤖 Ollama 在线: {len(models)} 个模型可用")
        resolved_model_profile = resolve_model_profile(model)
        if model_profile:
            resolved_model_profile.update({k: v for k, v in model_profile.items() if v})
            resolved_model_profile = resolve_model_profile(resolved_model_profile)
        result["model_profile"] = resolved_model_profile

        # ── 1. 导演分析 ─────────────────────────
        from agents.director.core import analyze_request
        yield from emit(0.02, "🎬 导演分析创作构想...")
        analysis = analyze_request(premise, model=resolved_model_profile["director"])

        if not project_name:
            project_name = analysis.get("project_name", "未命名项目")
        if not genre:
            genre = analysis.get("genre", "玄幻")
        if not tone:
            tone = analysis.get("tone", "热血")

        yield from emit(0.05, f"✅ 导演分析完成 → {project_name} ({genre}/{tone})")

        # ── 2. 创建项目 ─────────────────────────
        pid = create_project({
            "name": project_name,
            "description": f"{premise[:200]}...",
            "genre": genre,
            "status": "active",
        })
        result["project_id"] = pid
        yield from emit(0.08, f"📁 项目已创建: {project_name} (ID:{pid})")

        # ── 多集循环 ─────────────────────────────
        episode_key_chars: set = set()
        all_shots: list = []
        all_music: list = []
        all_sfx_items: list = []

        for ep_idx in range(total_episodes):
            ep_num = ep_idx + 1
            ep_pct_base = 0.08 + 0.85 * (ep_idx / max(total_episodes, 1))

            yield from emit(ep_pct_base, f"🎬 === 开始第{ep_num}集 (共{total_episodes}集) ===")

            # ── 3. 生成剧本（每集独立）────────────
            from agents.writer.core import generate_storyline
            yield from emit(ep_pct_base + 0.02, f"✍️ 第{ep_num}集: 编剧生成剧本大纲...")

            # 携带前一集关键角色到下一集的提示
            episode_premise = premise
            if episode_key_chars:
                episode_premise += f" (关键角色延续: {', '.join(sorted(episode_key_chars)[:5])})"

            script_data = generate_storyline(
                premise=episode_premise, genre=genre, tone=tone,
                acts=acts, project_id=pid, model=resolved_model_profile["writer"],
            )
            if script_data and "title" in script_data:
                if ep_idx == 0:
                    result["script_id"] = script_data.get("id", 0)
                result["script_synopsis"] = script_data.get("synopsis", "")
                title = script_data.get("title", "")
                yield from emit(ep_pct_base + 0.04,
                                f"✅ 第{ep_num}集剧本完成: {title}")

                # 收集关键角色
                for act in script_data.get("acts", []):
                    for sc in act.get("scenes", []):
                        for char_name in sc.get("characters", []):
                            episode_key_chars.add(char_name)

            acts_list = script_data.get("acts", []) if script_data else []

            # ── 4. 设计角色（首集 + 新角色）─────────
            if ep_idx == 0:
                from agents.character_designer.core import design_character
                yield from emit(ep_pct_base + 0.06, "👤 角色设计师创建角色...")
                all_chars = set()
                for act in acts_list:
                    for sc in act.get("scenes", []):
                        for char_name in sc.get("characters", []):
                            all_chars.add(char_name)
                story_context = (
                    f"项目：{project_name}\n类型：{genre}\n基调：{tone}\n"
                    f"故事大纲：{script_data.get('synopsis', premise)[:500]}"
                )
                for i, char_name in enumerate(sorted(all_chars)[:5]):
                    role = "主角" if i == 0 else "配角"
                    char_data = design_character(
                        name=char_name, role=role,
                        story_context=story_context, project_id=pid,
                        model=resolved_model_profile["character"],
                    )
                    if char_data and "name" in char_data:
                        result["characters"].append({
                            "id": char_data.get("id", 0), "name": char_data.get("name", char_name)
                        })
                        yield from emit(ep_pct_base + 0.07 + 0.01 * i,
                                        f"  → 角色: {char_data.get('name')} ({role})")
                yield from emit(ep_pct_base + 0.09, f"角色设计完成: {len(result['characters'])} 个")
            else:
                yield from emit(ep_pct_base + 0.06,
                                f"⏭️ 第{ep_num}集使用已有角色 ({len(result['characters'])} 个)")

            # ── 5. 设计场景（首集 + 新场景）─────────
            if ep_idx == 0:
                from agents.scene_designer.core import design_scene
                yield from emit(ep_pct_base + 0.10, "🏞️ 场景设计师构建场景...")
                all_scenes = {}
                for act in acts_list:
                    for sc in act.get("scenes", []):
                        loc_name = sc.get("location", "未知场景")
                        if loc_name not in all_scenes:
                            all_scenes[loc_name] = sc
                for idx, (loc_name, sc_data) in enumerate(all_scenes.items()):
                    scene_result = design_scene(
                        scene_name=loc_name,
                        story_context=json.dumps(sc_data, ensure_ascii=False),
                        project_id=pid,
                        model=resolved_model_profile["scene"],
                    )
                    if scene_result and "name" in scene_result:
                        result["scenes"].append({
                            "id": scene_result.get("id", 0), "name": scene_result.get("name", loc_name)
                        })
                        yield from emit(ep_pct_base + 0.11 + 0.01 * idx / max(len(all_scenes), 1),
                                        f"  → 场景: {scene_result.get('name')}")
                yield from emit(ep_pct_base + 0.13, f"场景设计完成: {len(result['scenes'])} 个")
            else:
                yield from emit(ep_pct_base + 0.10, f"⏭️ 第{ep_num}集使用已有场景 ({len(result['scenes'])} 个)")

            # ── 6. 美术指导（仅首集）───────────────
            if ep_idx == 0:
                from agents.art_director.core import (
                    define_color_palette, design_camera_language,
                    review_visual_consistency,
                )
                yield from emit(ep_pct_base + 0.14, "🎨 美术指导定义视觉风格...")
                palette = define_color_palette(
                    project_name=project_name, genre=genre,
                    tone=tone, project_id=pid, model=resolved_model_profile["art"],
                )
                if palette and "name" in palette:
                    result["art_style"]["palette"] = palette
                    add_prompt_log(pid, "art_director", "color_palette",
                                   f"调色板:{project_name}",
                                   json.dumps(palette, ensure_ascii=False))
                    colors = palette.get("primary_colors", [])
                    yield from emit(ep_pct_base + 0.15,
                                    f"  → 色调方案: {palette.get('name')} ({len(colors)} 种主色)")

                # 镜头语言
                moods = []
                for act in acts_list:
                    for sc in act.get("scenes", []):
                        if sc.get("mood"):
                            moods.append(sc["mood"])
                if moods:
                    camera_lang = design_camera_language(
                        genre=genre, mood_sequence=moods[:5],
                        project_id=pid, model=resolved_model_profile["art"],
                    )
                    if camera_lang and "overall_style" in camera_lang:
                        result["art_style"]["camera"] = camera_lang
                        add_prompt_log(pid, "art_director", "camera_language",
                                       f"镜头语言:{genre}",
                                       json.dumps(camera_lang, ensure_ascii=False))
                        yield from emit(ep_pct_base + 0.16,
                                        f"  → 镜头风格: {camera_lang.get('overall_style', '')[:60]}")
                yield from emit(ep_pct_base + 0.17, "✅ 美术指导完成")
            else:
                yield from emit(ep_pct_base + 0.14, f"⏭️ 第{ep_num}集使用已有美术风格")

            # ── 7. 音乐（每集配乐）───────────────────
            from agents.composer.core import compose_theme, compose_bgm
            yield from emit(ep_pct_base + 0.18, "🎵 作曲师创作音乐概念...")
            if ep_idx == 0:
                theme = compose_theme(
                    project_name=project_name, genre=genre,
                    tone=tone, mood="epic", project_id=pid,
                    model=resolved_model_profile["music"],
                )
                if theme and "name" in theme:
                    result["music"].append({
                        "id": theme.get("id", 0), "name": theme.get("name"), "type": "theme"
                    })
                    yield from emit(ep_pct_base + 0.19, f"  → 主题曲: {theme.get('name')}")

            # 每集 BGM
            for i, act in enumerate(acts_list[:1]):
                for sc in act.get("scenes", [])[:3]:
                    bgm = compose_bgm(
                        scene_description=sc.get("location", "未知场景"),
                        scene_mood=sc.get("mood", "平静"),
                        characters_present=sc.get("characters", []),
                        project_id=pid,
                        model=resolved_model_profile["music"],
                    )
                    if bgm and "name" in bgm:
                        result["music"].append({
                            "id": bgm.get("id", 0), "name": bgm.get("name"), "type": "bgm"
                        })
            yield from emit(ep_pct_base + 0.20, f"音乐概念完成: {len(result['music'])} 首")

            # ── 8. 音效设计（仅首集）────────────────
            if ep_idx == 0:
                from agents.sound_designer.core import design_soundscape
                yield from emit(ep_pct_base + 0.21, "🔊 音效设计师规划音效...")
                all_scene_desc = "; ".join(
                    f"{loc}({sd.get('mood','平静')})"
                    for loc, sd in list(all_scenes.items())[:5] if ep_idx == 0
                )
                sfx_plan = design_soundscape(
                    scene_description=(
                        f"项目「{project_name}」({genre}) "
                        f"共{len(all_scenes)}个场景: {all_scene_desc}"
                    ),
                    location="多场景",
                    weather=next(iter(all_scenes.values()), {}).get("weather", "晴"),
                    time_of_day="全天",
                    actions=["对话", "动作", "环境过渡"],
                    project_id=pid,
                    model=resolved_model_profile["sound"],
                )
                sfx_items = list_sfx(pid)
                result["sfx"] = [{"id": s.id, "name": s.name, "category": s.category} for s in sfx_items]
                if sfx_items:
                    yield from emit(ep_pct_base + 0.22, f"  → 音效资产: {len(sfx_items)} 条")
                yield from emit(ep_pct_base + 0.23, "✅ 音效设计完成")
            else:
                yield from emit(ep_pct_base + 0.21, f"⏭️ 第{ep_num}集使用已有音效设计")

            # ── 8.5 分镜规划（每集独立）─────────────
            ep_shots = _create_shot_plan(
                project_id=pid,
                script_id=result["script_id"],
                acts_list=acts_list,
                result=result,
            )
            all_shots.extend(ep_shots)
            yield from emit(ep_pct_base + 0.24,
                            f"🎞️ 第{ep_num}集分镜: {len(ep_shots)} 个镜头 (累计: {len(all_shots)})")

        # ── 汇总 shots ───────────────────────────
        result["shots"] = all_shots

        # ── 9. 渲染（可选，仅首集/汇总）───────────
        if enable_render and all_shots:
            yield from emit(0.75, "🎬 初始化 RenderDispatcher...")
            from pipelines.batch_renderer import BatchRenderer  # keep as fallback
            from pipelines.render_pipeline import RenderDispatcher, get_dispatcher

            try:
                dispatcher = get_dispatcher()
                matrix = dispatcher.capability_matrix()
                active_names = [n for n, s in matrix.items() if s.get("available")]
                yield from emit(0.78, f"✅ RenderDispatcher 就绪，可用管线: {active_names or '无'}")
            except Exception as e:
                yield from emit(0.76, f"⚠️ RenderDispatcher 初始化失败: {e}，回退 BatchRenderer")
                dispatcher = None

            shots_to_render = list_shots(project_id=pid)
            render_results = []
            base_output = Path.home() / "myworkspace" / "projects" / "story-agent-system" / "output" / project_name
            base_output.mkdir(parents=True, exist_ok=True)

            for shot in shots_to_render:
                shot_payload = shot.render_payload
                if isinstance(shot_payload, str):
                    shot_payload = json.loads(shot_payload) if shot_payload else {}

                ep = shot.act_number if hasattr(shot, 'act_number') else 1
                act = shot.act_number if hasattr(shot, 'act_number') else 1
                sc = shot.scene_number if hasattr(shot, 'scene_number') else 1
                sh = shot.shot_number if hasattr(shot, 'shot_number') else 1
                output_path = base_output / f"ep{ep:02d}_act{act:02d}_sc{sc:02d}_sh{sh:02d}.mp4"

                success = False
                error_msg = None

                # ── 优先使用 RenderDispatcher ──
                if dispatcher is not None:
                    try:
                        render_result = dispatcher.render(shot_payload, output_path)
                        result_path = render_result.path
                        if result_path and Path(result_path).exists():
                            render_results.append(str(result_path))
                            update_shot(shot.id, {"status": "rendered"})
                            success = True
                            yield from emit(0.80, f"  ✅ shot {shot.id} 渲染成功: {result_path.name}")
                    except Exception as re:
                        error_msg = str(re)
                        yield from emit(0.79, f"  ⚠️ RenderDispatcher 失败 (shot {shot.id}): {error_msg[:80]}")

                # ── fallback: BatchRenderer ──
                if not success:
                    try:
                        # BatchRenderer fallback uses render_multi_scene with single scene
                        fallback_renderer = BatchRenderer(project_name, project_id=pid)
                        fallback_results = fallback_renderer.render_multi_scene([shot_payload])
                        if fallback_results:
                            render_results.extend(fallback_results)
                            update_shot(shot.id, {"status": "rendered"})
                            success = True
                            yield from emit(0.79, f"  ✅ BatchRenderer fallback shot {shot.id} 成功")
                    except Exception as fe:
                        error_msg = str(fe)
                        yield from emit(0.78, f"  ❌ 所有管线失败 (shot {shot.id}): {error_msg[:80]}")

                if not success:
                    update_shot(shot.id, {"status": "failed", "error": (error_msg or "unknown error")[:500]})
                    yield from emit(0.78, f"  ⚠️ shot {shot.id} 渲染失败，已记录（短剧批量模式不阻断）")

            if render_results:
                result["render"] = render_results
                yield from emit(0.85,
                                f"✅ 渲染完成: {len(render_results)}/{len(shots_to_render)} 个镜头")
            else:
                yield from emit(0.83, "⚠️ 所有镜头渲染失败")
        else:
            yield from emit(0.72, "⏭️ 渲染未启用（可勾选「启用渲染」重新运行）")

        # ── 11. TTS 批量生成 ──────────────────────
        if enable_render and result.get("render"):
            yield from emit(0.86, "🎤 TTS 批量生成...")
            from pipelines.audio_pipeline import generate_tts_chattts, _CHATTTS_VOICE_SEEDS
            from core.database import create_audio_asset, list_shots

            audio_output = Path.home() / "myworkspace" / "projects" / "story-agent-system" / "output" / project_name / "audio"
            audio_output.mkdir(parents=True, exist_ok=True)

            tts_shots = list_shots(project_id=pid, status="rendered")
            tts_count = 0
            for shot in tts_shots:
                # 解析 dialogue
                dialogue_list = []
                if hasattr(shot, 'dialogue') and shot.dialogue:
                    try:
                        dialogue_list = json.loads(shot.dialogue) if isinstance(shot.dialogue, str) else shot.dialogue
                    except Exception:
                        dialogue_list = []
                if not dialogue_list:
                    continue

                for idx, line in enumerate(dialogue_list):
                    if not isinstance(line, dict):
                        continue
                    text = line.get("line", "").strip()
                    character = line.get("character", "旁白")
                    if not text:
                        continue

                    # 男声 seed=2, 女声 seed=42
                    voice_seed = 2 if character in ("旁白", "解说", "narrator") else 42
                    # 通过角色性别判断
                    chars = list_characters(pid)
                    found = next((c for c in chars if c.name == character), None)
                    if found and found.gender == "男":
                        voice_seed = _CHATTTS_VOICE_SEEDS.get("男", 2)
                    elif found and found.gender == "女":
                        voice_seed = _CHATTTS_VOICE_SEEDS.get("女", 42)

                    wav_path = str(audio_output / f"shot{shot.id}_char{character}.wav")
                    try:
                        success = generate_tts_chattts(text, wav_path, voice_seed=voice_seed)
                        if success and Path(wav_path).exists():
                            create_audio_asset({
                                "project_id": pid,
                                "shot_id": shot.id,
                                "asset_type": "tts",
                                "file_path": wav_path,
                                "duration_sec": 0,
                                "metadata": json.dumps({"character": character, "line_idx": idx, "text": text}, ensure_ascii=False),
                            })
                            tts_count += 1
                            yield from emit(0.86, f"  ✅ TTS shot {shot.id} char={character}")
                        else:
                            yield from emit(0.86, f"  ⚠️ TTS 失败 shot {shot.id} char={character}")
                    except Exception as te:
                        yield from emit(0.86, f"  ⚠️ TTS 异常 shot {shot.id}: {te}")

            yield from emit(0.87, f"✅ TTS 完成: {tts_count} 条配音")
        else:
            yield from emit(0.86, "⏭️ 跳过 TTS（需要先渲染）")

        # ── 12. BGM 生成 ─────────────────────────
        if enable_render and result.get("render"):
            yield from emit(0.88, "🎶 生成背景音乐...")
            from pipelines.audio_pipeline import generate_music

            audio_output = Path.home() / "myworkspace" / "projects" / "story-agent-system" / "output" / project_name / "audio"
            audio_output.mkdir(parents=True, exist_ok=True)

            # 按 episode 收集 bgm_mood
            bgm_shots = list_shots(project_id=pid)
            episode_bgm_moods = {}
            for s in bgm_shots:
                ep = s.episode_id or 1
                if ep not in episode_bgm_moods:
                    episode_bgm_moods[ep] = []
                mood = s.mood or ""
                if mood and mood not in episode_bgm_moods[ep]:
                    episode_bgm_moods[ep].append(mood)

            bgm_count = 0
            for ep, moods in episode_bgm_moods.items():
                bgm_path = str(audio_output / f"bgm_ep{ep}.wav")
                prompt = "atmospheric background music for Chinese drama, " + ", ".join(moods[:3]) if moods else "atmospheric background music, cinematic, emotional"
                try:
                    success = generate_music(prompt, bgm_path, duration=10, mood=moods[0] if moods else "")
                    if success and Path(bgm_path).exists():
                        create_audio_asset({
                            "project_id": pid,
                            "shot_id": 0,
                            "asset_type": "bgm",
                            "file_path": bgm_path,
                            "duration_sec": 10,
                            "metadata": json.dumps({"episode": ep, "moods": moods, "prompt": prompt}, ensure_ascii=False),
                        })
                        bgm_count += 1
                        yield from emit(0.88, f"  ✅ BGM ep{ep} 生成完成")
                    else:
                        yield from emit(0.88, f"  ⚠️ BGM ep{ep} 生成失败")
                except Exception as be:
                    yield from emit(0.88, f"  ⚠️ BGM ep{ep} 异常: {be}")

            yield from emit(0.89, f"✅ BGM 完成: {bgm_count} 首")
        else:
            yield from emit(0.88, "⏭️ 跳过 BGM（需要先渲染）")

        # ── 13. 合成阶段 ─────────────────────────
        if enable_render and result.get("render"):
            yield from emit(0.90, "🎞️ 音视频合成...")
            from pipelines.compositor import compose_episode

            base_output = Path.home() / "myworkspace" / "projects" / "story-agent-system" / "output" / project_name
            composed_dir = base_output / "composed"
            composed_dir.mkdir(parents=True, exist_ok=True)
            audio_output = base_output / "audio"

            # 按 episode 分组
            all_rendered = list_shots(project_id=pid)
            episode_shots = {}
            for s in all_rendered:
                ep = s.episode_id or 1
                if ep not in episode_shots:
                    episode_shots[ep] = []
                episode_shots[ep].append(s)

            for ep, shots_in_ep in episode_shots.items():
                video_files = []
                for s in shots_in_ep:
                    # 查找渲染输出
                    act = s.act_number or 1
                    sc = s.scene_number or 1
                    sh = s.shot_number or 1
                    candidate = base_output / f"ep{ep:02d}_act{act:02d}_sc{sc:02d}_sh{sh:02d}.mp4"
                    if candidate.exists():
                        video_files.append(str(candidate))

                if not video_files:
                    yield from emit(0.90, f"  ⚠️ ep{ep} 无视频文件，跳过")
                    continue

                ep_output = str(base_output / f"ep{ep:02d}_final.mp4")
                try:
                    final = compose_episode(
                        project_name=project_name,
                        episode=ep,
                        shot_videos=video_files,
                        output_path=ep_output,
                        crossfade_duration=0.5,
                    )
                    if final and Path(final).exists():
                        result["export"] = final
                        yield from emit(0.92, f"  ✅ ep{ep} 合成完成: {Path(final).name}")
                    else:
                        yield from emit(0.91, f"  ⚠️ ep{ep} 合成失败")
                except Exception as ce:
                    yield from emit(0.91, f"  ⚠️ ep{ep} 合成异常: {ce}")

            yield from emit(0.93, f"✅ 合成完成")
        else:
            yield from emit(0.90, "⏭️ 跳过合成（需要先渲染）")

        # ── 14. 导出 ────────────────────────────
        if enable_render and result.get("render"):
            yield from emit(0.90, "📦 合并导出视频...")
            from pipelines.output_manager import merge_episode, export_project
            try:
                merged = merge_episode(project_name, episode=1, overwrite=True)
                if merged and os.path.exists(merged):
                    result["export"] = merged
                    size_mb = os.path.getsize(merged) / 1024 / 1024
                    yield from emit(0.94,
                                    f"✅ 导出完成: {merged} ({size_mb:.1f}MB)")
                else:
                    yield from emit(0.93,
                                    "⚠️ 合并失败（可能缺少渲染视频文件）")
            except Exception as e:
                yield from emit(0.92, f"⚠️ 导出异常: {e}")
        else:
            yield from emit(0.72, "⏭️ 跳过导出（需要渲染完成）")

        # ── 完成 ────────────────────────────────
        update_project(pid, {"status": "completed"})
        yield from emit(1.0, "🎉 全流程完成！")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()[:2000]
        log_entries.append(f"[{time.strftime('%H:%M:%S')}] ❌ 管线出错: {e}")
        log_entries.append(tb)
        _pipeline_status["running"] = False
        result["error"] = str(e)
        yield (0.0, _format_log(log_entries), result)

    return result


def run_render_export_generator(
    project_id: int,
    project_name: str,
    render_config: Optional[dict] = None,
    start_index: int = 0,
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
        yield emit(0.15, "🎬 初始化 RenderDispatcher...")
        from pipelines.batch_renderer import BatchRenderer  # keep as fallback
        from pipelines.render_pipeline import RenderDispatcher, get_dispatcher

        try:
            dispatcher = get_dispatcher()
            matrix = dispatcher.capability_matrix()
            active_names = [n for n, s in matrix.items() if s.get("available")]
            yield emit(0.20, f"✅ RenderDispatcher 就绪，可用管线: {active_names or '无'}")
        except Exception as e:
            yield emit(0.18, f"⚠️ RenderDispatcher 初始化失败: {e}，回退 BatchRenderer")
            dispatcher = None

        yield emit(0.22, f"📖 已加载剧本: {script.title} | 分镜: {len(shots)}")

        # ── 3. 批量渲染每个 shot ──────────────────
        base_output = Path.home() / "myworkspace" / "projects" / "story-agent-system" / "output" / project_name
        base_output.mkdir(parents=True, exist_ok=True)

        render_results = []
        total = len(shots)
        for idx, shot in enumerate(shots):
            progress = 0.25 + 0.55 * (idx / max(total, 1))
            payload = shot.render_payload
            if isinstance(payload, str):
                payload = json.loads(payload) if payload else {}

            ep = shot.act_number if hasattr(shot, 'act_number') else 1
            act = shot.act_number if hasattr(shot, 'act_number') else 1
            sc = shot.scene_number if hasattr(shot, 'scene_number') else 1
            sh = shot.shot_number if hasattr(shot, 'shot_number') else 1
            output_path = base_output / f"ep{ep:02d}_act{act:02d}_sc{sc:02d}_sh{sh:02d}.mp4"

            yield emit(progress, f"  🎬 渲染 shot {shot.id} ({idx+1}/{total})...")
            success = False
            error_msg = None

            # ── 优先使用 RenderDispatcher ──
            if dispatcher is not None:
                try:
                    render_result = dispatcher.render(payload, output_path)
                    result_path = render_result.path
                    if result_path and Path(result_path).exists():
                        render_results.append(str(result_path))
                        update_shot(shot.id, {"status": "rendered"})
                        success = True
                except Exception as re:
                    error_msg = str(re)
                    yield emit(progress + 0.01, f"    ⚠️ RenderDispatcher 失败: {error_msg[:60]}")

            # ── fallback: BatchRenderer ──
            if not success:
                try:
                    fallback_renderer = BatchRenderer(project_name, project_id=project_id)
                    fallback_results = fallback_renderer.render_multi_scene([payload], start_index=start_index)
                    if fallback_results:
                        render_results.extend(fallback_results)
                        update_shot(shot.id, {"status": "rendered"})
                        success = True
                        yield emit(progress + 0.01, f"    ✅ BatchRenderer fallback shot {shot.id} 成功")
                except Exception as fe:
                    error_msg = str(fe)
                    yield emit(progress + 0.01, f"    ❌ 所有管线失败: {error_msg[:60]}")

            if not success:
                update_shot(shot.id, {"status": "failed", "error": (error_msg or "unknown error")[:500]})
                yield emit(progress + 0.01, f"    ⚠️ shot {shot.id} 渲染失败（短剧批量模式，继续下一镜）")

        if render_results:
            result["render"] = render_results
            yield emit(0.80, f"✅ 渲染完成: {len(render_results)}/{total} 个镜头")
        else:
            yield emit(0.70, "⚠️ 所有镜头渲染失败")

        # ── 5. 导出 ──────────────────────────────
        if result.get("render"):
            yield emit(0.85, "📦 合并导出视频...")
            from pipelines.output_manager import merge_episode, export_project
            try:
                merged = merge_episode(project_name, episode=1, overwrite=True)
                if merged and os.path.exists(merged):
                    result["export"] = merged
                    size_mb = os.path.getsize(merged) / 1024 / 1024
                    yield emit(0.95, f"✅ 导出完成: {merged} ({size_mb:.1f}MB)")
                else:
                    yield emit(0.90, "⚠️ 合并失败（可能缺少渲染视频文件）")
            except Exception as e:
                yield emit(0.88, f"⚠️ 导出异常: {e}")
        else:
            yield emit(0.82, "⏭️ 跳过导出（无渲染结果）")

        # ── 完成 ────────────────────────────────
        update_project(project_id, {"status": "rendered"})
        yield emit(1.0, "🎉 渲染导出完成！")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()[:2000]
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
    acts: int = 5,
    model: str = DEFAULT_MODEL,
    total_episodes: int = 5,
) -> dict:
    """旧版接口：遍历 generator 收集最终结果。"""
    gen = run_pipeline_generator(
        premise, project_name, genre, tone, acts, model, enable_render=False,
        total_episodes=total_episodes,
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


def _stage_status(project_id: int) -> dict:
    scripts = list_scripts(project_id)
    chars = list_characters(project_id)
    scenes = list_scene_assets(project_id)
    music = list_music(project_id)
    sfx = list_sfx(project_id)
    shots = list_shots(project_id=project_id)
    return {
        "story": bool(scripts),
        "script_title": scripts[0].title if scripts else "",
        "chars": bool(chars),
        "n_chars": len(chars),
        "scenes": bool(scenes),
        "n_scenes": len(scenes),
        "art_music_sfx": bool(music or sfx),
        "shots": bool(shots),
        "n_shots": len(shots),
    }


def run_stage_story(
    project_id: int,
    premise: str,
    project_name: str = "",
    genre: str = "玄幻",
    tone: str = "热血",
    acts: int = 5,
    model: str = DEFAULT_MODEL,
    total_episodes: int = 5,
):
    yield from run_pipeline_generator(
        premise=premise,
        project_name=project_name,
        genre=genre,
        tone=tone,
        acts=acts,
        model=model,
        enable_render=False,
        total_episodes=total_episodes,
    )


def run_stage_characters(project_id: int, model: str = DEFAULT_MODEL):
    project = get_project(project_id)
    scripts = list_scripts(project_id)
    if not project or not scripts:
        yield 0.0, _format_log(["❌ 项目或剧本不存在"]), {"project_id": project_id}
        return
    script = scripts[0]
    acts_list = json.loads(script.acts) if script.acts else []
    from agents.character_designer.core import design_character
    all_chars = []
    seen = set()
    for act in acts_list:
        for sc in act.get("scenes", []):
            for name in sc.get("characters", []):
                if name not in seen:
                    seen.add(name)
                    all_chars.append(name)
    result = {"project_id": project_id, "characters": []}
    log_entries = []
    story_context = f"项目：{project.name}\n类型：{project.genre}\n故事大纲：{script.synopsis[:500]}"
    for i, char_name in enumerate(all_chars[:8]):
        role = "主角" if i == 0 else "配角"
        data = design_character(char_name, role, story_context, project_id=project_id, model=model)
        result["characters"].append({"id": data.get("id", 0), "name": data.get("name", char_name)})
        log_entries.append(f"角色完成: {data.get('name', char_name)}")
        yield 0.2 + 0.7 * (i + 1) / max(len(all_chars[:8]), 1), _format_log(log_entries), result


def run_stage_scenes(project_id: int, model: str = DEFAULT_MODEL):
    project = get_project(project_id)
    scripts = list_scripts(project_id)
    if not project or not scripts:
        yield 0.0, _format_log(["❌ 项目或剧本不存在"]), {"project_id": project_id}
        return
    script = scripts[0]
    acts_list = json.loads(script.acts) if script.acts else []
    from agents.scene_designer.core import design_scene
    seen = {}
    for act in acts_list:
        for sc in act.get("scenes", []):
            seen.setdefault(sc.get("location", "未知场景"), sc)
    result = {"project_id": project_id, "scenes": []}
    log_entries = []
    for i, (name, sc) in enumerate(seen.items()):
        data = design_scene(name, json.dumps(sc, ensure_ascii=False), project_id=project_id, model=model)
        result["scenes"].append({"id": data.get("id", 0), "name": data.get("name", name)})
        log_entries.append(f"场景完成: {data.get('name', name)}")
        yield 0.2 + 0.7 * (i + 1) / max(len(seen), 1), _format_log(log_entries), result


def run_stage_art_music_sfx(project_id: int, model: str = DEFAULT_MODEL):
    project = get_project(project_id)
    scripts = list_scripts(project_id)
    if not project or not scripts:
        yield 0.0, _format_log(["❌ 项目或剧本不存在"]), {"project_id": project_id}
        return
    script = scripts[0]
    acts_list = json.loads(script.acts) if script.acts else []
    from agents.art_director.core import define_color_palette, design_camera_language
    from agents.composer.core import compose_theme
    from agents.sound_designer.core import design_soundscape

    result = {"project_id": project_id, "art_style": {}, "music": [], "sfx": []}
    result["art_style"]["palette"] = define_color_palette(project.name, project.genre, "热血", project_id=project_id, model=model)
    yield 0.3, _format_log(["美术色调完成"]), result
    moods = [sc.get("mood", "") for act in acts_list for sc in act.get("scenes", []) if sc.get("mood")]
    result["art_style"]["camera"] = design_camera_language(project.genre, moods[:5], project_id=project_id, model=model)
    yield 0.5, _format_log(["镜头语言完成"]), result
    theme = compose_theme(project.name, project.genre, "热血", project_id=project_id, model=model)
    result["music"].append({"id": theme.get("id", 0), "name": theme.get("name", "")})
    yield 0.7, _format_log(["配乐完成"]), result
    sfx = design_soundscape(project.description, "多场景", "晴", "全天", ["对话", "动作"], project_id=project_id, model=model)
    result["sfx"] = [{"name": x.get("name", "")} for x in sfx.get("sound_effects", [])]
    yield 1.0, _format_log(["音效完成"]), result


def run_stage_shots(project_id: int):
    scripts = list_scripts(project_id)
    if not scripts:
        yield 0.0, _format_log(["❌ 没有剧本"]), {"project_id": project_id}
        return
    script = scripts[0]
    acts_list = json.loads(script.acts) if script.acts else []
    result = {"project_id": project_id, "shots": _create_shot_plan(project_id, script.id, acts_list, {"script_synopsis": script.synopsis})}
    yield 1.0, _format_log([f"分镜规划完成: {len(result['shots'])} 个"]), result


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
