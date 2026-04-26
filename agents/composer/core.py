"""Composer Agent — business logic."""
import json
from core.ollama_client import generate, generate_json, DEFAULT_MODEL, CREATIVE_MODEL
from core.database import create_music, list_music


COMPOSER_SYSTEM = """You are a Music Composer (作曲师) for film and animation.
You excel at: creating thematic music, matching mood to sound, designing sonic landscapes.
You understand Chinese (中国) music traditions as well as Western orchestration.
Output in JSON format."""


def compose_theme(
    project_name: str,
    genre: str,
    tone: str,
    character_name: str = "",
    mood: str = "epic",
    project_id: int = 0,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Create a theme music concept — either for the project overall or a character.
    """
    theme_type = "project main theme" if not character_name else f"character theme for {character_name}"
    subject = project_name if not character_name else character_name

    prompt = f"""Create a {theme_type} concept.

Project: {project_name}
Genre: {genre}
Overall tone: {tone}
Subject: {subject}
Mood: {mood}

Design a complete music concept including:
1. Music style and genre
2. Instrumentation (which instruments, orchestra size)
3. Tempo and rhythm
4. Key signature and harmony
5. Emotional arc (how the piece progresses)
6. Leitmotif ideas (recurring melodic phrases)
7. Reference tracks (what existing music is similar in feel)
8. A generation prompt suitable for AI music generators (MusicGen/Suno)

Output JSON (no markdown):
{{
    "name": "{subject}主题曲",
    "type": "theme",
    "mood": "{mood}",
    "tempo": "tempo description (e.g. 中速 80bpm)",
    "instruments": "detailed instrumentation list",
    "key_signature": "key and mode (e.g. D minor)",
    "description": "full music description and emotional journey",
    "prompt_for_gen": "detailed prompt for Suno/MusicGen AI music generation",
    "reference": "similar existing music for reference"
}}
"""

    result = generate_json(
        prompt=prompt,
        system=COMPOSER_SYSTEM,
        model=model,
        temperature=0.8,
        project_id=project_id,
        agent_type="composer",
    )

    # Save to DB
    music_data = {
        "project_id": project_id,
        "name": result.get("name", f"{subject}主题曲"),
        "type": "theme",
        "mood": result.get("mood", mood),
        "tempo": result.get("tempo", ""),
        "instruments": result.get("instruments", ""),
        "key_signature": result.get("key_signature", ""),
        "description": result.get("description", ""),
        "file_path": "",
        "prompt_for_gen": result.get("prompt_for_gen", ""),
    }
    music_id = create_music(music_data)
    result["id"] = music_id
    return result


def compose_bgm(
    scene_description: str,
    scene_mood: str,
    characters_present: list,
    project_id: int = 0,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Create a BGM concept for a specific scene.
    """
    chars = ', '.join(characters_present) if characters_present else "未指定"
    prompt = f"""Create a background music (BGM) concept for this scene.

Scene description: {scene_description}
Scene mood: {scene_mood}
Characters present: {chars}

Design a BGM that:
1. Matches the scene's emotional tone
2. Supports but doesn't overpower dialogue
3. Has a clear emotional arc matching the scene's story beat
4. Can loop or transition smoothly

Output JSON (no markdown):
{{
    "name": "BGM for: {scene_description[:40]}",
    "type": "bgm",
    "mood": "{scene_mood}",
    "tempo": "tempo description",
    "instruments": "instrumentation (keep it appropriate for background)",
    "key_signature": "key recommendation",
    "description": "how the music evolves with the scene",
    "duration_hint": "suggested duration",
    "prompt_for_gen": "detailed prompt for MusicGen/Suno generation",
    "loopable": "yes/no - can this loop seamlessly"
}}
"""

    result = generate_json(
        prompt=prompt,
        system=COMPOSER_SYSTEM,
        model=model,
        temperature=0.7,
        project_id=project_id,
        agent_type="composer",
    )

    music_data = {
        "project_id": project_id,
        "name": result.get("name", f"Scene BGM"),
        "type": "bgm",
        "mood": result.get("mood", scene_mood),
        "tempo": result.get("tempo", ""),
        "instruments": result.get("instruments", ""),
        "key_signature": result.get("key_signature", ""),
        "description": result.get("description", ""),
        "file_path": "",
        "prompt_for_gen": result.get("prompt_for_gen", ""),
    }
    music_id = create_music(music_data)
    result["id"] = music_id
    return result


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Composer Agent."""
    if action == "theme":
        project_name = input_data.get("project_name", "")
        genre = input_data.get("genre", "")
        tone = input_data.get("tone", "")
        character_name = input_data.get("character_name", "")
        mood = input_data.get("mood", "epic")
        model = input_data.get("model", DEFAULT_MODEL)
        result = compose_theme(project_name, genre, tone, character_name, mood, project_id, model)
        return {"result": result}
    elif action == "bgm":
        scene_description = input_data.get("scene_description", "")
        scene_mood = input_data.get("scene_mood", "")
        characters_present = input_data.get("characters_present", [])
        model = input_data.get("model", DEFAULT_MODEL)
        result = compose_bgm(scene_description, scene_mood, characters_present, project_id, model)
        return {"result": result}
    else:
        return {"error": f"Unknown action: {action}"}
