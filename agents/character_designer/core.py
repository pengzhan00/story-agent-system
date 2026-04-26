"""Character Designer Agent — business logic."""
import json
from core.ollama_client import generate, generate_json, DEFAULT_MODEL, CREATIVE_MODEL
from core.database import create_character


CHARACTER_SYSTEM = """You are a Character Designer (角色设计师). You create rich, memorable characters.
You specialize in: appearance design, personality profiling, backstory crafting.
Write in Chinese (中文). Output in JSON format."""


def design_character(
    name: str,
    role: str,
    story_context: str,
    project_id: int = 0,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Design a complete character based on name and role within the story context.
    """
    prompt = f"""Design a detailed character for this story:

Character name: {name}
Role: {role}
Story context: {story_context}

Create a complete character profile including:
1. Basic info (age, gender, appearance)
2. Detailed appearance description (usable as SDXL prompt for image generation)
3. Personality traits (3-5 key traits)
4. Background story (who they are, where they came from)
5. Voice profile (tone, pitch, speech patterns for TTS)
6. Relationships with other characters (if known)
7. Costume/style description (for visual consistency)
8. A signature pose or mannerism

Output JSON (no markdown):
{{
    "name": "...",
    "role": "...",
    "age": "...",
    "gender": "...",
    "appearance": "detailed physical description for AI image generation",
    "personality": "3-5 key personality traits with explanation",
    "background": "character backstory (200-300 words)",
    "voice_profile": "vocal characteristics for TTS cloning",
    "relationships": [
        {{"character": "...", "relation": "..."}}
    ],
    "costume": "clothing and style description",
    "mannerism": "signature pose or habitual gesture",
    "sdxl_prompt": "complete SDXL prompt for generating this character",
    "prompt_template": "reusable prompt template with {{placeholders}} for scene-specific elements"
}}
"""

    result = generate_json(
        prompt=prompt,
        system=CHARACTER_SYSTEM,
        model=model,
        temperature=0.7,
        max_tokens=4096,
        project_id=project_id,
        agent_type="character_designer",
    )

    # Save to DB - ensure all string fields are actually strings
    def _ensure_str(v):
        if isinstance(v, list):
            return "\n".join(str(x) for x in v)
        return str(v) if v is not None else ""

    char_data = {
        "project_id": project_id,
        "name": _ensure_str(result.get("name", name)),
        "role": _ensure_str(result.get("role", role)),
        "age": _ensure_str(result.get("age", "")),
        "gender": _ensure_str(result.get("gender", "")),
        "appearance": _ensure_str(result.get("appearance", "")),
        "personality": _ensure_str(result.get("personality", "")),
        "background": _ensure_str(result.get("background", "")),
        "voice_profile": _ensure_str(result.get("voice_profile", "")),
        "relationships": json.dumps(result.get("relationships", []), ensure_ascii=False),
        "lora_ref": "",
        "ip_ref_images": "[]",
        "prompt_template": _ensure_str(result.get("prompt_template", "")),
    }
    char_id = create_character(char_data)
    result["id"] = char_id
    return result


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Character Designer Agent."""
    if action == "design":
        name = input_data.get("name", "")
        role = input_data.get("role", "")
        story_context = input_data.get("story_context", "")
        model = input_data.get("model", DEFAULT_MODEL)
        result = design_character(name, role, story_context, project_id, model)
        return {"result": result}
    else:
        return {"error": f"Unknown action: {action}"}
