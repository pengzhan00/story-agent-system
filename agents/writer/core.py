"""Writer (Screenwriter) Agent — business logic."""
import json
from core.ollama_client import generate, generate_json, DEFAULT_MODEL, CREATIVE_MODEL
from core.database import create_script, update_script, get_script


SCREENWRITER_SYSTEM = """You are a professional screenwriter (编剧) specializing in 短剧 (short drama series).
You write fast-paced, high-impact Chinese story scripts for short-form video dramas.
Key traits: fast pacing, strong plot twists, dense dialogue with high information density, quick scene transitions.
Write in Chinese (中文). Use proper script structure with acts, scenes, and shot-level breakdowns.

CRITICAL RULE: You MUST generate the EXACT number of scenes per act and shots per scene as specified.
Each scene MUST have a "shots" array with the exact number specified. Never omit the shots array."""


def generate_storyline(
    premise: str,
    genre: str = "玄幻",
    tone: str = "热血",
    acts: int = 5,
    scenes_per_act: int = 4,
    shots_per_scene: int = 3,
    project_id: int = 0,
    model: str = DEFAULT_MODEL,
) -> dict:
    """
    Generate a full storyline with acts and scene breakdown.
    Each scene includes shot-level breakdown for short drama production.
    Returns the script data ready to save to DB.
    """
    prompt = f"""You are generating a {genre} short drama (短剧) outline.

CONSTRAINTS (MUST follow exactly):
- {acts} acts total (acts 1 through {acts})
- EXACTLY {scenes_per_act} scenes per act (scene 1, 2, 3, {scenes_per_act})
- EXACTLY {shots_per_scene} shots per scene (shot 1, 2, {shots_per_scene})
- Each scene MUST have a "shots" array with {shots_per_scene} items
- Every shot MUST have "dialogue" array with 2-4 lines

Premise: {premise}
Tone: {tone}

Short drama style: fast pacing, strong plot twists, dense dialogue, quick scene transitions.

Output JSON format (fill in the values, KEEP the exact array sizes):
{{{{
    "title": "story title",
    "synopsis": "200-word synopsis",
    "acts": [
        {{{{
            "number": 1,
            "title": "...",
            "summary": "...",
            "scenes": [
                {{{{"number": 1, "location": "...", "time_of_day": "白天/傍晚/夜晚", "weather": "晴/雨/雪", "characters": ["角色A"], "mood": "...", "narration": "...", "bgm_mood": "...",
                    "shots": [
                        {{{{"shot_number": 1, "camera_angle": "中景", "characters_present": ["角色A"], "dialogue": [{{{{"character": "角色A", "line": "...", "emotion": "..."}}}}], "narration": "...", "mood": "..."}}}},
                        {{{{"shot_number": 2, "camera_angle": "特写", "characters_present": ["角色A"], "dialogue": [{{{{"character": "角色A", "line": "...", "emotion": "..."}}}}], "narration": "...", "mood": "..."}}}},
                        {{{{"shot_number": 3, "camera_angle": "远景", "characters_present": ["角色A"], "dialogue": [{{{{"character": "角色A", "line": "...", "emotion": "..."}}}}], "narration": "...", "mood": "..."}}}}
                    ]
                }}}},
                {{{{"number": 2, "location": "...", "time_of_day": "...", "weather": "...", "characters": ["角色A", "角色B"], "mood": "...", "narration": "...", "bgm_mood": "...",
                    "shots": [
                        {{{{"shot_number": 1, "camera_angle": "...", "characters_present": ["..."], "dialogue": [{{{{"character": "...", "line": "...", "emotion": "..."}}}}], "narration": "...", "mood": "..."}}}},
                        {{{{"shot_number": 2, "camera_angle": "...", "characters_present": ["..."], "dialogue": [{{{{"character": "...", "line": "...", "emotion": "..."}}}}], "narration": "...", "mood": "..."}}}},
                        {{{{"shot_number": 3, "camera_angle": "...", "characters_present": ["..."], "dialogue": [{{{{"character": "...", "line": "...", "emotion": "..."}}}}], "narration": "...", "mood": "..."}}}}
                    ]
                }}}},
                {{{{"number": 3, "location": "...", "time_of_day": "...", "weather": "...", "characters": [...], "mood": "...", "narration": "...", "bgm_mood": "...",
                    "shots": [
                        {{{{"shot_number": 1, ...}}}},
                        {{{{"shot_number": 2, ...}}}},
                        {{{{"shot_number": 3, ...}}}}
                    ]
                }}}},
                {{{{"number": 4, "location": "...", "time_of_day": "...", "weather": "...", "characters": [...], "mood": "...", "narration": "...", "bgm_mood": "...",
                    "shots": [
                        {{{{"shot_number": 1, ...}}}},
                        {{{{"shot_number": 2, ...}}}},
                        {{{{"shot_number": 3, ...}}}}
                    ]
                }}}}
            ]
        }}}},
        {{{{"number": 2, "title": "...", "summary": "...", "scenes": [same structure: {scenes_per_act} scenes with {shots_per_scene} shots each]}}}},
        {{{{"number": 3, "title": "...", "summary": "...", "scenes": [same: {scenes_per_act} scenes, {shots_per_scene} shots each]}}}},
        {{{{"number": 4, "title": "...", "summary": "...", "scenes": [same: {scenes_per_act} scenes, {shots_per_scene} shots each]}}}},
        {{{{"number": 5, "title": "...", "summary": "...", "scenes": [same: {scenes_per_act} scenes, {shots_per_scene} shots each]}}}}
    ]
}}}}
"""

    result = generate_json(
        prompt=prompt,
        system=SCREENWRITER_SYSTEM,
        model=model,
        temperature=0.3,
        max_tokens=16384,
        project_id=project_id,
        agent_type="screenwriter",
    )

    # Calculate word count
    word_count = len(str(result))

    # Save to DB
    script_data = {
        "project_id": project_id,
        "title": result.get("title", "未命名故事"),
        "synopsis": result.get("synopsis", ""),
        "acts": json.dumps(result.get("acts", []), ensure_ascii=False),
        "total_scenes": sum(
            len(act.get("scenes", [])) for act in result.get("acts", [])
        ),
        "word_count": word_count,
        "status": "draft",
    }
    script_id = create_script(script_data)
    result["id"] = script_id
    return result


