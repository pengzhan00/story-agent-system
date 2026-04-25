"""Reviewer Agent — business logic.
Quality assurance for generated content: scripts, characters, scenes, and projects."""
import json
from core.ollama_client import generate, generate_json, DEFAULT_MODEL, DETAIL_MODEL
from core.database import (
    get_script, get_project, list_scripts, list_characters,
    list_scene_assets, list_music, add_prompt_log, log_generation,
    get_character, get_scene_asset,
)

REVIEWER_SYSTEM = """You are a Quality Assurance Reviewer (质量审核员) for animation/story production.
You review creative content with professional standards for:
- Plot structure and coherence (no plot holes, logical progression)
- Character consistency (personality, voice, development arc)
- Pacing and dramatic tension
- Dialogue quality (natural, character-appropriate, engaging)
- Design completeness for visual and audio elements
Be thorough, constructive, and specific in your feedback.
Output in Chinese (中文) or English as appropriate."""


def review_script(
    script_id: int,
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Fetch a script from DB and send it to Ollama for quality review.
    Checks: plot holes, character consistency, pacing, dialogue quality.

    Returns structured review with scores and recommendations."""
    if not script_id:
        return {"error": "No script_id provided"}

    try:
        script = get_script(script_id)
    except Exception as e:
        return {"error": f"Failed to fetch script: {e}"}

    if not script:
        return {"error": f"Script with id {script_id} not found"}

    # Extract script data from the ORM/dict object
    if hasattr(script, "__dict__"):
        script_dict = script.__dict__
    elif hasattr(script, "_asdict"):
        script_dict = script._asdict()
    elif isinstance(script, dict):
        script_dict = script
    else:
        try:
            script_dict = dict(script)
        except (TypeError, ValueError):
            script_dict = {"id": script_id, "title": str(script)}

    title = script_dict.get("title", "Untitled")
    synopsis = script_dict.get("synopsis", "")
    acts_raw = script_dict.get("acts", "[]")
    if isinstance(acts_raw, str):
        try:
            acts = json.loads(acts_raw)
        except json.JSONDecodeError:
            acts = []
    else:
        acts = acts_raw

    # Build summary for review
    act_summaries = []
    total_scenes = 0
    for act in acts:
        scenes = act.get("scenes", [])
        if isinstance(scenes, str):
            try:
                scenes = json.loads(scenes)
            except json.JSONDecodeError:
                scenes = []
        act_summaries.append({
            "number": act.get("number", 0),
            "title": act.get("title", ""),
            "scene_count": len(scenes),
            "locations": [s.get("location", "") for s in scenes],
        })
        total_scenes += len(scenes)

    prompt = f"""Review this script for quality assurance:

Title: {title}
Synopsis: {synopsis[:500] if synopsis else "N/A"}
Total Acts: {len(act_summaries)}
Total Scenes: {total_scenes}
Act Overview: {json.dumps(act_summaries, ensure_ascii=False)}

Evaluate and score (0-100) each category:
1. Plot Coherence & Plot Holes — Does the story make logical sense? Any contradictions?
2. Character Consistency — Do characters behave according to their established personalities?
3. Pacing — Is the dramatic tension well distributed? Any rushed or dragging parts?
4. Dialogue Quality — Is dialogue natural, character-appropriate, and engaging?
5. Structure — Is the three-act (or multi-act) structure effective?

Also provide:
- Major issues (critical problems)
- Minor issues (suggestions)
- Overall verdict (pass / conditional-pass / fail)

Output as JSON (no markdown):
{{
    "script_id": {script_id},
    "title": "{title}",
    "scores": {{
        "plot_coherence": 0,
        "character_consistency": 0,
        "pacing": 0,
        "dialogue_quality": 0,
        "structure": 0
    }},
    "overall_score": 0,
    "major_issues": ["issue1"],
    "minor_issues": ["issue2"],
    "plot_holes": ["plot_hole_description"],
    "character_notes": ["character_note"],
    "pacing_notes": "overall pacing assessment",
    "dialogue_notes": "dialogue quality assessment",
    "recommendations": ["recommendation1"],
    "verdict": "pass|conditional-pass|fail",
    "summary": "brief review summary"
}}"""

    result = generate_json(
        prompt=prompt,
        system=REVIEWER_SYSTEM,
        model=DETAIL_MODEL,
        temperature=0.4,
        max_tokens=4096,
        project_id=project_id,
        agent_type="reviewer",
    )

    result["script_id"] = script_id
    result["title"] = title

    # Auto-save review as agent_logs
    _save_review_log("review_script", result, project_id, task_id)

    return result


def review_character(
    character_id: int,
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Fetch a character from DB and check design quality and completeness."""
    if not character_id:
        # If no specific character_id, review all characters for project
        return _review_characters_by_project(project_id, task_id)

    try:
        char = get_character(character_id)
    except Exception as e:
        return {"error": f"Failed to fetch character: {e}"}

    if not char:
        return {"error": f"Character with id {character_id} not found"}

    if hasattr(char, "__dict__"):
        char_dict = char.__dict__
    elif hasattr(char, "_asdict"):
        char_dict = char._asdict()
    elif isinstance(char, dict):
        char_dict = char
    else:
        try:
            char_dict = dict(char)
        except (TypeError, ValueError):
            char_dict = {"id": character_id, "name": str(char)}

    name = char_dict.get("name", char_dict.get("character_name", "Unknown"))
    personality = char_dict.get("personality", "")
    appearance = char_dict.get("appearance", "")
    background = char_dict.get("background", "")
    voice_profile = char_dict.get("voice_profile", char_dict.get("voice", ""))
    traits = char_dict.get("traits", [])

    prompt = f"""Review this character for design quality and completeness:

Character Name: {name}
Personality: {personality[:300] if personality else "Not defined"}
Appearance: {appearance[:300] if appearance else "Not defined"}
Background: {background[:500] if background else "Not defined"}
Voice Profile: {voice_profile if voice_profile else "Not defined"}
Traits: {json.dumps(traits[:10] if isinstance(traits, list) else [], ensure_ascii=False)}

Evaluate:
1. Completeness — Are all required fields filled? (personality, appearance, background, voice)
2. Design Quality — Is the character well-thought-out and compelling?
3. Consistency — Are traits and background coherent?
4. Visual Potential — Is the appearance description vivid enough for visual design?

Output as JSON (no markdown):
{{
    "character_id": {character_id},
    "name": "{name}",
    "completeness": {{
        "personality": "defined|missing|partial",
        "appearance": "defined|missing|partial",
        "background": "defined|missing|partial",
        "voice_profile": "defined|missing|partial",
        "overall_completeness_pct": 0
    }},
    "design_score": 0,
    "strengths": ["strength1"],
    "weaknesses": ["weakness1"],
    "recommendations": ["recommendation1"],
    "visual_design_readiness": "ready|needs_work|insufficient",
    "summary": "brief review summary"
}}"""

    result = generate_json(
        prompt=prompt,
        system=REVIEWER_SYSTEM,
        model=DEFAULT_MODEL,
        temperature=0.4,
        max_tokens=2048,
        project_id=project_id,
        agent_type="reviewer",
    )

    result["character_id"] = character_id
    result["name"] = name

    _save_review_log("review_character", result, project_id, task_id)

    return result


def _review_characters_by_project(project_id: int, task_id: int) -> dict:
    """Review all characters in a project."""
    try:
        characters = list_characters(project_id)
    except Exception as e:
        return {"error": f"Failed to list characters: {e}"}

    if not characters:
        return {
            "total_characters": 0,
            "note": "No characters found for this project.",
            "verdict": "no_characters",
        }

    all_reviews = []
    for char in characters:
        cid = char.id if hasattr(char, "id") else char.get("id", 0)
        review = review_character(cid, project_id, task_id)
        all_reviews.append(review)

    summary_prompt = f"""Summarize these character reviews into a project-level assessment:

{json.dumps(all_reviews, ensure_ascii=False)[:3000]}

Provide:
1. Overall character design quality for the project
2. Which characters need the most work
3. Missing elements across all characters

Output as JSON:
{{
    "project_character_quality": "excellent|good|needs_work|poor",
    "strongest_character": "...",
    "weakest_character": "...",
    "common_issues": ["issue1"],
    "project_recommendations": ["rec1"]
}}"""

    summary = generate_json(
        prompt=summary_prompt,
        system=REVIEWER_SYSTEM,
        model=DEFAULT_MODEL,
        temperature=0.4,
        max_tokens=2048,
        project_id=project_id,
        agent_type="reviewer",
    )

    result = {
        "total_characters": len(all_reviews),
        "project_id": project_id,
        "individual_reviews": all_reviews,
        "project_summary": summary,
    }

    _save_review_log("review_characters_batch", result, project_id, task_id)
    return result


def review_scene(
    scene_id: int,
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Fetch a scene from DB and check completeness."""
    if not scene_id:
        return {"error": "No scene_id provided"}

    try:
        scene = get_scene_asset(scene_id)
    except Exception as e:
        return {"error": f"Failed to fetch scene: {e}"}

    if not scene:
        return {"error": f"Scene with id {scene_id} not found"}

    if hasattr(scene, "__dict__"):
        scene_dict = scene.__dict__
    elif hasattr(scene, "_asdict"):
        scene_dict = scene._asdict()
    elif isinstance(scene, dict):
        scene_dict = scene
    else:
        try:
            scene_dict = dict(scene)
        except (TypeError, ValueError):
            scene_dict = {"id": scene_id}

    location = scene_dict.get("location", scene_dict.get("scene_name", ""))
    mood = scene_dict.get("mood", "")
    weather = scene_dict.get("weather", "")
    time_of_day = scene_dict.get("time_of_day", "")
    characters = scene_dict.get("characters", [])
    narration = scene_dict.get("narration", "")
    dialogue = scene_dict.get("dialogue", [])

    # Check completeness
    fields = {
        "location": bool(location),
        "mood": bool(mood),
        "weather": bool(weather),
        "time_of_day": bool(time_of_day),
        "characters": bool(characters),
        "narration": bool(narration),
    }
    filled = sum(1 for v in fields.values() if v)
    total = len(fields)
    completeness_pct = int((filled / total) * 100)

    dialogue_count = 0
    if isinstance(dialogue, list):
        dialogue_count = len(dialogue)
    elif isinstance(dialogue, str):
        try:
            dialogue_count = len(json.loads(dialogue))
        except json.JSONDecodeError:
            dialogue_count = 1 if dialogue else 0

    prompt = f"""Review this scene for completeness and quality:

Location: {location}
Mood: {mood}
Weather: {weather}
Time of Day: {time_of_day}
Characters Present: {json.dumps(characters if isinstance(characters, list) else [str(characters)], ensure_ascii=False)}
Narration: {narration[:300] if narration else "Not defined"}
Dialogue Lines: {dialogue_count}
Completeness: {completeness_pct}%

Evaluate and provide:
1. What's missing (empty fields)
2. Scene quality
3. Suggestions for improvement

Output as JSON (no markdown):
{{
    "scene_id": {scene_id},
    "location": "{location}",
    "completeness": {{
        "missing_fields": ["field1"],
        "completeness_pct": {completeness_pct},
        "has_dialogue": {"true" if dialogue_count > 0 else "false"},
        "dialogue_lines": {dialogue_count}
    }},
    "scene_quality_score": 0,
    "issues": ["issue1"],
    "recommendations": ["recommendation1"],
    "render_readiness": "ready|needs_scene_design|needs_review",
    "summary": "brief review summary"
}}"""

    result = generate_json(
        prompt=prompt,
        system=REVIEWER_SYSTEM,
        model=DEFAULT_MODEL,
        temperature=0.3,
        max_tokens=2048,
        project_id=project_id,
        agent_type="reviewer",
    )

    result["scene_id"] = scene_id
    result["completeness"]["completeness_pct"] = completeness_pct

    _save_review_log("review_scene", result, project_id, task_id)

    return result


def review_project(
    project_id: int = 0,
    task_id: int = 0,
) -> dict:
    """Comprehensive project review: fetches all assets
    (scripts, characters, scenes, music via list_* functions),
    generates a quality report with recommendations."""
    if not project_id:
        return {"error": "No project_id provided"}

    # Gather all project data
    project = None
    try:
        project = get_project(project_id)
    except Exception as e:
        pass

    try:
        scripts = list_scripts(project_id)
    except Exception as e:
        scripts = []
        project_data_error = f"Failed to list scripts: {e}"

    try:
        characters = list_characters(project_id)
    except Exception as e:
        characters = []

    try:
        scenes = list_scene_assets(project_id)
    except Exception as e:
        scenes = []

    try:
        music = list_music(project_id)
    except Exception as e:
        music = []

    project_name = ""
    if project:
        project_name = project.name if hasattr(project, "name") else project.get("name", str(project))

    # Build inventory
    inventory = {
        "project_id": project_id,
        "project_name": project_name or f"Project #{project_id}",
        "scripts_count": len(scripts),
        "characters_count": len(characters),
        "scenes_count": len(scenes),
        "music_themes_count": len(music),
        "has_project_record": project is not None,
    }

    # Run mini-reviews on each asset type
    script_reviews = []
    for s in scripts:
        sid = s.id if hasattr(s, "id") else s.get("id", 0)
        if sid:
            summary = _quick_script_check(s)
            script_reviews.append(summary)

    char_reviews = []
    for c in characters:
        cid = c.id if hasattr(c, "id") else c.get("id", 0)
        if cid:
            summary = _quick_character_check(c)
            char_reviews.append(summary)

    scene_summary = _quick_scene_stats(scenes)

    # Use Ollama for comprehensive analysis
    prompt = f"""Perform a comprehensive project quality review:

Project: {json.dumps(inventory, ensure_ascii=False)}

Script Overview: {json.dumps(script_reviews[:10], ensure_ascii=False)}
Character Overview: {json.dumps(char_reviews[:20], ensure_ascii=False)}
Scene Stats: {json.dumps(scene_summary, ensure_ascii=False)}
Music Themes: {inventory['music_themes_count']} tracks

Evaluate:
1. Overall production readiness (0-100)
2. Missing critical elements (scripts, characters, scenes)
3. Balance — is any area over/under-developed?
4. Critical path — what needs attention first?
5. Specific actionable recommendations

Output as JSON (no markdown):
{{
    "project_id": {project_id},
    "project_name": "{project_name}",
    "overall_readiness_score": 0,
    "readiness_level": "early_development|in_progress|nearly_ready|production_ready",
    "inventory": {json.dumps(inventory)},
    "asset_quality": {{
        "scripts": "excellent|good|needs_work|missing",
        "characters": "excellent|good|needs_work|missing",
        "scenes": "excellent|good|needs_work|missing",
        "music": "excellent|good|needs_work|missing"
    }},
    "critical_missing_elements": ["element1"],
    "strengths": ["strength1"],
    "weaknesses": ["weakness1"],
    "critical_path": ["step1"],
    "recommendations": ["recommendation1"],
    "next_action": "what to do next",
    "summary": "comprehensive review summary"
}}"""

    result = generate_json(
        prompt=prompt,
        system=REVIEWER_SYSTEM,
        model=DETAIL_MODEL,
        temperature=0.4,
        max_tokens=8192,
        project_id=project_id,
        agent_type="reviewer",
    )

    result["inventory"] = inventory
    result["project_id"] = project_id

    _save_review_log("review_project", result, project_id, task_id)

    return result


def _quick_script_check(script) -> dict:
    """Quick check on a script object."""
    if hasattr(script, "__dict__"):
        d = script.__dict__
    elif isinstance(script, dict):
        d = script
    else:
        d = {}

    acts_raw = d.get("acts", "[]")
    if isinstance(acts_raw, str):
        try:
            acts = json.loads(acts_raw)
        except json.JSONDecodeError:
            acts = []
    else:
        acts = acts_raw

    scene_count = sum(len(act.get("scenes", [])) for act in acts) if isinstance(acts, list) else 0

    return {
        "id": d.get("id", 0),
        "title": d.get("title", "Untitled"),
        "status": d.get("status", "unknown"),
        "acts": len(acts) if isinstance(acts, list) else 0,
        "scenes": scene_count,
    }


def _quick_character_check(char) -> dict:
    """Quick check on a character object."""
    if hasattr(char, "__dict__"):
        d = char.__dict__
    elif isinstance(char, dict):
        d = char
    else:
        d = {}

    name = d.get("name", d.get("character_name", "Unknown"))
    has_personality = bool(d.get("personality", ""))
    has_appearance = bool(d.get("appearance", ""))
    has_background = bool(d.get("background", ""))
    has_voice = bool(d.get("voice_profile", d.get("voice", "")))

    filled = sum([has_personality, has_appearance, has_background, has_voice])
    return {
        "id": d.get("id", 0),
        "name": name,
        "completeness_pct": int((filled / 4) * 100),
        "missing": [k for k, v in
                    [("personality", has_personality), ("appearance", has_appearance),
                     ("background", has_background), ("voice", has_voice)]
                    if not v],
    }


def _quick_scene_stats(scenes: list) -> dict:
    """Quick stats on scene list."""
    total = len(scenes)
    if total == 0:
        return {"total": 0, "avg_completeness": 0}

    completeness_scores = []
    locations = set()
    for scene in scenes:
        if hasattr(scene, "__dict__"):
            d = scene.__dict__
        elif isinstance(scene, dict):
            d = scene
        else:
            continue

        loc = d.get("location", d.get("scene_name", ""))
        if loc:
            locations.add(loc)

        fields = [
            bool(d.get("location", "")),
            bool(d.get("mood", "")),
            bool(d.get("weather", "")),
            bool(d.get("time_of_day", "")),
            bool(d.get("narration", "")),
        ]
        completeness_scores.append(int((sum(fields) / len(fields)) * 100))

    return {
        "total": total,
        "unique_locations": len(locations),
        "avg_completeness": int(sum(completeness_scores) / len(completeness_scores)) if completeness_scores else 0,
    }


def _save_review_log(action: str, review: dict, project_id: int, task_id: int):
    """Save review to agent_logs for auditing."""
    try:
        log_generation({
            "project_id": project_id,
            "agent_type": f"reviewer/{action}",
            "model": DETAIL_MODEL,
            "prompt": f"Review: {action}",
            "response": json.dumps(review, ensure_ascii=False)[:5000],
            "tokens_in": 0,
            "tokens_out": 0,
            "duration_ms": 0,
        })
    except Exception:
        pass  # Non-critical: don't fail the review if logging fails


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Reviewer Agent."""
    try:
        if action == "review_script":
            script_id = input_data.get("script_id", 0)
            result = review_script(script_id, project_id, task_id)
            return {"result": result}

        elif action == "review_character":
            character_id = input_data.get("character_id", input_data.get("character_id", 0))
            result = review_character(character_id, project_id, task_id)
            return {"result": result}

        elif action == "review_scene":
            scene_id = input_data.get("scene_id", 0)
            result = review_scene(scene_id, project_id, task_id)
            return {"result": result}

        elif action == "review_project":
            result = review_project(project_id, task_id)
            return {"result": result}

        else:
            return {"error": f"Unknown action: {action}"}
    except Exception as e:
        return {"error": str(e), "status": "error", "message": str(e)}
