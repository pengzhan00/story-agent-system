"""Art Director Agent — business logic (visual style unification & art direction)."""
import json
from core.ollama_client import generate, generate_json, DEFAULT_MODEL
from core.database import (
    add_prompt_log, get_project,
    list_scene_assets, get_scene_asset,
    list_characters, get_character,
)


SYSTEM_PROMPT = """你是一位专业的美术指导(Art Director)，负责动画/影视项目的视觉风格统一。
你需要从专业角度分析色彩方案、镜头语言、视觉一致性。
每次回答必须返回 JSON 格式，不要返回其他内容。"""


def define_color_palette(project_name: str, genre: str, tone: str, project_id: int = 0, model: str = "") -> dict:
    """为项目定义主色调板"""
    if not model:
        model = DEFAULT_MODEL

    user_prompt = f"""为动画项目「{project_name}」(类型:{genre} 基调:{tone}) 定义主色调板。

返回 JSON：
{{
    "name": "色调方案名称",
    "primary_colors": [{{"name":"色名", "hex":"#HEX", "usage":"用途"}}],
    "secondary_colors": [{{"name":"色名", "hex":"#HEX", "usage":"用途"}}],
    "atmosphere": "整体视觉氛围描述",
    "lighting_style": "布光风格",
    "reference_notes": "参考风格说明"
}}"""

    try:
        result = generate_json(user_prompt, system=SYSTEM_PROMPT, model=model)
        if isinstance(result, str):
            result = json.loads(result)

        if project_id:
            add_prompt_log(project_id, "art_director", "define_color_palette",
                           user_prompt, json.dumps(result, ensure_ascii=False))

        return result
    except Exception as e:
        return {"error": str(e), "name": "默认色调", "primary_colors": [], "secondary_colors": []}


def design_camera_language(genre: str, mood_sequence: list[str], project_id: int = 0, model: str = "") -> dict:
    """为各场景设计镜头语言"""
    if not model:
        model = DEFAULT_MODEL

    mood_str = ", ".join(mood_sequence)
    user_prompt = f"""为一部{genre}类型动画设计镜头语言方案。
情绪序列: {mood_str}

返回 JSON：
{{
    "overall_style": "整体镜头风格",
    "scene_breakdown": [
        {{
            "mood": "对应情绪",
            "shot_types": ["推荐镜头类型"],
            "camera_movement": "运镜方式",
            "focal_length": "推荐焦距",
            "composition_notes": "构图说明"
        }}
    ],
    "transition_style": "场景转场风格",
    "notes": "注意事项"
}}"""

    try:
        result = generate_json(user_prompt, system=SYSTEM_PROMPT, model=model)
        if isinstance(result, str):
            result = json.loads(result)

        if project_id:
            add_prompt_log(project_id, "art_director", "design_camera_language",
                           user_prompt, json.dumps(result, ensure_ascii=False))

        return result
    except Exception as e:
        return {"error": str(e), "overall_style": "标准镜头", "scene_breakdown": []}


def review_visual_consistency(project_id: int, model: str = "") -> str:
    """审查项目的视觉一致性"""
    if not model:
        model = DEFAULT_MODEL

    project = get_project(project_id)
    scenes = list_scene_assets(project_id)
    chars = list_characters(project_id)

    scene_summaries = []
    for s in scenes[:5]:
        scene_summaries.append(f"- {s.name}: {s.description[:100] if hasattr(s, 'description') and s.description else '暂无'}")

    char_summaries = []
    for c in chars[:5]:
        char_summaries.append(f"- {c.name}: {c.appearance[:80] if hasattr(c, 'appearance') and c.appearance else '暂无'}")

    user_prompt = f"""审查项目的视觉一致性。

项目: {project.name if project else '未知'}
类型: {project.genre if project else ''}

角色:
{chr(10).join(char_summaries)}

场景:
{chr(10).join(scene_summaries)}

请分析：
1. 角色设计风格是否一致
2. 场景色调是否协调
3. 是否存在视觉冲突
4. 给出统一的风格建议

返回格式为 Markdown 文本（不是 JSON）。"""

    try:
        result = generate(user_prompt, system=SYSTEM_PROMPT, model=model)
        if project_id:
            add_prompt_log(project_id, "art_director", "review_consistency",
                           user_prompt, str(result))

        return str(result) if result else "审查完成，未发现问题。"
    except Exception as e:
        return f"审查失败: {str(e)}"


def generate_style_guide_for_comfyui(palette: dict, camera: dict, project_id: int = 0) -> str:
    """从美术指导输出生成 ComfyUI 可用的 prompt 风格指南"""
    parts = []
    if palette and "primary_colors" in palette:
        colors = [c.get("name", "") for c in palette["primary_colors"]]
        parts.append(f"色调: {', '.join(colors)}")
    if palette and "lighting_style" in palette:
        parts.append(f"布光: {palette['lighting_style']}")
    if camera and "overall_style" in camera:
        parts.append(f"镜头: {camera['overall_style']}")
    if palette and "atmosphere" in palette:
        parts.append(f"氛围: {palette['atmosphere']}")

    parts.append("高画质, 细节丰富, 动画风格")
    return ", ".join(parts)


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Art Director Agent."""
    if action == "color_palette":
        project_name = input_data.get("project_name", "")
        genre = input_data.get("genre", "")
        tone = input_data.get("tone", "")
        model = input_data.get("model", "")
        result = define_color_palette(project_name, genre, tone, project_id, model)
        return {"result": result}
    elif action == "camera_language":
        genre = input_data.get("genre", "")
        mood_sequence = input_data.get("mood_sequence", [])
        model = input_data.get("model", "")
        result = design_camera_language(genre, mood_sequence, project_id, model)
        return {"result": result}
    elif action == "review":
        pid = input_data.get("project_id", project_id)
        model = input_data.get("model", "")
        result = review_visual_consistency(pid, model)
        return {"result": result}
    elif action == "style_guide":
        palette = input_data.get("palette", {})
        camera = input_data.get("camera", {})
        result = generate_style_guide_for_comfyui(palette, camera, project_id)
        return {"result": result}
    else:
        return {"error": f"Unknown action: {action}"}
