"""
One-Click Orchestrator — 一键全流程管线
创意构思 → 导演分析 → 编剧 → 角色 → 场景 → 美术 → 音乐 → 音效 → 渲染 → 导出
所有中间结果保存到 DB，支持断点续做
"""
import json
import time
from typing import Optional, Callable

from core.database import (
    create_project, update_project, get_project,
    create_script, update_script, get_script,
    create_character, create_scene_asset,
    create_music, create_sfx,
    add_prompt_log, list_projects, list_characters,
    list_scene_assets, list_music, list_sfx, list_scripts,
)
from core.ollama_client import generate, generate_json, DEFAULT_MODEL, CREATIVE_MODEL
from agents.director.core import analyze_request
from agents.writer.core import generate_storyline
from agents.character_designer.core import design_character
from agents.scene_designer.core import design_scene
from agents.art_director.core import define_color_palette, design_camera_language, review_visual_consistency
from agents.composer.core import compose_theme, compose_bgm
from agents.sound_designer.core import design_soundscape


# ─── 进度回调 ──────────────────────────────────────────

_progress_callback: Optional[Callable[[str, float], None]] = None

def set_progress_callback(fn: Callable[[str, float], None]):
    global _progress_callback
    _progress_callback = fn

def _progress(msg: str, pct: float = 0.0):
    if _progress_callback:
        _progress_callback(msg, pct)


# ─── 管线阶段定义 ──────────────────────────────────────

STAGES = [
    ("premise", "📝 分析创作构想", 0.05),
    ("script",  "✍️ 生成剧本大纲", 0.15),
    ("characters", "👤 设计角色", 0.25),
    ("scenes",   "🏞️ 设计场景", 0.35),
    ("art",      "🎨 美术指导", 0.45),
    ("music",    "🎵 生成音乐概念", 0.55),
    ("sfx",      "🔊 设计音效", 0.65),
    ("render",   "🎬 渲染动画", 0.80),
    ("export",   "📦 导出成品", 0.95),
]

# ─── 一键管线 ──────────────────────────────────────────

# Status tracking
_pipeline_status = {"running": False, "current_stage": "", "progress": 0.0, "log": []}

def _log(msg: str):
    _pipeline_status["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")

def get_pipeline_status() -> dict:
    """Return current pipeline status for UI polling."""
    return dict(_pipeline_status)

def reset_pipeline_status():
    _pipeline_status["running"] = False
    _pipeline_status["current_stage"] = ""
    _pipeline_status["progress"] = 0.0
    _pipeline_status["log"] = []


