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


# ─── 核心管线（Generator 版本，支持流式输出）──────────

def run_pipeline_generator(
    premise: str,
    project_name: str = "",
    genre: str = "玄幻",
    tone: str = "热血",
    acts: int = 3,
    model: str = DEFAULT_MODEL,
    enable_render: bool = False,
) -> Generator[tuple[float, str, Optional[dict]], None, dict]:
    """
    Generator 版本的一键全流程管线。
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
        model_profile = resolve_model_profile(model)
        result["model_profile"] = model_profile

        # ── 1. 导演分析 ─────────────────────────
        from agents.director.core import analyze_request
        yield from emit(0.02, "🎬 导演分析创作构想...")
        analysis = analyze_request(premise, model=model_profile["director"])

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

        # ── 3. 生成剧本 ─────────────────────────
        from agents.writer.core import generate_storyline
        yield from emit(0.10, "✍️ 编剧生成剧本大纲...")
        script_data = generate_storyline(
            premise=premise, genre=genre, tone=tone,
            acts=acts, project_id=pid, model=model_profile["writer"],
        )
        if script_data and "title" in script_data:
            result["script_id"] = script_data.get("id", 0)
            result["script_synopsis"] = script_data.get("synopsis", "")
            title = script_data.get("title", "")
            synopsis = script_data.get("synopsis", "")[:60]
            yield from emit(0.15, f"✅ 剧本完成: {title} ({synopsis}...)")

        acts_list = script_data.get("acts", []) if script_data else []

        # ── 4. 设计角色 ─────────────────────────
        from agents.character_designer.core import design_character
        yield from emit(0.20, "👤 角色设计师创建角色...")
        all_chars = set()
        for act in acts_list:
            for sc in act.get("scenes", []):
                for char_name in sc.get("characters", []):
                    all_chars.add(char_name)
        story_context = (
            f"项目：{project_name}\\n类型：{genre}\\n基调：{tone}\\n"
            f"故事大纲：{script_data.get('synopsis', premise)[:500]}"
        )
        for i, char_name in enumerate(sorted(all_chars)[:5]):
            role = "主角" if i == 0 else "配角"
            char_data = design_character(
                name=char_name, role=role,
                story_context=story_context, project_id=pid,
                model=model_profile["character"],
            )
            if char_data and "name" in char_data:
                result["characters"].append({
                    "id": char_data.get("id", 0), "name": char_data.get("name", char_name)
                })
                yield from emit(0.22 + 0.02 * i,
                                f"  → 角色: {char_data.get('name')} ({role})")
        yield from emit(0.28, f"角色设计完成: {len(result['characters'])} 个")

        # ── 5. 设计场景 ─────────────────────────
        from agents.scene_designer.core import design_scene
        yield from emit(0.32, "🏞️ 场景设计师构建场景...")
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
                model=model_profile["scene"],
            )
            if scene_result and "name" in scene_result:
                result["scenes"].append({
                    "id": scene_result.get("id", 0), "name": scene_result.get("name", loc_name)
                })
                yield from emit(0.33 + 0.02 * idx / max(len(all_scenes), 1),
                                f"  → 场景: {scene_result.get('name')}")
        yield from emit(0.38, f"场景设计完成: {len(result['scenes'])} 个")

        # ── 6. 美术指导 ─────────────────────────
        from agents.art_director.core import (
            define_color_palette, design_camera_language,
            review_visual_consistency,
        )
        yield from emit(0.42, "🎨 美术指导定义视觉风格...")
        palette = define_color_palette(
            project_name=project_name, genre=genre,
            tone=tone, project_id=pid, model=model_profile["art"],
        )
        if palette and "name" in palette:
            result["art_style"]["palette"] = palette
            add_prompt_log(pid, "art_director", "color_palette",
                           f"调色板:{project_name}",
                           json.dumps(palette, ensure_ascii=False))
            colors = palette.get("primary_colors", [])
            yield from emit(0.45,
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
                project_id=pid, model=model_profile["art"],
            )
            if camera_lang and "overall_style" in camera_lang:
                result["art_style"]["camera"] = camera_lang
                add_prompt_log(pid, "art_director", "camera_language",
                               f"镜头语言:{genre}",
                               json.dumps(camera_lang, ensure_ascii=False))
                yield from emit(0.48,
                                f"  → 镜头风格: {camera_lang.get('overall_style', '')[:60]}")
        yield from emit(0.50, "✅ 美术指导完成")

        # ── 7. 音乐 ─────────────────────────────
        from agents.composer.core import compose_theme, compose_bgm
        yield from emit(0.52, "🎵 作曲师创作音乐概念...")
        theme = compose_theme(
            project_name=project_name, genre=genre,
            tone=tone, mood="epic", project_id=pid,
            model=model_profile["music"],
        )
        if theme and "name" in theme:
            result["music"].append({
                "id": theme.get("id", 0), "name": theme.get("name"), "type": "theme"
            })
            yield from emit(0.55, f"  → 主题曲: {theme.get('name')}")

        for i, act in enumerate(acts_list[:1]):
            for sc in act.get("scenes", [])[:3]:
                bgm = compose_bgm(
                    scene_description=sc.get("location", "未知场景"),
                    scene_mood=sc.get("mood", "平静"),
                    characters_present=sc.get("characters", []),
                    project_id=pid,
                    model=model_profile["music"],
                )
                if bgm and "name" in bgm:
                    result["music"].append({
                        "id": bgm.get("id", 0), "name": bgm.get("name"), "type": "bgm"
                    })
        yield from emit(0.60, f"音乐概念完成: {len(result['music'])} 首")

        # ── 8. 音效设计 ─────────────────────────
        from agents.sound_designer.core import design_soundscape
        yield from emit(0.63, "🔊 音效设计师规划音效...")
        all_scene_desc = "; ".join(
            f"{loc}({sd.get('mood','平静')})"
            for loc, sd in list(all_scenes.items())[:5]
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
            model=model_profile["sound"],
        )
        sfx_items = list_sfx(pid)
        result["sfx"] = [{"id": s.id, "name": s.name, "category": s.category} for s in sfx_items]
        if sfx_items:
            yield from emit(0.68, f"  → 音效资产: {len(sfx_items)} 条")
        yield from emit(0.70, "✅ 音效设计完成")

        # ── 8.5 分镜规划 ───────────────────────
        result["shots"] = _create_shot_plan(
            project_id=pid,
            script_id=result["script_id"],
            acts_list=acts_list,
            result=result,
        )
        yield from emit(0.74, f"🎞️ 分镜规划完成: {len(result['shots'])} 个镜头")

        # ── 9. 渲染（可选）────────────────────────
        if enable_render:
            yield from emit(0.75, "🎬 检查 ComfyUI 状态...")
            comfy_online = _ensure_comfyui()
            if comfy_online:
                yield from emit(0.78, "✅ ComfyUI 在线，开始批量渲染...")
                from pipelines.batch_renderer import BatchRenderer

                # 从剧本场景构建渲染场景数据
                render_scenes = [json.loads(s.render_payload) for s in list_shots(project_id=pid)]

                if render_scenes:
                    renderer = BatchRenderer(project_name, project_id=pid)
                    render_results = renderer.render_multi_scene(render_scenes)
                    if render_results:
                        result["render"] = render_results
                        yield from emit(0.85,
                                        f"✅ 渲染完成: {len(render_results)}/{len(render_scenes)} 个场景")
                    else:
                        yield from emit(0.83,
                                        "⚠️ 渲染未生成视频（ComfyUI 处理中或异常）")
                else:
                    yield from emit(0.80, "⚠️ 没有可渲染的场景数据")
            else:
                yield from emit(0.78, "⚠️ ComfyUI 离线，跳过渲染")
                result["render"] = ["跳过（ComfyUI 离线）"]
        else:
            yield from emit(0.72, "⏭️ 渲染未启用（可勾选「启用渲染」重新运行）")

        # ── 10. 导出 ────────────────────────────
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
        tb = traceback.format_exc()[:500]
        log_entries.append(f"[{time.strftime('%H:%M:%S')}] ❌ 管线出错: {e}")
        log_entries.append(tb)
        _pipeline_status["running"] = False
        result["error"] = str(e)
        yield (0.0, _format_log(log_entries), result)

    return result


def run_render_export_generator(
    project_id: int,
    project_name: str,
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
            render_scenes.append(payload)
        yield emit(0.30, f"🎯 准备渲染 {len(render_scenes)} 个场景")

        # ── 4. 批量渲染 ──────────────────────────
        if render_scenes:
            renderer = BatchRenderer(project_name, project_id=project_id)
            # 模拟进度（渲染时再细分）
            render_results = renderer.render_multi_scene(render_scenes)
            if render_results:
                result["render"] = render_results
                yield emit(0.80, f"✅ 渲染完成: {len(render_results)}/{len(render_scenes)}")
            else:
                yield emit(0.70, "⚠️ 渲染未生成视频（ComfyUI 处理中或异常）")
        else:
            yield emit(0.40, "⚠️ 没有场景数据可渲染")

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
