"""Scene Designer Agent — business logic."""
import json
from core.ollama_client import generate, generate_json, DEFAULT_MODEL, CREATIVE_MODEL
from core.database import create_scene_asset


SCENE_SYSTEM = """You are a Scene Designer (场景设计师). You create immersive scene environments.
You specialize in: environment design, lighting, color palettes, atmosphere.
Write in Chinese (中文). Output in JSON format."""


def design_scene(
    scene_name: str,
    story_context: str,
    project_id: int = 0,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Design a complete scene environment asset.
    """
    prompt = f"""Design a detailed scene/environment for a story:

Scene name: {scene_name}
Story context: {story_context}

Create a complete scene profile including:
1. Visual description (for image generation)
2. Lighting setup (time, light sources, color temperature)
3. Color palette (dominant colors)
4. Atmospheric details (smells, sounds, temperature)
5. Weather conditions
6. Key visual elements (props, architecture, nature)
7. Camera-friendly notes (best angles, depth setup)

Output JSON (no markdown):
{{
    "name": "...",
    "description": "rich visual description for image generation",
    "lighting": "lighting setup details",
    "color_palette": "dominant colors and hex codes",
    "atmosphere": "atmospheric details",
    "weather": "weather conditions",
    "key_elements": ["list of key visual elements"],
    "camera_notes": "recommended camera angles and compositions",
    "sdxl_prompt": "complete SDXL prompt for generating this scene background",
    "prompt_template": "reusable prompt template with {{placeholders}}"
}}
"""

    result = generate_json(
        prompt=prompt,
        system=SCENE_SYSTEM,
        model=model,
        temperature=0.7,
        max_tokens=4096,
        project_id=project_id,
        agent_type="scene_designer",
    )

    # Save to DB
    def _ensure_str(v):
        if isinstance(v, list):
            return "\n".join(str(x) for x in v)
        return str(v) if v is not None else ""

    scene_data = {
        "project_id": project_id,
        "name": _ensure_str(result.get("name", scene_name)),
        "description": _ensure_str(result.get("description", "")),
        "lighting": _ensure_str(result.get("lighting", "")),
        "color_palette": _ensure_str(result.get("color_palette", "")),
        "atmosphere": _ensure_str(result.get("atmosphere", "")),
        "ref_images": "[]",
        "lora_ref": "",
        "prompt_template": _ensure_str(result.get("prompt_template", "")),
    }
    scene_id = create_scene_asset(scene_data)
    result["id"] = scene_id
    return result


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Scene Designer Agent."""
    if action == "design":
        scene_name = input_data.get("scene_name", "")
        story_context = input_data.get("story_context", "")
        model = input_data.get("model", DEFAULT_MODEL)
        result = design_scene(scene_name, story_context, project_id, model)
        return {"result": result}
    else:
        return {"error": f"Unknown action: {action}"}
