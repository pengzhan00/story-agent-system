"""Voice Actor Agent — business logic.
Generates dialogue audio using TTS (soft agent: uses macOS say command as fallback,
produces voice-over plans for future TTS model integration)."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from core.ollama_client import generate, generate_json, DEFAULT_MODEL, CREATIVE_MODEL
from core.database import (
    list_characters, add_prompt_log, log_generation, create_task,
)

VOICE_ACTOR_SYSTEM = """You are a Voice Acting Director (配音导演). You analyze dialogue text and
describe how each line should be spoken: tone, pacing, emphasis, emotional intensity.
You understand voice acting techniques including breath control, pitch variation,
and emotional delivery. Output in Chinese (中文) or English as appropriate."""


def generate_dialogue(
    character_name: str,
    voice_profile: str,
    dialogue_text: str,
    emotion: str = "neutral",
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Generate a voice-over plan for a single line of dialogue and attempt
    macOS say command for audio synthesis.

    Returns dict with:
      - voice_plan: text instructions for how to speak the line
      - audio_file: path to generated audio (or None if say unavailable)
      - tts_prompt: prompt suitable for future TTS model integration
      - character: character name
      - dialogue: the original dialogue text
    """
    if not dialogue_text:
        return {"error": "No dialogue text provided"}

    prompt = f"""Analyze this character's dialogue line and produce a detailed voice acting plan.

Character: {character_name}
Voice Profile: {voice_profile}
Emotion: {emotion}
Dialogue: "{dialogue_text}"

Provide a voice acting breakdown including:
1. Tone (e.g., warm, cold, urgent, hesitant, mocking)
2. Pacing (e.g., slow and deliberate, rapid, staccato, drawling)
3. Emphasis (which words to stress, where to pause)
4. Pitch (high/low register, rising/falling inflection)
5. Emotional state (how the character truly feels underneath)
6. Breath control notes
7. TTS prompt text (a single descriptive line that could guide a TTS model, e.g., "speak in a {{warm, concerned}} tone, {{slowly}} with emphasis on 'never'")

Output as JSON (no markdown):
{{
    "character": "{character_name}",
    "dialogue": "{dialogue_text}",
    "emotion": "{emotion}",
    "tone": "...",
    "pacing": "...",
    "emphasis": "...",
    "pitch": "...",
    "emotional_state": "...",
    "breath_notes": "...",
    "tts_prompt": "descriptive TTS guidance string",
    "delivery_instructions": "brief instructions a voice actor would follow"
}}"""

    result = generate_json(
        prompt=prompt,
        system=VOICE_ACTOR_SYSTEM,
        model=DEFAULT_MODEL,
        temperature=0.7,
        max_tokens=4096,
        project_id=project_id,
        agent_type="voice_actor",
    )

    # Attempt macOS say command for TTS fallback
    audio_file = _attempt_say_synthesis(dialogue_text, character_name, emotion)

    if audio_file:
        result["audio_file"] = audio_file
        result["audio_synthesized"] = True
    else:
        result["audio_file"] = None
        result["audio_synthesized"] = False
        result["synthesis_note"] = (
            "macOS say command not available or failed. "
            "Use tts_prompt field for future TTS model integration."
        )

    # Save to generation_logs for future TTS model training
    log_generation({
        "project_id": project_id,
        "agent_type": "voice_actor/generate_dialogue",
        "model": DEFAULT_MODEL,
        "prompt": f"Character: {character_name}\nDialogue: {dialogue_text}\nEmotion: {emotion}",
        "response": json.dumps(result, ensure_ascii=False)[:5000],
        "tokens_in": 0,
        "tokens_out": 0,
        "duration_ms": 0,
    })

    return result


def _attempt_say_synthesis(text: str, character_name: str, emotion: str) -> Optional[str]:
    """Attempt to use macOS 'say' command for TTS synthesis.
    Returns path to generated audio file, or None on failure."""
    try:
        # macOS say command
        safe_name = character_name.replace(" ", "_").replace("/", "_")
        safe_emotion = emotion.replace(" ", "_")
        audio_dir = Path(tempfile.gettempdir()) / "story_agent_tts"
        audio_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(audio_dir / f"{safe_name}_{safe_emotion}_{abs(hash(text)) % 10000}.aiff")

        # Different voices based on emotion
        voice = _map_emotion_to_voice(emotion)

        cmd = ["say", "-v", voice, "-o", output_path, text]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _map_emotion_to_voice(emotion: str) -> str:
    """Map emotion to macOS say command voice."""
    emotion_lower = emotion.lower().strip()
    voice_map = {
        "happy": "Samantha",
        "sad": "Samantha",
        "angry": "Daniel",
        "fear": "Samantha",
        "surprise": "Samantha",
        "neutral": "Samantha",
        "excited": "Samantha",
        "calm": "Samantha",
        "whisper": "Samantha",
        "shout": "Daniel",
        "joyful": "Samantha",
        "melancholy": "Samantha",
        "narrative": "Samantha",
    }
    for key, voice in voice_map.items():
        if key in emotion_lower:
            return voice
    # Check for partial matches
    if "angry" in emotion_lower or "furious" in emotion_lower:
        return "Daniel"
    if "sad" in emotion_lower or "depressed" in emotion_lower:
        return "Samantha"
    return "Samantha"


