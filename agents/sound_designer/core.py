"""Sound Designer Agent — business logic."""
import json
from core.ollama_client import generate, generate_json, DEFAULT_MODEL, CREATIVE_MODEL
from core.database import create_sfx


SOUND_SYSTEM = """You are a Sound Designer (音效设计师) for film/animation.
You create immersive soundscapes: environmental ambience, Foley effects, transition sounds.
You understand how sound supports storytelling emotionally and spatially.
Output in JSON format."""


def design_soundscape(
    scene_description: str,
    location: str,
    weather: str,
    time_of_day: str,
    actions: list,
    project_id: int = 0,
) -> dict:
    """
    Design a complete soundscape for a scene — ambient + action sounds.
    """
    action_text = ', '.join(actions) if actions else "对话/日常"
    prompt = f"""Design a complete soundscape for this scene:

Location: {location}
Weather: {weather}
Time of day: {time_of_day}
Actions/events: {action_text}
Scene description: {scene_description}

Create a layered soundscape with:
1. Ambient background (environmental sounds)
2. Weather/atmosphere layer
3. Action/Foley sounds for character movements and interactions
4. Transition markers (scene start/end)

Output JSON (no markdown):
{{
    "scene_audio_plan": {{
        "ambient": {{
            "name": "环境音名称",
            "description": "ambient sound details",
            "category": "环境"
        }},
        "weather": {{
            "name": "天气音名称",
            "description": "weather sound details",
            "category": "环境"
        }},
        "foley_actions": [
            {{
                "name": "动作音效名",
                "description": "sound description",
                "category": "动作",
                "timing": "when this sound occurs in scene"
            }}
        ],
        "transition": {{
            "name": "转场音效",
            "description": "transition sound",
            "category": "过渡"
        }}
    }},
    "sound_effects": [
        {{
            "name": "...",
            "category": "环境/动作/情绪/过渡",
            "description": "...",
            "tags": "comma-separated search tags"
        }}
    ]
}}
"""

    result = generate_json(
        prompt=prompt,
        system=SOUND_SYSTEM,
        model=DEFAULT_MODEL,
        temperature=0.6,
        project_id=project_id,
        agent_type="sound_designer",
    )

    # Save individual sound effects to DB
    sfx_list = result.get("sound_effects", [])
    scene_plan = result.get("scene_audio_plan", {})

    # Also save the individual components
    for key in ["ambient", "weather", "transition"]:
        item = scene_plan.get(key, {})
        if item and item.get("name"):
            create_sfx({
                "project_id": project_id,
                "name": item["name"],
                "category": item.get("category", "环境"),
                "description": item.get("description", ""),
                "tags": "",
            })

    for action_item in scene_plan.get("foley_actions", []):
        if action_item.get("name"):
            create_sfx({
                "project_id": project_id,
                "name": action_item["name"],
                "category": action_item.get("category", "动作"),
                "description": action_item.get("description", ""),
                "tags": "",
            })

    for sfx in sfx_list:
        if sfx.get("name"):
            create_sfx({
                "project_id": project_id,
                "name": sfx["name"],
                "category": sfx.get("category", "环境"),
                "description": sfx.get("description", ""),
                "tags": sfx.get("tags", ""),
            })

    return result


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Sound Designer Agent."""
    if action == "design_soundscape":
        scene_description = input_data.get("scene_description", "")
        location = input_data.get("location", "")
        weather = input_data.get("weather", "")
        time_of_day = input_data.get("time_of_day", "")
        actions = input_data.get("actions", [])
        result = design_soundscape(scene_description, location, weather, time_of_day, actions, project_id)
        return {"result": result}
    else:
        return {"error": f"Unknown action: {action}"}