def run_one_click_pipeline(
    premise: str,
    project_name: str = "",
    genre: str = "玄幻",
    tone: str = "热血",
    acts: int = 3,
    render_enabled: bool = True,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    一键全流程：从创意到视频成品
    返回完整的结果字典，包含所有资产 ID
    """
    global _pipeline_status
    _pipeline_status["running"] = True
    _pipeline_status["current_stage"] = "premise"
    _pipeline_status["progress"] = 0.0
    _pipeline_status["log"] = []
    
    result = {
        "project_id": 0,
        "script_id": 0,
        "characters": [],
        "scenes": [],
        "art_style": {},
        "music": [],
        "sfx": [],
        "videos": [],
        "error": None,
    }
    
    try:
        # ── 0. 探测Ollama ──────────────────────────────
        from core.ollama_client import refresh_models, list_models
        refresh_models()
        models = list_models()
        _log(f"🤖 Ollama 在线: {len(models)} 个模型可用")
        
        # ── 1. 导演分析 ────────────────────────────────
        _log("🎬 导演分析创作构想...")
        _progress("导演正在分析创作构想...", 0.02)
        analysis = analyze_request(premise)
        _log(f"✅ 导演分析完成: {analysis.get('project_name', project_name)}")
        
        if not project_name:
            project_name = analysis.get("project_name", "未命名项目")
        if not genre:
            genre = analysis.get("genre", "玄幻")
        if not tone:
            tone = analysis.get("tone", "热血")
        
        # ── 2. 创建项目 ────────────────────────────────
        _log(f"📁 创建项目: {project_name}")
        pid = create_project({
            "name": project_name,
            "description": f"{premise[:100]}...",
            "genre": genre,
            "status": "active",
        })
        result["project_id"] = pid
        _progress("项目已创建", 0.08)
        
        # ── 3. 生成剧本（编剧） ──────────────────────────
        _log("✍️ 编剧生成剧本大纲...")
        _progress("编剧正在创作故事大纲...", 0.10)
        script_data = generate_storyline(
            premise=premise,
            genre=genre,
            tone=tone,
            acts=acts,
            project_id=pid,
        )
        if script_data and "title" in script_data:
            sid = create_script({
                "project_id": pid,
                "title": script_data.get("title", "未命名剧本"),
                "synopsis": script_data.get("synopsis", ""),
                "acts": json.dumps(script_data.get("acts", []), ensure_ascii=False),
                "total_scenes": sum(len(a.get("scenes", [])) for a in script_data.get("acts", [])),
                "status": "draft",
            })
            result["script_id"] = sid
            _log(f"✅ 剧本完成: {script_data.get('title')} ({script_data.get('synopsis', '')[:50]}...)")
        _progress("剧本已生成", 0.18)
        
        # ── 4. 提取角色名并设计角色 ──────────────────────
        _log("👤 角色设计师创建角色...")
        _progress("角色设计师正在塑造角色...", 0.20)
        acts_list = script_data.get("acts", []) if script_data else []
        all_chars = set()
        for act in acts_list:
            for sc in act.get("scenes", []):
                for char_name in sc.get("characters", []):
                    all_chars.add(char_name)
        
        for char_name in sorted(all_chars)[:5]:  # 最多5个角色
            char_data = design_character(
                character_name=char_name,
                project_name=project_name,
                genre=genre,
                tone=tone,
                project_id=pid,
            )
            if char_data and "name" in char_data:
                cid = create_character({
                    "project_id": pid,
                    "name": char_data.get("name", char_name),
                    "gender": char_data.get("gender", "未知"),
                    "age": char_data.get("age", "未知"),
                    "appearance": char_data.get("appearance", ""),
                    "personality": char_data.get("personality", ""),
                    "background": char_data.get("background", ""),
                    "traits": json.dumps(char_data.get("traits", []), ensure_ascii=False),
                    "role_type": char_data.get("role_type", "配角"),
                })
                result["characters"].append({"id": cid, "name": char_data.get("name", char_name)})
                _log(f"  → 角色: {char_data.get('name')} ({char_data.get('role_type', '配角')})")
        _progress(f"角色设计完成: {len(result['characters'])}个角色", 0.28)
        
        # ── 5. 设计场景 ────────────────────────────────
        _log("🏞️ 场景设计师构建场景...")
        _progress("场景设计师正在构建场景...", 0.30)
        all_scenes = {}
        for act in acts_list:
            for sc in act.get("scenes", []):
                loc_name = sc.get("location", "未知场景")
                if loc_name not in all_scenes:
                    all_scenes[loc_name] = sc
        
        for loc_name, sc_data in all_scenes.items():
            scene_result = design_scene(
                scene_name=loc_name,
                mood=sc_data.get("mood", "平静"),
                time_of_day=sc_data.get("time_of_day", "白天"),
                project_id=pid,
            )
            if scene_result and "name" in scene_result:
                asset_id = create_scene_asset({
                    "project_id": pid,
                    "name": scene_result.get("name", loc_name),
                    "description": scene_result.get("description", ""),
                    "mood": scene_result.get("mood", ""),
                    "lighting": scene_result.get("lighting", ""),
                    "color_scheme": scene_result.get("color_scheme", ""),
                    "props": json.dumps(scene_result.get("props", []), ensure_ascii=False),
                })
                result["scenes"].append({"id": asset_id, "name": scene_result.get("name", loc_name)})
                _log(f"  → 场景: {scene_result.get('name')}")
        _progress(f"场景设计完成: {len(result['scenes'])}个场景", 0.38)
        
        # ── 6. 美术指导 ────────────────────────────────
        _log("🎨 美术指导定义视觉风格...")
        _progress("美术指导正在定义视觉风格...", 0.40)
        palette = define_color_palette(
            project_name=project_name,
            genre=genre,
            tone=tone,
            project_id=pid,
            model=model,
        )
        if palette and "name" in palette:
            result["art_style"]["palette"] = palette
            add_prompt_log(pid, "art_director", "color_palette",
                          f"调色板:{project_name}", json.dumps(palette, ensure_ascii=False))
            colors = palette.get("primary_colors", [])
            _log(f"  → 色调方案: {palette.get('name')} ({len(colors)}种主色)")
        
        # 镜头语言
        moods = []
        for act in acts_list:
            for sc in act.get("scenes", []):
                if sc.get("mood"):
                    moods.append(sc["mood"])
        if moods:
            camera_lang = design_camera_language(
                genre=genre,
                mood_sequence=moods[:5],
                project_id=pid,
                model=model,
            )
            if camera_lang and "overall_style" in camera_lang:
                result["art_style"]["camera"] = camera_lang
                add_prompt_log(pid, "art_director", "camera_language",
                              f"镜头语言:{genre}", json.dumps(camera_lang, ensure_ascii=False))
                _log(f"  → 镜头风格: {camera_lang.get('overall_style', '')[:60]}")
        
        _progress("美术指导完成", 0.48)
        
        # ── 7. 音乐主题 ────────────────────────────────
        _log("🎵 作曲师创作音乐概念...")
        _progress("作曲师正在构思音乐...", 0.50)
        theme = compose_theme(
            project_name=project_name,
            genre=genre,
            tone=tone,
            mood="epic",
            project_id=pid,
        )
        if theme and "name" in theme:
            music_id = create_music({
                "project_id": pid,
                "name": theme.get("name", f"{project_name}主题曲"),
                "type": "theme",
                "mood": theme.get("mood", "epic"),
                "tempo": theme.get("tempo", ""),
                "instruments": theme.get("instruments", ""),
                "description": theme.get("description", ""),
                "prompt_for_gen": theme.get("prompt_for_gen", ""),
            })
            result["music"].append({"id": music_id, "name": theme.get("name"), "type": "theme"})
            _log(f"  → 主题曲: {theme.get('name')}")
        
        # 场景 bgm
        for i, act in enumerate(acts_list[:1]):  # 第1幕各场景BGM
            for sc in act.get("scenes", [])[:3]:
                bgm = compose_bgm(
                    scene_description=f"{sc.get('location')}: {sc.get('mood')}",
                    mood=sc.get("mood", "平静"),
                    genre=genre,
                    project_id=pid,
                )
                if bgm and "name" in bgm:
                    mid = create_music({
                        "project_id": pid,
                        "name": bgm.get("name", f"{sc.get('location')}BGM"),
                        "type": "bgm",
                        "mood": sc.get("mood", "平静"),
                        "tempo": bgm.get("tempo", ""),
                        "instruments": bgm.get("instruments", ""),
                        "description": bgm.get("description", ""),
                        "prompt_for_gen": bgm.get("prompt_for_gen", ""),
                    })
                    result["music"].append({"id": mid, "name": bgm.get("name"), "type": "bgm"})
        _progress("音乐概念完成", 0.58)
        
        # ── 8. 音效设计 ────────────────────────────────
        _log("🔊 音效设计师规划音效...")
        _progress("音效设计师正在规划音效...", 0.60)
        sfx_plan = design_soundscape(
            project_name=project_name,
            genre=genre,
            scenes_count=len(all_scenes),
            project_id=pid,
        )
        if sfx_plan and "name" in sfx_plan:
            sfx_id = create_sfx({
                "project_id": pid,
                "name": sfx_plan.get("name", f"{project_name}音效方案"),
                "type": "ambient",
                "description": sfx_plan.get("description", ""),
                "prompt_for_gen": sfx_plan.get("prompt_for_gen", ""),
            })
            result["sfx"].append({"id": sfx_id, "name": sfx_plan.get("name")})
            _log(f"  → 音效方案: {sfx_plan.get('name')}")
        
        _progress("音效设计完成", 0.68)
        
        # ── 9. 渲染动画 ────────────────────────────────
        if render_enabled:
            _log("🎬 开始渲染动画...")
            _progress("正在渲染动画...", 0.70)
            try:
                from pipelines.batch_renderer import BatchRenderer
                renderer = BatchRenderer(project_name=project_name, project_id=pid)
                
                # 只渲染前3个场景做演示
                scenes_to_render = []
                for act in acts_list[:1]:
                    for sc in act.get("scenes", [])[:3]:
                        scenes_to_render.append(sc)
                
                _log(f"  计划渲染 {len(scenes_to_render)} 个场景...")
                
                for idx, sc in enumerate(scenes_to_render):
                    pct = 0.70 + (idx / max(len(scenes_to_render), 1)) * 0.25
                    _progress(f"渲染场景 {idx+1}/{len(scenes_to_render)}: {sc.get('location','')}", pct)
                    
                    video_path = renderer.render_scene(sc)
                    if video_path:
                        result["videos"].append({
                            "scene": sc.get("location", f"scene_{idx}"),
                            "path": video_path,
                        })
                        _log(f"  ✅ 场景{idx+1} 渲染完成: {video_path}")
                    else:
                        _log(f"  ⚠️ 场景{idx+1} 渲染失败")
                
                _progress("渲染完成", 0.95)
                
                # ── 10. 合并导出 ────────────────────────
                _log("📦 合并导出最终视频...")
                if result["videos"]:
                    from pipelines.output_manager import ensure_project_dirs, load_timeline, save_timeline, merge_project
                    paths = ensure_project_dirs(project_name)
                    timeline = load_timeline(project_name)
                    
                    # 注册场景到timeline
                    for v in result["videos"]:
                        timeline["scenes"].append({
                            "id": v["scene"],
                            "file": v["path"],
                            "episode": 1,
                            "duration": 2.0,
                        })
                    # 注册剧集
                    timeline["episodes"].append({
                        "number": 1,
                        "scenes": [v["scene"] for v in result["videos"]],
                        "total_duration": sum(2.0 for _ in result["videos"]),
                    })
                    save_timeline(project_name, timeline)
                    
                    # 合并
                    merged = merge_project(project_name, episode=1, output_name=f"{project_name}_EP1")
                    if merged:
                        result["merged_video"] = str(merged)
                        _log(f"✅ 合并完成: {merged}")
                
                _progress("全部完成！", 1.0)
            except Exception as e:
                _log(f"⚠️ 渲染阶段出错(可跳过): {e}")
                # 不阻断整个流程
        else:
            _log("⏭️ 跳过渲染（已禁用）")
        
        # ── 更新项目状态 ────────────────────────────────
        update_project(pid, {"status": "completed"})
        _log("🎉 全流程完成！")
        _pipeline_status["current_stage"] = "done"
        _pipeline_status["running"] = False
        _pipeline_status["progress"] = 1.0
        
        return result
    
    except Exception as e:
        _log(f"❌ 管线出错: {e}")
        import traceback
        _log(traceback.format_exc()[:300])
        _pipeline_status["running"] = False
        _pipeline_status["current_stage"] = "error"
        result["error"] = str(e)
        return result


# ─── 断点续做 ──────────────────────────────────────────

def resume_pipeline(project_id: int) -> dict:
    """
    从已有项目继续：检查哪些阶段已完成，跳过已完成阶段。
    """
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