def save_script(
    script_data: dict,
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Take a full script with dialogue and generate a voice-over plan.
    Returns which lines need voice, suggested pacing, and emotional arc."""
    if not script_data:
        return {"error": "No script data provided"}

    title = script_data.get("title", "Untitled")
    acts = script_data.get("acts", [])
    if isinstance(acts, str):
        try:
            acts = json.loads(acts)
        except json.JSONDecodeError:
            acts = []

    lines_plan = []
    emotional_arc = []
    total_lines = 0

    for act in acts:
        scenes = act.get("scenes", [])
        if isinstance(scenes, str):
            try:
                scenes = json.loads(scenes)
            except json.JSONDecodeError:
                scenes = []

        for scene in scenes:
            dialogue_list = scene.get("dialogue", [])
            if isinstance(dialogue_list, str):
                try:
                    dialogue_list = json.loads(dialogue_list)
                except json.JSONDecodeError:
                    dialogue_list = []

            for d in dialogue_list:
                total_lines += 1
                char = d.get("character", "Unknown")
                line = d.get("line", "")
                emotion = d.get("emotion", "neutral")
                lines_plan.append({
                    "index": total_lines,
                    "character": char,
                    "line": line[:100],  # Truncate for readability
                    "emotion": emotion,
                    "needs_voice": True,
                    "suggested_pacing": _suggest_pacing(emotion),
                })
                emotional_arc.append({
                    "index": total_lines,
                    "character": char,
                    "emotion": emotion,
                })

    # Use Ollama to analyze overall emotional arc and pacing
    analysis = _analyze_script_emotions(acts, title, project_id)

    plan = {
        "title": title,
        "total_dialogue_lines": total_lines,
        "voice_lines": lines_plan,
        "emotional_arc": emotional_arc,
        "analysis": analysis,
        "tts_preparation": {
            "total_audio_clips_needed": total_lines,
            "estimated_duration_minutes": round(total_lines * 0.1, 1),
            "format": "aiff (macOS say default) or WAV for production",
            "sample_rate": "44100 Hz recommended",
        },
    }

    # Save to generation_logs
    log_generation({
        "project_id": project_id,
        "agent_type": "voice_actor/save_script",
        "model": DEFAULT_MODEL,
        "prompt": f"Save voice-over plan for script: {title}",
        "response": json.dumps(plan, ensure_ascii=False)[:5000],
        "tokens_in": 0,
        "tokens_out": 0,
        "duration_ms": 0,
    })

    return plan


def _suggest_pacing(emotion: str) -> str:
    """Suggest dialogue pacing based on emotion."""
    emotion_lower = emotion.lower()
    if "angry" in emotion_lower or "furious" in emotion_lower or "shout" in emotion_lower:
        return "fast, clipped, intense"
    if "sad" in emotion_lower or "melancholy" in emotion_lower or "depressed" in emotion_lower:
        return "slow, measured, with pauses"
    if "happy" in emotion_lower or "joyful" in emotion_lower or "excited" in emotion_lower:
        return "brisk, bright, energetic"
    if "fear" in emotion_lower or "scared" in emotion_lower or "whisper" in emotion_lower:
        return "hesitant, soft, uneven"
    if "calm" in emotion_lower or "neutral" in emotion_lower:
        return "even, relaxed, steady"
    if "surprise" in emotion_lower or "shock" in emotion_lower:
        return "start-stop, breathy, quick"
    return "moderate, natural"


def _analyze_script_emotions(acts: list, title: str, project_id: int) -> dict:
    """Use Ollama to analyze the emotional arc of the script."""
    acts_summary = []
    for act in acts:
        scenes = act.get("scenes", [])
        if isinstance(scenes, str):
            try:
                scenes = json.loads(scenes)
            except json.JSONDecodeError:
                scenes = []
        act_info = {
            "number": act.get("number", 0),
            "title": act.get("title", ""),
            "scene_count": len(scenes),
        }
        acts_summary.append(act_info)

    prompt = f"""Analyze the voice-over and emotional arc needs for this script:

Title: {title}
Acts: {json.dumps(acts_summary, ensure_ascii=False)}

Provide:
1. Overall emotional arc (how emotion progresses through the story)
2. Voice acting difficulty level (easy/medium/hard)
3. Key emotional beats that need special voice attention
4. Suggested pacing for the overall performance
5. Any characters that need distinct vocal qualities

Output as JSON (no markdown):
{{
    "overall_emotional_arc": "...",
    "voice_difficulty": "easy|medium|hard",
    "key_emotional_beats": ["beat1", "beat2"],
    "overall_suggested_pacing": "...",
    "character_vocal_notes": "character-specific voice direction",
    "recommendations": ["recommendation1"]
}}"""

    result = generate_json(
        prompt=prompt,
        system=VOICE_ACTOR_SYSTEM,
        model=DEFAULT_MODEL,
        temperature=0.6,
        max_tokens=2048,
        project_id=project_id,
        agent_type="voice_actor",
    )
    return result


def list_lines(
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Query character data from DB and produce a voice schedule."""
    try:
        characters = list_characters(project_id)
    except Exception as e:
        return {"error": f"Failed to list characters: {e}"}

    if not characters:
        return {
            "voice_schedule": [],
            "total_characters": 0,
            "note": "No characters found for this project. Create characters first.",
        }

    schedule = []
    for char in characters:
        char_dict = char
        if hasattr(char, "__dict__"):
            char_dict = char.__dict__
        elif hasattr(char, "_asdict"):
            char_dict = char._asdict()
        elif not isinstance(char, dict):
            # Try converting from ORM-like object
            try:
                char_dict = dict(char)
            except (TypeError, ValueError):
                char_dict = {"name": str(char)}

        name = char_dict.get("name", char_dict.get("character_name", "Unknown"))
        voice_profile = char_dict.get("voice_profile", char_dict.get("voice", "neutral"))
        personality = char_dict.get("personality", char_dict.get("description", ""))

        schedule.append({
            "character_name": name,
            "voice_profile": voice_profile,
            "personality": personality[:200] if personality else "",
            "priority": "high",
            "suggested_voice_type": _suggest_voice_type(personality, voice_profile),
            "notes": "",
        })

    voice_schedule = {
        "voice_schedule": schedule,
        "total_characters": len(schedule),
        "project_id": project_id,
    }

    # Save to generation_logs
    log_generation({
        "project_id": project_id,
        "agent_type": "voice_actor/list_lines",
        "model": "",
        "prompt": f"List voice schedule for project {project_id}",
        "response": json.dumps(voice_schedule, ensure_ascii=False)[:5000],
        "tokens_in": 0,
        "tokens_out": 0,
        "duration_ms": 0,
    })

    return voice_schedule


def _suggest_voice_type(personality: str, profile: str) -> str:
    """Suggest a voice type based on character personality and profile."""
    text = (personality + " " + profile).lower()

    if any(w in text for w in ["wise", "old", "elder", "ancient", "mentor"]):
        return "warm, resonant, slightly slow"
    if any(w in text for w in ["warrior", "strong", "brave", "heroic", "fierce"]):
        return "deep, firm, commanding"
    if any(w in text for w in ["young", "child", "innocent", "playful", "cheerful"]):
        return "bright, high-pitched, energetic"
    if any(w in text for w in ["villain", "evil", "dark", "sinister", "cruel"]):
        return "low, gravelly, menacing"
    if any(w in text for w in ["mysterious", "quiet", "subtle", "stealth", "calm"]):
        return "soft, smooth, measured"
    if any(w in text for w in ["elegant", "refined", "noble", "royal", "sophisticated"]):
        return "clear, articulate, poised"
    return "natural, moderate, versatile"


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Voice Actor Agent."""
    try:
        if action == "generate_dialogue":
            character_name = input_data.get("character_name", input_data.get("character", "Unknown"))
            voice_profile = input_data.get("voice_profile", input_data.get("voice", "neutral"))
            dialogue_text = input_data.get("dialogue_text", input_data.get("dialogue", ""))
            emotion = input_data.get("emotion", "neutral")
            result = generate_dialogue(character_name, voice_profile, dialogue_text, emotion, project_id, task_id)
            return {"result": result}

        elif action == "save_script":
            script_data = input_data.get("script_data", input_data.get("script", {}))
            result = save_script(script_data, project_id, task_id)
            return {"result": result}

        elif action == "list_lines":
            result = list_lines(project_id, task_id)
            return {"result": result}

        else:
            return {"error": f"Unknown action: {action}"}
    except Exception as e:
        return {"error": str(e), "status": "error", "message": str(e)}