def expand_scene(
    script_id: int,
    act_number: int,
    scene_number: int,
    project_id: int = 0,
    model: str = CREATIVE_MODEL,
) -> dict:
    """
    Take an existing scene outline and expand it into full script
    with detailed dialogue, actions, and camera directions.
    """
    script = get_script(script_id)
    if not script:
        return {"error": "Script not found"}

    acts = script.get_acts()
    target_act = None
    target_scene = None
    for act in acts:
        if act["number"] == act_number:
            target_act = act
            for sc in act.get("scenes", []):
                if sc["number"] == scene_number:
                    target_scene = sc
                    break
            break

    if not target_scene:
        return {"error": f"Act {act_number} Scene {scene_number} not found"}

    prompt = f"""Expand this scene into full script format:

Story: {script.title}
Act {act_number}: {target_act['title']}
Scene {scene_number}: {target_scene.get('location', '')} - {target_scene.get('mood', '')}

Characters: {', '.join(target_scene.get('characters', []))}
Narration base: {target_scene.get('narration', '')}

Write in Chinese. Include:
1. Full narration/action descriptions
2. Detailed dialogue between characters (at least 8-15 exchanges)
3. Emotions and movements for each line
4. Camera direction notes
5. Background/setting details

Output as JSON (no markdown):
{{
    "scene_number": {scene_number},
    "location": "...",
    "time_of_day": "...",
    "weather": "...",
    "characters_present": ["..."],
    "full_narration": "detailed scene description...",
    "dialogue": [
        {{"character": "...", "line": "...", "emotion": "...", "action": "..."}}
    ],
    "camera_notes": "camera and shot direction notes",
    "bgm_note": "specific music direction",
    "duration_estimate": "estimated screen time"
}}
"""

    result = generate_json(
        prompt=prompt,
        system=SCREENWRITER_SYSTEM,
        model=model,
        temperature=0.8,
        max_tokens=8192,
        project_id=project_id,
        agent_type="screenwriter",
    )

    # Update the scene in DB
    for act in acts:
        if act["number"] == act_number:
            for i, sc in enumerate(act.get("scenes", [])):
                if sc["number"] == scene_number:
                    act["scenes"][i]["expanded"] = result
                    break
            break

    update_script(script_id, {"acts": json.dumps(acts, ensure_ascii=False)})
    return result


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Writer (Screenwriter) Agent."""
    if action == "generate_storyline":
        premise = input_data.get("premise", "")
        genre = input_data.get("genre", "玄幻")
        tone = input_data.get("tone", "热血")
        acts = input_data.get("acts", 5)
        scenes_per_act = input_data.get("scenes_per_act", 4)
        shots_per_scene = input_data.get("shots_per_scene", 3)
        model = input_data.get("model", DEFAULT_MODEL)
        result = generate_storyline(premise, genre, tone, acts, scenes_per_act, shots_per_scene, project_id, model)
        return {"result": result}
    elif action == "expand_scene":
        script_id = input_data.get("script_id", 0)
        act_number = input_data.get("act_number", 1)
        scene_number = input_data.get("scene_number", 1)
        model = input_data.get("model", CREATIVE_MODEL)
        result = expand_scene(script_id, act_number, scene_number, project_id, model)
        return {"result": result}
    else:
        return {"error": f"Unknown action: {action}"}
