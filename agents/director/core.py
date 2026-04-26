"""Director Agent — business logic (task scheduler and orchestrator)."""
import json
from core.ollama_client import generate, generate_json, DEFAULT_MODEL, CREATIVE_MODEL, DETAIL_MODEL
from core.database import create_script, create_character, create_scene_asset, create_music, create_sfx
from core.database import list_projects, get_project, list_characters, list_scripts, list_music


DIRECTOR_SYSTEM = """You are the Director (导演) of a story production system.
Your job:
1. Understand the user's creative request
2. Break it down into a production plan
3. Decide which specialist agents to involve
4. Return structured JSON plans

Available specialist agents:
- screenwriter (编剧): generates storylines, scripts, dialogue, acts
- character_designer (角色设计): creates character profiles, appearances, personalities
- scene_designer (场景设计): designs scene environments, lighting, atmosphere
- composer (作曲): creates music themes, BGM moods
- sound_designer (音效设计): plans sound effects and ambient audio

Always respond with a structured plan in JSON format."""


def analyze_request(request: str, project_id: int = 0, model: str = DEFAULT_MODEL) -> dict:
    """Analyze a user request and return a production plan."""
    prompt = f"""Analyze this creative request and produce a production plan as JSON:

User request: "{request}"

Output format (JSON, no markdown):
{{
    "project_name": "suggested project name",
    "genre": "story genre",
    "summary": "brief understanding of the request",
    "required_agents": ["list of agent types needed"],
    "tasks": [
        {{
            "agent": "agent_type",
            "priority": 1-5,
            "instruction": "what this agent needs to do"
        }}
    ],
    "estimated_acts": "number of acts/chapters",
    "tone": "overall tone of the story"
}}"""

    return generate_json(
        prompt=prompt,
        system=DIRECTOR_SYSTEM,
        model=model,
        temperature=0.3,
        project_id=project_id,
        agent_type="director",
    )


def create_production_plan(request: str, project_name: str = "") -> dict:
    """
    Full end-to-end plan: creates project, analyzes request, returns plan.
    """
    analysis = analyze_request(request)
    return analysis


# Prompt to generate Director's summarization
DIRECTOR_SUMMARY_SYSTEM = """You are the Director (导演) of a story production pipeline.
Synthesize outputs from multiple specialist agents into a coherent project summary."""


def summarize_project(project_id: int) -> str:
    """Generate a human-readable summary of an entire project."""
    proj = get_project(project_id)
    if not proj:
        return "Project not found."

    scripts = list_scripts(project_id)
    chars = list_characters(project_id)
    music = list_music(project_id)

    summary_parts = [
        f"# {proj.name}",
        f"**类型**: {proj.genre}  |  **状态**: {proj.status}",
        f"**描述**: {proj.description}",
        "",
        "## 角色 ({})".format(len(chars)),
    ]
    for c in chars:
        summary_parts.append(f"- **{c.name}** ({c.role}): {c.appearance[:80]}...")

    summary_parts.append(f"\n## 剧本 ({len(scripts)}版)")
    for s in scripts:
        lens = s.total_scenes
        summary_parts.append(f"- **{s.title}**: {lens}场戏, {s.word_count}字")
        summary_parts.append(f"  梗概: {s.synopsis[:120]}...")

    summary_parts.append(f"\n## 配乐 ({len(music)}首)")
    for m in music:
        summary_parts.append(f"- **{m.name}** ({m.type}): {m.mood} - {m.description[:80]}...")

    return "\n".join(summary_parts)


def decompose_task(project_id: int, request: str) -> dict:
    """Decompose a request into sub-tasks for other agents."""
    plan = analyze_request(request, project_id)
    return plan


def run_action(action: str, input_data: dict, project_id: int = 0, task_id: int = 0) -> dict:
    """Dispatch actions for the Director Agent."""
    if action == "analyze":
        request = input_data.get("request", "")
        result = analyze_request(request, project_id)
        return {"result": result}
    elif action == "plan":
        request = input_data.get("request", "")
        project_name = input_data.get("project_name", "")
        result = create_production_plan(request, project_name)
        return {"result": result}
    elif action == "summarize":
        pid = input_data.get("project_id", project_id)
        result = summarize_project(pid)
        return {"result": result}
    elif action == "decompose":
        request = input_data.get("request", "")
        result = decompose_task(project_id, request)
        return {"result": result}
    else:
        return {"error": f"Unknown action: {action}"}
